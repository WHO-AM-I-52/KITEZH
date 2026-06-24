# ╔═══════════════════════════════════════════════
# ║                         db.py                               ║
# ║  Подключение к базе данных и пути к папкам приложения       ║
# ╚═══════════════════════════════════════════════

import sqlite3
from datetime import date, timedelta

# ─── ПУТИ ────────────────────────────────────────────────────────────────────────────────────
# Единый источник правды для всех runtime-путей — paths.py (корень проекта).
# Здесь они реэкспортируются для обратной совместимости: часть кода импортирует
# BASE_DIR / REPORTS_DIR / DB_PATH / UPLOADS_DIR именно из db.
from paths import (
    BASE_DIR,
    DB_PATH,
    UPLOADS_DIR,
    UPLOADS_TMP,
    REPORTS_DIR,
)

ALLOWED_EXT = {'pdf', 'ppt', 'pptx', 'doc', 'docx', 'xlsx', 'zip'}

# Каталоги гарантированно создаются в paths.py при импорте.


# ─── НОРМАТИВЫ ПО ЭТАПАМ (рабочих дней) ──────────────────────────────────────
# draft → registered
NORM_TO_REGISTERED       = 1
# registered → in_progress
NORM_TO_IN_PROGRESS      = 1
# in_progress → under_review
NORM_TO_UNDER_REVIEW     = 5
# under_review → ready_to_send
NORM_TO_READY_TO_SEND    = 2
# ready_to_send → sent_to_applicant
NORM_TO_SENT             = 1
# sent_to_applicant → closed
NORM_TO_CLOSED           = 12

# Маппинг «в какой статус переходим» → норматив рабочих дней для ЭТОГО этапа
STATUS_NORM_DAYS = {
    'registered':        NORM_TO_REGISTERED,
    'in_progress':       NORM_TO_IN_PROGRESS,
    'under_review':      NORM_TO_UNDER_REVIEW,
    'ready_to_send':     NORM_TO_READY_TO_SEND,
    'sent_to_applicant': NORM_TO_SENT,
    'closed':            NORM_TO_CLOSED,
}

# Суммарный норматив полного цикла (для обратной совместимости дашборда)
NORM_TOTAL_DAYS = (
    NORM_TO_REGISTERED + NORM_TO_IN_PROGRESS + NORM_TO_UNDER_REVIEW
    + NORM_TO_READY_TO_SEND + NORM_TO_SENT
)  # = 10 рабочих дней до отправки заявителю

# ─── МАППИНГ ────────────────────────────────────────────────────────────────────────────────────

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

_migrated = False


def _has_column(conn, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r['name'] == column for r in rows)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    ).fetchone()
    return row is not None


def _ensure_prefixes(conn):
    if not _has_column(conn, 'subject_types', 'reg_prefix'):
        return
    for name, prefix in _PREFIX_BY_NAME.items():
        conn.execute(
            "UPDATE subject_types SET reg_prefix=? WHERE LOWER(name)=LOWER(?) AND (reg_prefix IS NULL OR reg_prefix='')",
            (prefix, name)
        )


