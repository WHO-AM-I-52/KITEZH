# ╔════════════════════════════════════════════════════════════════════════╗
# ║                          repository.py                                    ║
# ║  Параметризованный SQL слоя «Договоры».                                  ║
# ║  Контракт: функции получают conn; транзакцию ведёт вызывающий маршрут.  ║
# ╚══════════════════════════════════════════════════════════════════════╝

from datetime import datetime


CONTRACT_FIELDS = [
    'is_free', 'free_basis', 'free_basis_date', 'origin', 'customer_type',
    'customer_full_name', 'customer_short_name', 'customer_opf',
    'customer_inn', 'customer_ogrn', 'customer_kpp', 'customer_address',
    'customer_fact_address', 'customer_email', 'customer_bank_account',
    'customer_bank_name', 'customer_bank_bik', 'customer_bank_corr_account',
    'signatory_name', 'signatory_position', 'signatory_basis',
    'contact_name', 'contact_position', 'contact_phone', 'contact_email',
    'directory_org_id', 'directory_contact_id',
    'project_name', 'object_description', 'object_type_id', 'address',
    'district_id', 'cadastral_numbers', 'area_ha', 'executor_id',
    'payment_scheme', 'payment_days', 'payment_day_type', 'payment_event',
    'duration_days', 'duration_day_type', 'work_start_basis',
    'act_sign_method', 'incoming_docs_ok', 'incoming_docs_date',
    'vat_percent', 'reg_number', 'reg_date',
]

# Колонки, добавленные после первого выпуска раздела. Таблица contracts
# создаётся в migrations.py командой CREATE TABLE IF NOT EXISTS, поэтому
# в уже существующей базе недостающие колонки добавляются здесь.
_ADDED_COLUMNS = (
    ('customer_fact_address', 'TEXT'),
    ('customer_email', 'TEXT'),
    ('customer_bank_account', 'TEXT'),
    ('customer_bank_name', 'TEXT'),
    ('customer_bank_bik', 'TEXT'),
    ('customer_bank_corr_account', 'TEXT'),
)


def ensure_contract_columns(conn) -> bool:
    """Добавляет недостающие колонки в contracts; True, если что-то добавлено.

    Идемпотентна. commit делает вызывающий код.
    """
    existing = {
        row[1] for row in conn.execute("PRAGMA table_info(contracts)").fetchall()
    }
    added = False
    for name, sql_type in _ADDED_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE contracts ADD COLUMN {name} {sql_type}")
            added = True
    return added


def _now() -> str:
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')


def _inner_number_counter(conn, year: int, advance: bool) -> int:
    """Счётчик года в существующей таблице настроек; без commit."""
    key = f'inner_number_counter:{year}'
    conn.execute(
        "INSERT OR IGNORE INTO contract_settings (key, value) VALUES (?, '0')",
        (key,)
    )
    row = conn.execute(
        "SELECT value FROM contract_settings WHERE key=?", (key,)
    ).fetchone()
    current = int(row[0])
    prefix = f'ДГ-{year}-'
    rows = conn.execute(
        "SELECT inner_number FROM contracts WHERE inner_number LIKE ?",
        (prefix + '%',)
    ).fetchall()
    for row in rows:
        suffix = row[0][len(prefix):]
        if suffix and suffix.isascii() and suffix.isdigit():
            current = max(current, int(suffix))
    value = current + 1 if advance else current
    conn.execute(
        "UPDATE contract_settings SET value=? WHERE key=?", (str(value), key)
    )
    return value


def next_inner_number(conn) -> str:
    """ДГ-ГГГГ-NNNN; удалённые номера повторно не выдаются."""
    year = datetime.utcnow().year
    number = _inner_number_counter(conn, year, advance=True)
    return f'ДГ-{year}-{number:04d}'


def get_contract(conn, contract_id: int):
    return conn.execute(
        "SELECT * FROM contracts WHERE id=?", (contract_id,)
    ).fetchone()


