"""Общая бизнес-логика единого справочника адресатов.

Организации, контакты и связи человек–организация используются
телефонным справочником и журналом писем.

Контракт: все функции записи получают conn и НЕ делают commit/close.
Транзакцией владеет вызывающий маршрут или Excel apply.

Не объединяет людей по одному ФИО и организации по похожему названию:
при совпадениях вызывающий код получает кандидатов и явное решение
пользователя. Совпадение ИНН не создаёт вторую организацию молча.
"""

from datetime import datetime


class DirectoryError(ValueError):
    """Ошибка справочника: конфликт, отсутствующая или архивная запись."""


def _now() -> str:
    return datetime.utcnow().isoformat()


def _norm(value) -> str:
    return ' '.join((value or '').split()).casefold()


# ─── Чтение ───────────────────────────────────────────────────────────────────

def get_org(conn, org_id):
    if org_id is None:
        return None
    return conn.execute(
        'SELECT * FROM phonebook_orgs WHERE id = ?', (org_id,)
    ).fetchone()


def get_contact(conn, contact_id):
    if contact_id is None:
        return None
    return conn.execute(
        'SELECT * FROM phonebook WHERE id = ?', (contact_id,)
    ).fetchone()


def get_employment(conn, employment_id):
    if employment_id is None:
        return None
    return conn.execute(
        'SELECT * FROM phonebook_employment WHERE id = ?', (employment_id,)
    ).fetchone()


def find_org_candidates(conn, name='', inn=None):
    """Организации с совпадающим ИНН или нормализованным названием."""
    candidates = []
    if inn:
        for row in conn.execute(
            'SELECT * FROM phonebook_orgs WHERE inn = ?', (inn,)
        ).fetchall():
            if row not in candidates:
                candidates.append(row)
    if name:
        for row in conn.execute(
            'SELECT * FROM phonebook_orgs'
        ).fetchall():
            if _norm(row['name']) == _norm(name) and row not in candidates:
                candidates.append(row)
    return candidates


def find_person_candidates(conn, full_name):
    """Контакты с совпадающим ФИО (без объединения)."""
    if not full_name:
        return []
    rows = conn.execute(
        '''
        SELECT p.*, o.name AS org_name
        FROM phonebook p
        LEFT JOIN phonebook_orgs o ON o.id = p.org_id
        WHERE p.archived = 0
        '''
    ).fetchall()
    return [r for r in rows if _norm(r['full_name']) == _norm(full_name)]


def list_employments(conn, contact_id, include_archived=False):
    """Все места работы контакта."""
    sql = 'SELECT * FROM phonebook_employment WHERE contact_id = ?'
    if not include_archived:
        sql += ' AND archived = 0'
    return conn.execute(sql + ' ORDER BY id', (contact_id,)).fetchall()


def search_directory(conn, q, limit=20):
    """Поиск адресатов для формы письма.

    Возвращает организации, людей и места работы с контекстом.
    Личные телефоны и прочие лишние поля не возвращаются.
    """
    tokens = [t.casefold() for t in (q or '').split() if t]
    results = []

    def matches(*parts):
        hay = ' '.join(p for p in parts if p).casefold()
        return all(t in hay for t in tokens)

    for row in conn.execute(
        'SELECT id, name FROM phonebook_orgs WHERE archived = 0 ORDER BY name'
    ).fetchall():
        if matches(row['name']):
            results.append({
                'type': 'org',
                'org_id': row['id'],
                'org_name': row['name'],
                'display': row['name'],
            })

    for row in conn.execute(
        '''
        SELECT e.id AS employment_id, e.position,
               p.id AS contact_id, p.full_name, p.email, p.phone_work,
               o.id AS org_id, o.name AS org_name
        FROM phonebook_employment e
        JOIN phonebook p ON p.id = e.contact_id
        LEFT JOIN phonebook_orgs o ON o.id = e.org_id
        WHERE e.archived = 0 AND p.archived = 0
        ORDER BY p.full_name
        '''
    ).fetchall():
        if matches(row['full_name'], row['org_name'], row['position']):
            results.append({
                'type': 'employment',
                'employment_id': row['employment_id'],
                'contact_id': row['contact_id'],
                'full_name': row['full_name'],
                'org_id': row['org_id'],
                'org_name': row['org_name'] or '',
                'position': row['position'] or '',
                'email': row['email'] or '',
                'display': ' — '.join(x for x in (
                    row['full_name'],
                    row['org_name'] or 'без организации',
                    row['position'],
                ) if x),
            })

    for row in conn.execute(
        '''
        SELECT p.id AS contact_id, p.full_name, p.email, p.phone_work
        FROM phonebook p
        WHERE p.archived = 0
          AND NOT EXISTS (
              SELECT 1 FROM phonebook_employment e
              WHERE e.contact_id = p.id AND e.archived = 0
          )
        ORDER BY p.full_name
        '''
    ).fetchall():
        if matches(row['full_name']):
            results.append({
                'type': 'person',
                'contact_id': row['contact_id'],
                'full_name': row['full_name'],
                'org_id': None,
                'org_name': '',
                'position': '',
                'email': row['email'] or '',
                'display': row['full_name'],
            })

    return results[:limit]