def _add_workdays(start: date, days: int) -> date:
    """Добавляет `days` рабочих дней (Пн–Пт) к дате `start`."""
    current, added = start, 0
    while added < days:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def _backfill_review_deadline(conn):
    """
    Бэкфилл review_deadline по текущему статусу обращения:
    deadline = дата перехода в текущий статус + норматив этапа (рабочих дней).
    Если дата перехода неизвестна — используем request_date.
    Идемпотентно: перезаписывает только записи, где review_deadline IS NULL.
    """
    if not _has_column(conn, 'requests', 'review_deadline'):
        return

    # Нормативы этапов в рабочих днях (сколько дней даётся на текущий этап)
    stage_norms = STATUS_NORM_DAYS

    rows = conn.execute("""
        SELECT id, status, request_date,
               at_registered, at_in_progress, at_under_review,
               at_ready_to_send, at_sent_to_applicant,
               COALESCE(review_days, 7) AS review_days
        FROM requests
        WHERE (review_deadline IS NULL OR review_deadline = '')
          AND status NOT IN ('draft', 'closed')
          AND request_date IS NOT NULL
          AND request_date != ''
    """).fetchall() if _has_column(conn, 'requests', 'at_registered') else []

    for row in rows:
        try:
            status = row['status']
            at_field_map = {
                'registered':        'at_registered',
                'in_progress':       'at_in_progress',
                'under_review':      'at_under_review',
                'ready_to_send':     'at_ready_to_send',
                'sent_to_applicant': 'at_sent_to_applicant',
            }
            at_field = at_field_map.get(status)
            at_val = row[at_field] if at_field else None
            start_str = at_val if at_val else row['request_date']
            start = date.fromisoformat(start_str[:10])
            norm = stage_norms.get(status, int(row['review_days']))
            deadline = _add_workdays(start, norm)
            conn.execute(
                "UPDATE requests SET review_deadline=? WHERE id=?",
                (deadline.isoformat(), row['id'])
            )
        except (ValueError, TypeError):
            pass