def list_contracts(conn, view_all: bool, user_id: int, filters: dict):
    """Реестр договоров с фильтрами. Без view_all — только свои."""
    where = []
    params = []
    if not view_all:
        where.append("(c.executor_id=? OR c.created_by=?)")
        params += [user_id, user_id]
    if filters.get('stage'):
        where.append("c.stage=?")
        params.append(filters['stage'])
    if filters.get('state'):
        where.append("c.state=?")
        params.append(filters['state'])
    if filters.get('free'):
        where.append("c.is_free=?")
        params.append(1 if filters['free'] == 'free' else 0)
    if filters.get('executor'):
        where.append("c.executor_id=?")
        params.append(filters['executor'])
    if filters.get('year'):
        where.append("strftime('%Y', c.created_at)=?")
        params.append(filters['year'])
    if filters.get('q'):
        like = f"%{filters['q']}%"
        where.append(
            "(c.inner_number LIKE ? OR c.reg_number LIKE ?"
            " OR c.customer_full_name LIKE ? OR c.customer_short_name LIKE ?"
            " OR c.project_name LIKE ?)"
        )
        params += [like] * 5
    sql = """
        SELECT c.*, u.full_name AS executor_name,
               ot.name AS object_type_name,
               (SELECT COALESCE(SUM(amount_kop), 0) FROM contract_payments
                WHERE contract_id=c.id AND kind='income') AS paid_kop
        FROM contracts c
        JOIN users u ON u.id = c.executor_id
        LEFT JOIN contract_object_types ot ON ot.id = c.object_type_id
    """
    if where:
        sql += "WHERE " + " AND ".join(where) + " "
    sql += "ORDER BY c.id DESC"
    return conn.execute(sql, params).fetchall()


# ─── СОЗДАНИЕ И ОБНОВЛЕНИЕ ──────────────────────────────────────────────────

def insert_contract(conn, form, user_id: int) -> int:
    inner_number = next_inner_number(conn)
    cols = ['inner_number', 'stage', 'state', 'created_by', 'created_at',
            'updated_at', 'total_kop', 'executor_id']
    vals = [inner_number, 'draft', 'active', user_id, _now(), _now(), 0,
            form.get('executor_id') or user_id]
    for f in CONTRACT_FIELDS:
        v = form.get(f)
        if v in ('', None):
            v = None
        if f == 'origin' and v is None:
            v = 'new'
        if f == 'is_free':
            v = 1 if str(v) == '1' else 0
        if f == 'incoming_docs_ok':
            v = 1 if str(v) == '1' else 0
        cols.append(f)
        vals.append(v)
    placeholders = ', '.join('?' for _ in vals)
    cur = conn.execute(
        f"INSERT INTO contracts ({', '.join(cols)}) VALUES ({placeholders})",
        vals
    )
    return cur.lastrowid


def update_contract(conn, contract_id: int, form: dict):
    sets, vals = [], []
    for f in CONTRACT_FIELDS:
        if f not in form:
            continue
        v = form.get(f)
        if v in ('', None):
            v = None
        if f == 'origin' and v is None:
            v = 'new'
        if f == 'is_free':
            v = 1 if str(v) == '1' else 0
        if f == 'incoming_docs_ok':
            v = 1 if str(v) == '1' else 0
        sets.append(f"{f}=?")
        vals.append(v)
    sets.append("updated_at=?")
    vals.append(_now())
    vals.append(contract_id)
    conn.execute(
        f"UPDATE contracts SET {', '.join(sets)} WHERE id=?", vals
    )


