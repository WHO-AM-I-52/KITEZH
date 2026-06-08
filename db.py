# ╔═══════════════════════════════════════════════════════════════
# ║                         db.py                               ║
# ║  Подключение к базе данных и пути к папкам приложения       ║
# ╚═══════════════════════════════════════════════════════════════

import sqlite3
import os

# ─── ПУТИ ───────────────────────────────────────────────────────────────────────────────────────

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BASE_DIR, 'db', 'database.db')
UPLOADS_DIR = os.path.join(BASE_DIR, 'uploads')
UPLOADS_TMP = os.path.join(BASE_DIR, 'uploads', 'tmp')
REPORTS_DIR = os.path.join(BASE_DIR, 'reports')
ALLOWED_EXT = {'pdf', 'ppt', 'pptx', 'doc', 'docx', 'xlsx', 'zip'}

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(UPLOADS_TMP, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)


# ─── МИГРАЦИЯ ──────────────────────────────────────────────────────────────────────────────────

# Маппинг названий предметов → префиксы рег. номеров.
# Сравнение нечувствительно к регистру (LOWER).
# Список охватывает оба варианта написания (старый и новый).
_PREFIX_BY_NAME = {
    'подбор з/у':                                   'ПЗУ',
    'подбор зу':                                    'ПЗУ',
    'подбор мер поддержки':                         'ПМП',
    'подбор з/у / здания / помещения':              'ПЗУ',
    'подбор зу, помещений':                         'ПЗУ',
    'консультация':                                 'К',
    'подбор здания / помещения':                    'ПЗ',
    'проблемный вопрос':                            'ПВ',
    'продление разрешения на строительство':        'ПРС',
    'подбор индустриального парка':                 'ПИП',
}