# ─── Создание ────────────────────────────────────────────────────────────────

def create_org(conn, name, address='', inn=None, user_id=None):
    """Создаёт организацию либо возвращает существующую.

    Совпадение ИНН или точного названия не создаёт вторую организацию:
    возвращается status='exists' и карточка-кандидат.
    """
    name = ' '.join((name or '').split())
    inn = (inn or '').strip() or None
    if not name:
        raise DirectoryError('Для организации необходимо название')

    existing = find_org_candidates(conn, name, inn)
    if existing:
        row = existing[0]
        return {'status': 'exists', 'org': dict(row)}

    cur = conn.execute(
        'INSERT INTO phonebook_orgs (name, address, inn, created_by, created_at) '
        'VALUES (?, ?, ?, ?, ?)',
        (name, address or '', inn, user_id, _now()),
    )
    return {'status': 'created', 'org_id': cur.lastrowid}


def create_contact(conn, full_name, org_id=None, position='', room='',
                   phone_work='', phone_ext='', phone_personal='', email='',
                   inn='', notes='', user_id=None):
    """Создаёт контакт и, при указанной организации, связь с ней.

    Дубли по ФИО не объединяет: вызывающий код сам проверяет кандидатов
    через find_person_candidates и получает явное решение пользователя.
    """
    full_name = ' '.join((full_name or '').split())
    if not full_name:
        raise DirectoryError('Для контакта необходимо ФИО')

    if org_id is not None:
        org = get_org(conn, org_id)
        if org is None:
            raise DirectoryError('Организация не найдена')
        if org['archived']:
            raise DirectoryError('Организация в архиве')

    cur = conn.execute(
        '''
        INSERT INTO phonebook
            (org_id, position, room, full_name, phone_work, phone_ext,
             phone_personal, email, inn, notes, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (org_id, (position or '').strip() if org_id is not None else '',
         (room or '').strip(), full_name, (phone_work or '').strip(),
         (phone_ext or '').strip(), (phone_personal or '').strip(),
         (email or '').strip(), (inn or '').strip(), (notes or '').strip(),
         user_id, _now()),
    )
    contact_id = cur.lastrowid

    employment_id = None
    if org_id is not None:
        employment_id = _insert_employment(
            conn, contact_id, org_id, (position or '').strip(), user_id
        )
    return {'status': 'created', 'contact_id': contact_id,
            'employment_id': employment_id}


def _insert_employment(conn, contact_id, org_id, position, user_id):
    cur = conn.execute(
        '''
        INSERT INTO phonebook_employment
            (contact_id, org_id, position, created_by, created_at)
        VALUES (?, ?, ?, ?, ?)
        ''',
        (contact_id, org_id, position or '', user_id, _now()),
    )
    return cur.lastrowid


# ─── Места работы ────────────────────────────────────────────────────────────

def set_main_employment(conn, contact_id, org_id, position='', user_id=None):
    """Меняет основное место работы, сохраняя прежние связи.

    Находит активную связь с той же организацией и должностью либо
    создаёт новую. Прежние связи не завершаются и не архивируются.
    Совместимые поля phonebook.org_id/position обновляются согласованно.
    """
    contact = get_contact(conn, contact_id)
    if contact is None:
        raise DirectoryError('Контакт не найден')
    if contact['archived']:
        raise DirectoryError('Контакт в архиве')

    position = (position or '').strip()
    if org_id is not None:
        org = get_org(conn, org_id)
        if org is None:
            raise DirectoryError('Организация не найдена')
        if org['archived']:
            raise DirectoryError('Организация в архиве')

        existing = None
        for row in list_employments(conn, contact_id):
            if row['org_id'] == org_id and (row['position'] or '') == position:
                existing = row
                break
        if existing is None:
            _insert_employment(conn, contact_id, org_id, position, user_id)
    else:
        org_id = None
        position = ''

    conn.execute(
        'UPDATE phonebook SET org_id = ?, position = ? WHERE id = ?',
        (org_id, position, contact_id),
    )


# ─── Снимок адресата письма ──────────────────────────────────────────────────

def letter_snapshot(conn, org_id=None, contact_id=None, employment_id=None,
                    address='', email=''):
    """Проверяет выбранные ID и формирует исторический снимок адресата.

    Снимок полностью независим от живого справочника: смена работы или
    правка общих карточек не меняют его. Адрес/email берутся из формы
    письма (использованные), при пустых значениях — из карточки.
    """
    snapshot = {
        'org_id': None, 'contact_id': None, 'employment_id': None,
        'snapshot_org_name': '', 'snapshot_full_name': '',
        'snapshot_position': '', 'snapshot_email': '', 'snapshot_phone': '',
    }

    if org_id is not None:
        org = get_org(conn, org_id)
        if org is None:
            raise DirectoryError('Указанная организация не найдена')
        if org['archived']:
            raise DirectoryError('Организация в архиве — выберите другую')
        snapshot['org_id'] = org_id
        snapshot['snapshot_org_name'] = org['name']

    if contact_id is not None:
        contact = get_contact(conn, contact_id)
        if contact is None:
            raise DirectoryError('Указанный контакт не найден')
        if contact['archived']:
            raise DirectoryError('Контакт в архиве — выберите другого')
        snapshot['contact_id'] = contact_id
        snapshot['snapshot_full_name'] = contact['full_name']
        snapshot['snapshot_email'] = (email or '').strip() or (contact['email'] or '')
        snapshot['snapshot_phone'] = (address or '').strip() or (contact['phone_work'] or '')

        if employment_id is not None:
            emp = get_employment(conn, employment_id)
            if emp is None:
                raise DirectoryError('Указанное место работы не найдено')
            if emp['archived']:
                raise DirectoryError('Место работы в архиве')
            if emp['contact_id'] != contact_id:
                raise DirectoryError('Место работы не принадлежит выбранному человеку')
            if org_id is not None and emp['org_id'] != org_id:
                raise DirectoryError('Место работы не соответствует выбранной организации')
            snapshot['employment_id'] = employment_id
            snapshot['snapshot_position'] = emp['position'] or ''
        else:
            snapshot['snapshot_position'] = contact['position'] or ''

    return snapshot


# ─── Архив и удаление ────────────────────────────────────────────────────────

def _set_archived(conn, table, record_id, archived, label, user_id=None):
    row = conn.execute(
        f'SELECT id FROM {table} WHERE id = ?', (record_id,)
    ).fetchone()
    if row is None:
        raise DirectoryError(f'{label} не найден(а)')
    conn.execute(
        f'UPDATE {table} SET archived = ? WHERE id = ?',
        (1 if archived else 0, record_id),
    )


def archive_org(conn, org_id, user_id=None):
    _set_archived(conn, 'phonebook_orgs', org_id, True, 'Организация', user_id)


def restore_org(conn, org_id, user_id=None):
    _set_archived(conn, 'phonebook_orgs', org_id, False, 'Организация', user_id)


def archive_contact(conn, contact_id, user_id=None):
    _set_archived(conn, 'phonebook', contact_id, True, 'Контакт', user_id)


def restore_contact(conn, contact_id, user_id=None):
    _set_archived(conn, 'phonebook', contact_id, False, 'Контакт', user_id)


def org_dependencies(conn, org_id):
    """Зависимости организации: письма, контакты."""
    letters = conn.execute(
        'SELECT COUNT(*) FROM letters WHERE org_id = ?', (org_id,)
    ).fetchone()[0]
    # Совместимое поле основного места работы (сервис держит его
    # синхронным со связями) плюс активные связи любого контакта.
    main_contacts = conn.execute(
        'SELECT COUNT(*) FROM phonebook WHERE org_id = ?', (org_id,)
    ).fetchone()[0]
    linked = conn.execute(
        'SELECT COUNT(*) FROM phonebook_employment '
        'WHERE org_id = ? AND archived = 0', (org_id,)
    ).fetchone()[0]
    return {'letters': letters, 'contacts': max(main_contacts, linked)}


def contact_dependencies(conn, contact_id):
    """Зависимости контакта: письма."""
    return {
        'letters': conn.execute(
            'SELECT COUNT(*) FROM letters WHERE contact_id = ?', (contact_id,)
        ).fetchone()[0],
    }


def delete_org(conn, org_id, user_id=None):
    """Окончательное удаление организации — только без зависимостей.

    Каскадное удаление людей не выполняется: при зависимостях
    вызывающий код предлагает архивирование.
    """
    deps = org_dependencies(conn, org_id)
    if deps['letters'] or deps['contacts']:
        raise DirectoryError(
            'Организация связана с письмами или контактами. '
            'Удалите связи или архивируйте организацию.'
        )
    conn.execute('DELETE FROM phonebook_employment WHERE org_id = ?', (org_id,))
    conn.execute('DELETE FROM phonebook_orgs WHERE id = ?', (org_id,))


def delete_contact(conn, contact_id, user_id=None):
    """Окончательное удаление контакта — только без зависимостей."""
    deps = contact_dependencies(conn, contact_id)
    if deps['letters']:
        raise DirectoryError(
            'Контакт связан с письмами. Архивируйте его вместо удаления.'
        )
    conn.execute('DELETE FROM phonebook_employment WHERE contact_id = ?',
                 (contact_id,))
    conn.execute('DELETE FROM phonebook WHERE id = ?', (contact_id,))