def contract_delete_error(conn, contract_id: int):
    """Причина запрета удаления или None. Права проверяет маршрут."""
    row = conn.execute(
        "SELECT stage, state, reg_number, reg_date FROM contracts WHERE id=?",
        (contract_id,)
    ).fetchone()
    if row is None:
        return 'Договор не найден'
    if row['stage'] != 'draft' or row['state'] != 'active':
        return 'Удалить можно только действующий черновик'
    if row['reg_number'] or row['reg_date']:
        return 'Нельзя удалить зарегистрированный договор'
    checks = (
        ('contract_stage_log', 'У договора есть история переходов стадий'),
        ('contract_payments', 'У договора есть платежи'),
        ('contract_files', 'У договора есть прикреплённые файлы'),
        ('contract_reviews', 'У договора есть рассмотрения'),
        ('contract_activities', 'У договора есть мероприятия'),
        ('contract_tasks', 'У договора есть связанные задачи'),
    )
    for table, message in checks:
        if conn.execute(
            f"SELECT 1 FROM {table} WHERE contract_id=? LIMIT 1",
            (contract_id,)
        ).fetchone():
            return message
    return None


def delete_contract(conn, contract_id: int):
    """Удаляет допустимый черновик и услуги; возвращает причину отказа.

    Вызывающий код проверяет права, журналирует и делает commit/rollback.
    SAVEPOINT защищает от частичного удаления; UPDATE получает write-lock
    до повторной проверки условий, исключая гонку с другими писателями.
    """
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    conn.execute("SAVEPOINT contract_delete")
    try:
        conn.execute("UPDATE contracts SET id=id WHERE id=?", (contract_id,))
        error = contract_delete_error(conn, contract_id)
        if error:
            conn.execute("ROLLBACK TO SAVEPOINT contract_delete")
            conn.execute("RELEASE SAVEPOINT contract_delete")
            return error
        row = conn.execute(
            "SELECT inner_number FROM contracts WHERE id=?", (contract_id,)
        ).fetchone()
        parts = (row['inner_number'] or '').split('-')
        if (len(parts) == 3 and parts[0] == 'ДГ'
                and len(parts[1]) == 4 and parts[1].isascii()
                and parts[1].isdigit()):
            _inner_number_counter(conn, int(parts[1]), advance=False)
        conn.execute(
            "DELETE FROM contract_services WHERE contract_id=?", (contract_id,)
        )
        conn.execute("DELETE FROM contracts WHERE id=?", (contract_id,))
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT contract_delete")
        conn.execute("RELEASE SAVEPOINT contract_delete")
        raise
    conn.execute("RELEASE SAVEPOINT contract_delete")
    return None


# ─── УСЛУГИ ─────────────────────────────────────────────────────────────────

def get_services(conn, contract_id: int):
    return conn.execute(
        """SELECT * FROM contract_services WHERE contract_id=?
           ORDER BY sort_order, id""",
        (contract_id,)
    ).fetchall()