def _migrate(conn):
    """
    Автоматическое добавление новых таблиц и колонок если они отсутствуют.
    Вызывается ОДИН РАЗ при старте приложения (fix #57).
    """

    conn.execute("""
        CREATE TABLE IF NOT EXISTS online_presence (
            user_id   INTEGER PRIMARY KEY,
            last_seen TEXT NOT NULL
        )
    """)

    if not _has_column(conn, 'request_history', 'action'):
        conn.execute(
            "ALTER TABLE request_history ADD COLUMN action TEXT DEFAULT 'edit'"
        )
    if not _has_column(conn, 'request_history', 'field'):
        conn.execute("ALTER TABLE request_history ADD COLUMN field TEXT")
    if not _has_column(conn, 'request_history', 'old_val'):
        conn.execute("ALTER TABLE request_history ADD COLUMN old_val TEXT")
    if not _has_column(conn, 'request_history', 'new_val'):
        conn.execute("ALTER TABLE request_history ADD COLUMN new_val TEXT")

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

    if not _has_column(conn, 'subject_types', 'reg_prefix'):
        conn.execute(
            "ALTER TABLE subject_types ADD COLUMN reg_prefix TEXT"
        )

    _ensure_prefixes(conn)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS reg_number_sequences (
            prefix   TEXT    NOT NULL,
            year     INTEGER NOT NULL,
            last_seq INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (prefix, year)
        )
    """)

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

    # ════════════════════════════════════════════════════════════════
    # Даты переходов по этапам (для многоэтапного норматива)
    # ════════════════════════════════════════════════════════════════
    stage_date_cols = [
        'at_registered',
        'at_in_progress',
        'at_under_review',
        'at_ready_to_send',
        'at_sent_to_applicant',
        'at_closed',
    ]
    for col in stage_date_cols:
        if not _has_column(conn, 'requests', col):
            conn.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT")

    if _has_column(conn, 'requests', 'at_registered'):
        conn.execute(
            "UPDATE requests SET at_registered = registered_at "
            "WHERE at_registered IS NULL AND registered_at IS NOT NULL AND registered_at != ''"
        )
    if _has_column(conn, 'requests', 'at_sent_to_applicant'):
        conn.execute(
            "UPDATE requests SET at_sent_to_applicant = sent_to_applicant_at "
            "WHERE at_sent_to_applicant IS NULL AND sent_to_applicant_at IS NOT NULL AND sent_to_applicant_at != ''"
        )

    conn.execute("UPDATE requests SET status='registered'        WHERE status='review'")
    conn.execute("UPDATE requests SET status='in_progress'       WHERE status='accepted'")
    conn.execute("UPDATE requests SET status='sent_to_applicant' WHERE status='answered'")

    _backfill_review_deadline(conn)

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

    if not _has_column(conn, 'requests', 'contact_position'):
        conn.execute(
            "ALTER TABLE requests ADD COLUMN contact_position TEXT NOT NULL DEFAULT ''"
        )

    conn.execute("CREATE INDEX IF NOT EXISTS idx_req_status     ON requests(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_req_created_by ON requests(created_by)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_req_assigned   ON requests(assigned_to)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_req_date       ON requests(request_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_notif_user     ON notifications(user_id, is_read)")

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

    conn.execute("""
        CREATE TABLE IF NOT EXISTS ocr_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TEXT    NOT NULL,
            user_id     INTEGER,
            filename    TEXT,
            raw_text    TEXT,
            fields_json TEXT,
            msg         TEXT,
            ok          INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ocr_log_created ON ocr_log(created_at)"
    )

    _TEXT_FIELDS_TO_CLEAN = [
        'status', 'source_type',
        'applicant_full_name', 'applicant_short_name', 'applicant_legal_form',
        'applicant_inn', 'applicant_msp_category', 'applicant_okved_main',
        'postal_address', 'legal_address', 'project_name',
        'contact_person', 'contact_phone', 'contact_email',
        'contact_position',
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

    if _table_exists(conn, 'classifiers'):
        conn.execute("""
            INSERT OR IGNORE INTO classifiers (category, value, sort_order)
            VALUES ('tray_notify_level', 'critical', 1)
        """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS review_chain (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id    INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
            user_id       INTEGER REFERENCES users(id),
            external_name TEXT,
            step_order    INTEGER NOT NULL,
            decision      TEXT,
            comment       TEXT,
            decided_at    TEXT
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_review_chain_req ON review_chain(request_id)"
    )

    # ════════════════════════════════════════════════════════════════
    # Таблицы инвест. карты — fix: отсутствовали в _migrate(),
    # из-за чего при обновлении данные справочников терялись
    # ════════════════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS investmap_fields (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            tech_name          TEXT    NOT NULL UNIQUE,
            display_name       TEXT    NOT NULL,
            is_required        TEXT    NOT NULL DEFAULT 'да',
            required_condition TEXT,
            classifier_num     TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS investmap_classifiers (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            classifier_num TEXT    NOT NULL,
            field_name     TEXT    NOT NULL,
            sort_order     INTEGER NOT NULL DEFAULT 0,
            value          TEXT    NOT NULL,
            UNIQUE(classifier_num, value)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_investmap_cls_num "
        "ON investmap_classifiers(classifier_num)"
    )

    # ════════════════════════════════════════════════════════════════
    # Журнал писем
    # ════════════════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS letters (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT    NOT NULL,
            number     TEXT    NOT NULL DEFAULT '',
            subject    TEXT    NOT NULL DEFAULT '',
            note       TEXT             DEFAULT '',
            created_by INTEGER NOT NULL REFERENCES users(id),
            created_at TEXT    NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS letter_tags (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS letter_tag_links (
            letter_id INTEGER NOT NULL REFERENCES letters(id) ON DELETE CASCADE,
            tag_id    INTEGER NOT NULL REFERENCES letter_tags(id) ON DELETE CASCADE,
            PRIMARY KEY (letter_id, tag_id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_letters_date   ON letters(date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_letters_number ON letters(number)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ltlinks_tag    ON letter_tag_links(tag_id)"
    )

    # ════════════════════════════════════════════════════════════════
    # Закреплённые заметки по обращению (📌 Заметка)
    # ════════════════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pinned_notes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            object_type TEXT    NOT NULL DEFAULT 'request',
            object_id   INTEGER NOT NULL,
            text        TEXT    NOT NULL DEFAULT '',
            created_by  INTEGER NOT NULL REFERENCES users(id),
            updated_at  TEXT    NOT NULL
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pinned_notes_obj "
        "ON pinned_notes(object_type, object_id)"
    )

    conn.commit()


# ─── ПОДКЛЮЧЕНИЕ К БД ────────────────────────────────────────────────────────────────────────────────────

def get_db():
    global _migrated
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    if not _migrated:
        _migrate(conn)
        _migrated = True
    return conn


def run_migrations():
    global _migrated
    if not _migrated:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        _migrate(conn)
        conn.close()
        _migrated = True