def _has_column(conn, table: str, column: str) -> bool:
    """True если колонка уже есть в таблице."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r['name'] == column for r in rows)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    ).fetchone()
    return row is not None


def _ensure_prefixes(conn):
    """
    Идемпотентно заполняет reg_prefix для записей где он ещё NULL.
    Вызывается при каждом старте — безопасно, уже заполненные не трогает.
    """
    if not _has_column(conn, 'subject_types', 'reg_prefix'):
        return
    for name, prefix in _PREFIX_BY_NAME.items():
        conn.execute(
            "UPDATE subject_types SET reg_prefix=? WHERE LOWER(name)=LOWER(?) AND (reg_prefix IS NULL OR reg_prefix='')",
            (prefix, name)
        )


def _migrate(conn):
    """
    Автоматическое добавление новых таблиц и колонок если они отсутствуют.
    ВАЖНО: все изменения должны быть идемпотентны — при повторном запуске
    на уже обновлённой БД ничего не должно ломаться.
    """

    # ─ Таблица присутствия онлайн
    conn.execute("""
        CREATE TABLE IF NOT EXISTS online_presence (
            user_id   INTEGER PRIMARY KEY,
            last_seen TEXT NOT NULL
        )
    """)

    # ─ Колонка action в request_history
    if not _has_column(conn, 'request_history', 'action'):
        conn.execute(
            "ALTER TABLE request_history ADD COLUMN action TEXT DEFAULT 'edit'"
        )

    # ════════════════════════════════════════════════════════════════
    # МинЭК: справочники и новые поля
    # ════════════════════════════════════════════════════════════════

    # ─ Справочник «Предмет обращения»
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subject_types (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
    """)

    cnt = conn.execute("SELECT COUNT(*) FROM subject_types").fetchone()[0]
    if cnt == 0:
        conn.executemany(
            "INSERT OR IGNORE INTO subject_types (name) VALUES (?)",
            [
                ('Подбор з/у',),
                ('Подбор мер поддержки',),
                ('Подбор индустриального парка',),
                ('Подбор з/у / здания / помещения',),
                ('Консультация',),
                ('Подбор здания / помещения',),
                ('Проблемный вопрос',),
                ('Продление разрешения на строительство',),
            ]
        )

    # ─ Поле reg_prefix в subject_types
    if not _has_column(conn, 'subject_types', 'reg_prefix'):
        conn.execute(
            "ALTER TABLE subject_types ADD COLUMN reg_prefix TEXT"
        )

    # ─ Заполняем reg_prefix всегда (идемпотентно, только NULL-записи)
    _ensure_prefixes(conn)

    # ─ Таблица счётчиков рег. номеров (per-prefix, per-year)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reg_number_sequences (
            prefix   TEXT    NOT NULL,
            year     INTEGER NOT NULL,
            last_seq INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (prefix, year)
        )
    """)

    # ─ Справочник «Итоги работы по обращению»
    result_types_exists_before = _table_exists(conn, 'result_types')
    conn.execute("""
        CREATE TABLE IF NOT EXISTS result_types (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT NOT NULL UNIQUE,
            color_hex TEXT NOT NULL DEFAULT 'FFFFFF'
        )
    """)

    default_results = [
        ('Вопрос решен',                           'FF0000'),
        ('Проект взят на сопровождение',          '92D050'),
        ('Обращение частично отработано',         'A6A6A6'),
        ('На исполнении',                         'FFFFFF'),
        ('Взято на сопровождение',                 '92D050'),
        ('В работе',                               'FFFFFF'),
        ('Отвечено',                              'FF0000'),
        ('Подобранные зу направлены инвестору',   'FF0000'),
        ('Подобранные помещения направлены инвестору', 'FF0000'),
        ('Подобранные зу и помещения направлены инвестору', 'FF0000'),
        ('Проведено рабочее совещание с инвестором',  '92D050'),
        ('Проведено совещание (с участием ОИВ/ОМС)',  '92D050'),
        ('Отказ',                                   'A6A6A6'),
    ]

    result_cnt = conn.execute("SELECT COUNT(*) FROM result_types").fetchone()[0]
    if (not result_types_exists_before) or result_cnt == 0:
        conn.executemany(
            "INSERT OR IGNORE INTO result_types (name, color_hex) VALUES (?, ?)",
            default_results
        )

    # ─ Новые поля в таблице requests
    if not _has_column(conn, 'requests', 'subject_type_id'):
        conn.execute(
            "ALTER TABLE requests ADD COLUMN subject_type_id INTEGER REFERENCES subject_types(id)"
        )
    if not _has_column(conn, 'requests', 'feedback_date'):
        conn.execute(
            "ALTER TABLE requests ADD COLUMN feedback_date TEXT"
        )
    if not _has_column(conn, 'requests', 'result_type_id'):
        conn.execute(
            "ALTER TABLE requests ADD COLUMN result_type_id INTEGER REFERENCES result_types(id)"
        )
    if not _has_column(conn, 'requests', 'incoming_number'):
        conn.execute(
            "ALTER TABLE requests ADD COLUMN incoming_number TEXT"
        )

    # ════════════════════════════════════════════════════════════════
    # Права доступа: новые колонки в таблице users
    # ════════════════════════════════════════════════════════════════

    if not _has_column(conn, 'users', 'can_export_full'):
        conn.execute(
            "ALTER TABLE users ADD COLUMN can_export_full INTEGER NOT NULL DEFAULT 0"
        )
    if not _has_column(conn, 'users', 'can_import_full'):
        conn.execute(
            "ALTER TABLE users ADD COLUMN can_import_full INTEGER NOT NULL DEFAULT 0"
        )
    if not _has_column(conn, 'users', 'can_view_investmap'):
        conn.execute(
            "ALTER TABLE users ADD COLUMN can_view_investmap INTEGER NOT NULL DEFAULT 0"
        )

    # ════════════════════════════════════════════════════════════════
    # Issue #53: новая логика статусов обращений
    # ════════════════════════════════════════════════════════════════

    if not _has_column(conn, 'requests', 'review_days'):
        conn.execute("ALTER TABLE requests ADD COLUMN review_days INTEGER NOT NULL DEFAULT 7")
    if not _has_column(conn, 'requests', 'review_deadline'):
        conn.execute("ALTER TABLE requests ADD COLUMN review_deadline TEXT")
    if not _has_column(conn, 'requests', 'registered_at'):
        conn.execute("ALTER TABLE requests ADD COLUMN registered_at TEXT")
    if not _has_column(conn, 'requests', 'responsible_id'):
        conn.execute("ALTER TABLE requests ADD COLUMN responsible_id INTEGER REFERENCES users(id)")
    if not _has_column(conn, 'requests', 'responsible_not_in_system'):
        conn.execute("ALTER TABLE requests ADD COLUMN responsible_not_in_system INTEGER NOT NULL DEFAULT 0")
    if not _has_column(conn, 'requests', 'responsible_name_external'):
        conn.execute("ALTER TABLE requests ADD COLUMN responsible_name_external TEXT")
    if not _has_column(conn, 'requests', 'reviewer_id'):
        conn.execute("ALTER TABLE requests ADD COLUMN reviewer_id INTEGER REFERENCES users(id)")
    if not _has_column(conn, 'requests', 'reviewer_not_in_system'):
        conn.execute("ALTER TABLE requests ADD COLUMN reviewer_not_in_system INTEGER NOT NULL DEFAULT 0")
    if not _has_column(conn, 'requests', 'reviewer_name_external'):
        conn.execute("ALTER TABLE requests ADD COLUMN reviewer_name_external TEXT")
    if not _has_column(conn, 'requests', 'reviewer_comment'):
        conn.execute("ALTER TABLE requests ADD COLUMN reviewer_comment TEXT")
    if not _has_column(conn, 'requests', 'reviewer_decision'):
        conn.execute("ALTER TABLE requests ADD COLUMN reviewer_decision TEXT")
    if not _has_column(conn, 'requests', 'reviewer_decision_at'):
        conn.execute("ALTER TABLE requests ADD COLUMN reviewer_decision_at TEXT")
    if not _has_column(conn, 'requests', 'sent_to_applicant_at'):
        conn.execute("ALTER TABLE requests ADD COLUMN sent_to_applicant_at TEXT")
    if not _has_column(conn, 'requests', 'send_method'):
        conn.execute("ALTER TABLE requests ADD COLUMN send_method TEXT")
    if not _has_column(conn, 'requests', 'applicant_feedback'):
        conn.execute("ALTER TABLE requests ADD COLUMN applicant_feedback TEXT")
    if not _has_column(conn, 'requests', 'applicant_feedback_at'):
        conn.execute("ALTER TABLE requests ADD COLUMN applicant_feedback_at TEXT")
    if not _has_column(conn, 'requests', 'taken_under_supervision'):
        conn.execute("ALTER TABLE requests ADD COLUMN taken_under_supervision INTEGER NOT NULL DEFAULT 0")

    # ─ Маппинг старых статусов → новые (идемпотентный)
    conn.execute("UPDATE requests SET status='registered'        WHERE status='review'")
    conn.execute("UPDATE requests SET status='in_progress'       WHERE status='accepted'")
    conn.execute("UPDATE requests SET status='sent_to_applicant' WHERE status='answered'")

    # ════════════════════════════════════════════════════════════════
    # Инициализация счётчиков reg_number_sequences по номерам
    # формата PREFIX-YYYY-NNN (4-значный год). Идемпотентно.
    # ════════════════════════════════════════════════════════════════
    conn.execute("""
        INSERT OR REPLACE INTO reg_number_sequences (prefix, year, last_seq)
        SELECT prefix, year, MAX(seq) AS last_seq
        FROM (
            SELECT
                SUBSTR(request_number, 1, INSTR(request_number, '-') - 1) AS prefix,
                CAST(SUBSTR(request_number,
                     INSTR(request_number, '-') + 1, 4) AS INTEGER)       AS year,
                CAST(SUBSTR(request_number,
                     INSTR(request_number, '-') + 6) AS INTEGER)          AS seq
            FROM requests
            WHERE request_number IS NOT NULL
              AND request_number != ''
              AND INSTR(request_number, '-') > 0
        )
        WHERE prefix != ''
          AND year BETWEEN 2020 AND 2100
          AND seq > 0
        GROUP BY prefix, year
        HAVING MAX(seq) > COALESCE(
            (SELECT last_seq FROM reg_number_sequences rns
             WHERE rns.prefix = prefix AND rns.year = year), 0
        )
    """)

    # ════════════════════════════════════════════════════════════════
    # Issue #48: единицы измерения инфраструктурных полей
    # ════════════════════════════════════════════════════════════════
    _unit_cols = [
        ('elec_unit',  'кВт'),
        ('heat_unit',  'Гкал/ч'),
        ('gas_unit_h', 'м³/ч'),
        ('gas_unit_y', 'м³/год'),
        ('water_unit', 'м³/сут'),
    ]
    for col, default in _unit_cols:
        if not _has_column(conn, 'requests', col):
            conn.execute(
                f"ALTER TABLE requests ADD COLUMN {col} TEXT NOT NULL DEFAULT '{default}'"
            )
        conn.execute(
            f"UPDATE requests SET {col}=? WHERE {col} IS NULL OR {col}=''",
            (default,)
        )

    # ════════════════════════════════════════════════════════════════
    # Индексы — fix #6
    # ════════════════════════════════════════════════════════════════
    conn.execute("CREATE INDEX IF NOT EXISTS idx_req_status     ON requests(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_req_created_by ON requests(created_by)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_req_assigned   ON requests(assigned_to)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_req_date       ON requests(request_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notif_user     ON notifications(user_id, is_read)")

    # ════════════════════════════════════════════════════════════════
    # Таблица хэшей файлов обращений (SHA-256)
    # Идемпотентно: CREATE TABLE IF NOT EXISTS
    # ════════════════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS request_file_hashes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
            filename   TEXT    NOT NULL,
            sha256     TEXT    NOT NULL,
            created_at TEXT    NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_file_hashes_req ON request_file_hashes(request_id)"
    )

    # ════════════════════════════════════════════════════════════════
    # Чистка NULL → '' в текстовых полях таблицы requests.
    # Идемпотентно: трогает только записи с NULL.
    # Не затрагивает: числа (INT/FLOAT), FK, даты, файлы, единицы.
    # ════════════════════════════════════════════════════════════════
    _TEXT_FIELDS_TO_CLEAN = [
        'status', 'source_type',
        'applicant_full_name', 'applicant_short_name', 'applicant_legal_form',
        'applicant_inn', 'applicant_msp_category', 'applicant_okved_main',
        'postal_address', 'legal_address', 'project_name',
        'contact_person', 'contact_phone', 'contact_email',
        'construction_stages',
        'product_nomenclature', 'production_description', 'object_composition',
        'site_right', 'hazard_class', 'site_shape', 'site_other',
        'heat_source', 'gas_purpose',
        'internet', 'engineering_extra',
        'road_extra', 'railway_extra', 'transport_extra',
        'preferred_districts', 'location_extra',
        'raw_materials', 'raw_extra', 'additional_info',
        'answer_method', 'answer_method_other', 'answer_notes',
        'request_files', 'edit_reason',
        'incoming_number',
        'responsible_name_external', 'reviewer_name_external',
        'reviewer_comment', 'reviewer_decision',
        'send_method', 'applicant_feedback',
    ]
    set_parts = ', '.join(f"{col}=COALESCE({col}, '')" for col in _TEXT_FIELDS_TO_CLEAN)
    conn.execute(f"UPDATE requests SET {set_parts} WHERE 1=1")

    conn.commit()


# ─── ПОДКЛЮЧЕНИЕ К БД ──────────────────────────────────────────────────────────────────────────────────────

def get_db():
    """
    Открывает соединение с базой данных SQLite.
    - row_factory = sqlite3.Row — обращение к полям по имени
    - WAL-режим — производительность при параллельных запросах
    - _migrate() — автоматически добавляет новые таблицы/колонки/индексы
    """
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _migrate(conn)
    return conn