def add_service(conn, contract_id: int, tariff_item, amount_kop: int,
                qty=None, area_ha=None, manual_reason=None):
    from contracts import pricing
    edition_code = (tariff_item['edition_code']
                    if 'edition_code' in tariff_item.keys() else None)
    cur = conn.execute(
        """INSERT INTO contract_services
           (contract_id, tariff_item_id, edition_code, code, name, qty,
            area_ha, price_kop, amount_kop, manual_edit, manual_reason,
            sort_order)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (contract_id, tariff_item['id'], edition_code,
         tariff_item['code'], tariff_item['name'], qty, area_ha,
         tariff_item['price_kop'], amount_kop,
         1 if manual_reason else 0, manual_reason,
         _service_count(conn, contract_id))
    )
    recalc_total(conn, contract_id)
    return cur.lastrowid


def _service_count(conn, contract_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM contract_services WHERE contract_id=?",
        (contract_id,)
    ).fetchone()[0]


def delete_service(conn, contract_id: int, service_id: int):
    conn.execute(
        "DELETE FROM contract_services WHERE id=? AND contract_id=?",
        (service_id, contract_id)
    )
    recalc_total(conn, contract_id)


def recalc_total(conn, contract_id: int):
    from contracts import pricing
    rows = conn.execute(
        "SELECT code, amount_kop FROM contract_services WHERE contract_id=?",
        (contract_id,)
    ).fetchall()
    total = pricing.council_services_total(
        [dict(r) for r in rows]
    ) if rows else 0
    conn.execute(
        "UPDATE contracts SET total_kop=?, updated_at=? WHERE id=?",
        (total, _now(), contract_id)
    )


def get_tariff_items(conn, active_only=True):
    sql = """SELECT ti.*, ed.code AS edition_code
             FROM contract_tariff_items ti
             JOIN contract_tariff_editions ed ON ed.id = ti.edition_id"""
    if active_only:
        sql += " WHERE ti.is_active=1"
    sql += " ORDER BY ti.sort_order"
    return conn.execute(sql).fetchall()


def get_object_types(conn):
    return conn.execute(
        """SELECT t.*, p.name AS parent_name
           FROM contract_object_types t
           LEFT JOIN contract_object_types p ON p.id = t.parent_id
           WHERE t.is_active=1
           ORDER BY COALESCE(p.sort_order, t.sort_order), t.sort_order"""
    ).fetchall()


# ─── ФАЙЛЫ, ОПЛАТЫ, РАССМОТРЕНИЯ, ИСТОРИЯ ─────────────────────────────────

def get_files(conn, contract_id: int):
    return conn.execute(
        """SELECT f.*, u.full_name AS user_name FROM contract_files f
           LEFT JOIN users u ON u.id = f.user_id
           WHERE f.contract_id=? ORDER BY f.category, f.version DESC""",
        (contract_id,)
    ).fetchall()


def get_payments(conn, contract_id: int):
    return conn.execute(
        """SELECT p.*, u.full_name AS user_name FROM contract_payments p
           LEFT JOIN users u ON u.id = p.user_id
           WHERE p.contract_id=? ORDER BY p.pay_date, p.id""",
        (contract_id,)
    ).fetchall()


def get_reviews(conn, contract_id: int):
    return conn.execute(
        "SELECT * FROM contract_reviews WHERE contract_id=? ORDER BY id",
        (contract_id,)
    ).fetchall()


def get_stage_log(conn, contract_id: int):
    return conn.execute(
        """SELECT l.*, u.full_name AS user_name FROM contract_stage_log l
           LEFT JOIN users u ON u.id = l.user_id
           WHERE l.contract_id=? ORDER BY l.id""",
        (contract_id,)
    ).fetchall()


def get_activities(conn, contract_id: int):
    return conn.execute(
        "SELECT * FROM contract_activities WHERE contract_id=? ORDER BY id",
        (contract_id,)
    ).fetchall()


def add_payment(conn, contract_id: int, kind: str, amount_kop: int,
                 number=None, pay_date=None, note=None, file_id=None,
                 user_id=None):
    cur = conn.execute(
        """INSERT INTO contract_payments
           (contract_id, kind, number, pay_date, amount_kop, note,
            file_id, user_id, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (contract_id, kind, number, pay_date, amount_kop, note,
         file_id, user_id, _now())
    )
    return cur.lastrowid


def add_review(conn, contract_id: int, body: str, request_no=None,
               sent_date=None, accepted_date=None, decision_date=None,
               result=None, comment=None, file_id=None):
    cur = conn.execute(
        """INSERT INTO contract_reviews
           (contract_id, body, request_no, sent_date, accepted_date,
            decision_date, result, comment, file_id, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (contract_id, body, request_no, sent_date, accepted_date,
         decision_date, result, comment, file_id, _now())
    )
    return cur.lastrowid


def add_activity(conn, contract_id: int, name: str, assignee=None,
                 assignee_user_id=None, assigned_at=None, deadline=None,
                 status='new', result=None):
    cur = conn.execute(
        """INSERT INTO contract_activities
           (contract_id, name, assignee, assignee_user_id, assigned_at,
            deadline, status, result, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (contract_id, name, assignee, assignee_user_id, assigned_at,
         deadline, status, result, _now())
    )
    return cur.lastrowid


def list_executors(conn):
    """Исполнители для выбора в форме (пользователи с доступом)."""
    return conn.execute(
        "SELECT id, full_name FROM users WHERE is_active=1"
        " ORDER BY full_name"
    ).fetchall()


def list_districts(conn):
    return conn.execute(
        "SELECT id, name FROM districts ORDER BY name"
    ).fetchall()
