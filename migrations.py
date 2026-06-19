# ╔══════════════════════════════════════════════════════════════╗
# ║ migrations.py                                                ║
# ║ Инициализация и миграция БД (вынесено из app.py)             ║
# ║ +can_view_phonebook, can_view_investmap,                     ║
# ║  can_export_full, can_import_full, can_investmap_rules       ║
# ╚══════════════════════════════════════════════════════════════╝

import sqlite3

from db import DB_PATH
from auth_utils import hash_pw
from spravochnik import LEGAL_FORMS_DEFAULT, DISTRICTS_DEFAULT, SOURCE_TYPES_DEFAULT
from db import get_db

# ──────────────────────────────────────────────────────────────────────────────
# НОВЫЕ КОЛОНКИ requests (#53):
#   registered_at, review_days, review_deadline
#   responsible_id, responsible_not_in_system, responsible_name_external
#   reviewer_id, reviewer_not_in_system, reviewer_name_external
#   reviewer_decision, reviewer_comment, reviewer_decision_at
#   sent_to_applicant_at, send_method
#   applicant_feedback, applicant_feedback_at
# ──────────────────────────────────────────────────────────────────────────────
_NEW_REQUEST_COLS = [
    ('registered_at',                'TEXT'),
    ('review_days',                  'INTEGER'),
    ('review_deadline',              'TEXT'),
    ('responsible_id',               'INTEGER'),
    ('responsible_not_in_system',    'INTEGER DEFAULT 0'),
    ('responsible_name_external',    'TEXT'),
    ('reviewer_id',                  'INTEGER'),
    ('reviewer_not_in_system',       'INTEGER DEFAULT 0'),
    ('reviewer_name_external',       'TEXT'),
    ('reviewer_decision',            'TEXT'),
    ('reviewer_comment',             'TEXT'),
    ('reviewer_decision_at',         'TEXT'),
    ('sent_to_applicant_at',         'TEXT'),
    ('send_method',                  'TEXT'),
    ('applicant_feedback',           'TEXT'),
    ('applicant_feedback_at',        'TEXT'),
]


def _migrate_request_cols(conn):
    """ADD COLUMN для всех новых колонок requests (#53)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(requests)").fetchall()}
    for col, typ in _NEW_REQUEST_COLS:
        if col not in cols:
            conn.execute(f"ALTER TABLE requests ADD COLUMN {col} {typ}")


def _migrate_users_cols(conn):
    """Единая точка миграции колонок users — используется и init_db, и migrate_db."""
    user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    for col in [
        'can_create', 'can_edit_others', 'can_confirm', 'can_delete',
        'can_rollback', 'can_export', 'can_export_full', 'can_import_full',
        'can_classifiers', 'can_users', 'can_view_all',
        'can_view_investmap', 'can_view_phonebook', 'can_investmap_rules',
    ]:
        if col not in user_cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT 0")
    if 'must_change_password' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER DEFAULT 0")
    for col, definition in [
        ('email',               'TEXT DEFAULT NULL'),
        ('theme',               "TEXT DEFAULT 'light'"),
        ('email_notifications', 'INTEGER DEFAULT 0'),
        ('is_active',           'INTEGER NOT NULL DEFAULT 1'),
    ]:
        if col not in user_cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")


def _migrate_classifiers_tables(conn):
    """Создаёт subject_types/result_types если отсутствуют (для старых БД)."""
    conn.executescript("""
CREATE TABLE IF NOT EXISTS subject_types (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE,
    reg_prefix TEXT
);
CREATE TABLE IF NOT EXISTS result_types (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    color_hex TEXT DEFAULT 'FFFFFF'
);
""")


def _migrate_districts_table(conn):
    """Создаёт таблицу districts если отсутствует (нужна view_routes.py)."""
    conn.executescript("""
CREATE TABLE IF NOT EXISTS districts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER DEFAULT 0
);
""")
    # Наполнить из DISTRICTS_DEFAULT, если таблица пустая
    if not conn.execute("SELECT id FROM districts LIMIT 1").fetchone():
        for i, name in enumerate(DISTRICTS_DEFAULT):
            conn.execute(
                "INSERT OR IGNORE INTO districts (name, is_active, sort_order) VALUES (?,1,?)",
                (name, i)
            )


def _migrate_review_chain_table(conn):
    """Создаёт таблицу review_chain если отсутствует (нужна view_routes.py)."""
    conn.executescript("""
CREATE TABLE IF NOT EXISTS review_chain (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id    INTEGER NOT NULL,
    step_order    INTEGER NOT NULL DEFAULT 0,
    user_id       INTEGER,
    external_name TEXT,
    decision      TEXT,
    comment       TEXT,
    decided_at    TEXT,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_rc_request ON review_chain(request_id);
""")


def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript("""
CREATE TABLE IF NOT EXISTS users (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    username             TEXT UNIQUE NOT NULL,
    password             TEXT NOT NULL,
    full_name            TEXT NOT NULL,
    role                 TEXT NOT NULL DEFAULT 'employee',
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP,
    must_change_password INTEGER DEFAULT 0,
    can_create           INTEGER DEFAULT 0,
    can_edit_others      INTEGER DEFAULT 0,
    can_confirm          INTEGER DEFAULT 0,
    can_delete           INTEGER DEFAULT 0,
    can_rollback         INTEGER DEFAULT 0,
    can_export           INTEGER DEFAULT 0,
    can_export_full      INTEGER DEFAULT 0,
    can_import_full      INTEGER DEFAULT 0,
    can_classifiers      INTEGER DEFAULT 0,
    can_users            INTEGER DEFAULT 0,
    can_view_all         INTEGER DEFAULT 0,
    can_view_investmap   INTEGER DEFAULT 0,
    can_view_phonebook   INTEGER DEFAULT 0,
    can_investmap_rules  INTEGER DEFAULT 0,
    is_active            INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS login_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    username   TEXT,
    event      TEXT NOT NULL,
    ip         TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ll_user  ON login_log(user_id);
CREATE INDEX IF NOT EXISTS idx_ll_event ON login_log(event);
CREATE TABLE IF NOT EXISTS request_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL,
    changed_by INTEGER NOT NULL,
    changed_at TEXT DEFAULT CURRENT_TIMESTAMP,
    changes TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rh_request ON request_history(request_id);
CREATE TABLE IF NOT EXISTS classifiers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL, value TEXT NOT NULL, sort_order INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL, message TEXT NOT NULL,
    link TEXT, is_read INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS saved_filters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, description TEXT,
    params TEXT NOT NULL, created_by INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP, sort_order INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS favorites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL, request_id INTEGER NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, request_id)
);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_number TEXT, request_date TEXT, status TEXT DEFAULT 'draft',
    created_by INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    confirmed_by INTEGER, confirmed_at TEXT, admin_comment TEXT,
    assigned_to INTEGER, consent_disclosure INTEGER DEFAULT 0,
    source_type TEXT, incoming_number TEXT,
    applicant_full_name TEXT, applicant_short_name TEXT, applicant_legal_form TEXT,
    applicant_inn TEXT, applicant_msp_category TEXT, applicant_okved_main TEXT,
    postal_address TEXT, legal_address TEXT, project_name TEXT,
    contact_person TEXT, contact_phone TEXT, contact_email TEXT,
    jobs_total INTEGER, jobs_foreign INTEGER,
    investment_total REAL, investment_borrowed REAL,
    construction_stages TEXT, construction_start TEXT, operation_start TEXT,
    product_nomenclature TEXT, production_description TEXT, object_composition TEXT,
    site_type_free INTEGER DEFAULT 0, site_type_existing INTEGER DEFAULT 0,
    site_area_ha_min REAL, site_area_ha_max REAL,
    site_area_expansion INTEGER DEFAULT 0,
    site_build_area_m2_min REAL, site_build_area_m2_max REAL,
    site_right TEXT, sanitary_zone_m REAL,
    hazard_class TEXT, site_shape TEXT, site_length_min REAL,
    site_width_min REAL, site_other TEXT,
    water_household REAL, water_production REAL, sewage REAL, firefighting REAL,
    electricity_total REAL, electricity_cat1 REAL, electricity_cat2 REAL, electricity_cat3 REAL,
    heat_source TEXT, heat_gcal REAL, gas_m3h REAL, gas_m3y REAL,
    gas_purpose TEXT, heated_area REAL, internet TEXT, phones_qty INTEGER,
    engineering_extra TEXT,
    road_federal_dist REAL, road_regional_dist REAL, road_local_dist REAL,
    road_private_dist REAL, road_extra TEXT,
    railway_needed INTEGER DEFAULT 0, railway_dist REAL, railway_cargo REAL,
    railway_extra TEXT, transport_extra TEXT,
    distance_nn_matters INTEGER DEFAULT 0, distance_nn_max REAL,
    preferred_districts TEXT, location_extra TEXT,
    staff_management INTEGER, staff_workers INTEGER, staff_other INTEGER,
    staff_it INTEGER, staff_admin INTEGER,
    raw_materials TEXT, raw_extra TEXT, additional_info TEXT,
    answer_date TEXT, answer_method TEXT, answer_method_other TEXT,
    answer_notes TEXT, answer_file TEXT, answer_system_number TEXT,
    request_files TEXT,
    edit_reason TEXT, updated_by INTEGER,
    -- #53: цепочка статусов
    registered_at TEXT,
    review_days INTEGER,
    review_deadline TEXT,
    responsible_id INTEGER,
    responsible_not_in_system INTEGER DEFAULT 0,
    responsible_name_external TEXT,
    reviewer_id INTEGER,
    reviewer_not_in_system INTEGER DEFAULT 0,
    reviewer_name_external TEXT,
    reviewer_decision TEXT,
    reviewer_comment TEXT,
    reviewer_decision_at TEXT,
    sent_to_applicant_at TEXT,
    send_method TEXT,
    applicant_feedback TEXT,
    applicant_feedback_at TEXT
);
CREATE TABLE IF NOT EXISTS okved (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL, name TEXT NOT NULL,
    parent_code TEXT, is_active INTEGER DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_okved_code ON okved(code);
CREATE INDEX  IF NOT EXISTS idx_okved_name ON okved(name);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
""")

        # ── subject_types / result_types ─────────────────────────────────────────────
        _migrate_classifiers_tables(conn)

        # ── districts ────────────────────────────────────────────────────────────────
        _migrate_districts_table(conn)

        # ── review_chain ─────────────────────────────────────────────────────────────
        _migrate_review_chain_table(conn)

        # ── Миграция requests ──────────────────────────────────────────────────────
        cols = {r[1] for r in conn.execute("PRAGMA table_info(requests)").fetchall()}
        for col in ['source_type', 'request_files', 'edit_reason', 'updated_by']:
            if col not in cols:
                conn.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT")
        for col in ['applicant_inn', 'applicant_msp_category', 'applicant_okved_main']:
            if col not in cols:
                conn.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT")
        for col, typ in {
            'incoming_number':        'TEXT',
            'answer_system_number':   'TEXT',
            'site_area_ha_min':       'REAL',
            'site_area_ha_max':       'REAL',
            'site_build_area_m2_min': 'REAL',
            'site_build_area_m2_max': 'REAL',
        }.items():
            if col not in cols:
                conn.execute(f"ALTER TABLE requests ADD COLUMN {col} {typ}")
        _migrate_request_cols(conn)  # #53: 16 новых колонок

        # ── Миграция users ────────────────────────────────────────────────────────
        _migrate_users_cols(conn)

        # ── admin ────────────────────────────────────────────────────────────────────────
        if not conn.execute("SELECT id FROM users WHERE username='admin'").fetchone():
            conn.execute(
                "INSERT INTO users (username,password,full_name,role,"
                "can_create,can_edit_others,can_confirm,can_delete,"
                "can_rollback,can_export,can_classifiers,can_users,can_view_all,is_active) "
                "VALUES (?,?,?,?,1,1,1,1,1,1,1,1,1,1)",
                ('admin', hash_pw('admin123'), 'Администратор', 'admin')
            )

        # ── Справочники ───────────────────────────────────────────────────────────────
        if not conn.execute("SELECT id FROM classifiers LIMIT 1").fetchone():
            for v in LEGAL_FORMS_DEFAULT:
                conn.execute("INSERT INTO classifiers (category,value) VALUES ('legal_form',?)", (v,))
            for v in DISTRICTS_DEFAULT:
                conn.execute("INSERT INTO classifiers (category,value) VALUES ('district',?)", (v,))
            for v in SOURCE_TYPES_DEFAULT:
                conn.execute("INSERT INTO classifiers (category,value) VALUES ('source_type',?)", (v,))
        else:
            if not conn.execute(
                "SELECT id FROM classifiers WHERE category='source_type' LIMIT 1"
            ).fetchone():
                for v in SOURCE_TYPES_DEFAULT:
                    conn.execute(
                        "INSERT INTO classifiers (category,value) VALUES ('source_type',?)", (v,)
                    )

        conn.commit()
    finally:
        conn.close()


def migrate_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        # ── subject_types / result_types ─────────────────────────────────────────────
        _migrate_classifiers_tables(conn)

        # ── districts ────────────────────────────────────────────────────────────────
        _migrate_districts_table(conn)

        # ── review_chain ─────────────────────────────────────────────────────────────
        _migrate_review_chain_table(conn)

        # ── requests ──────────────────────────────────────────────────────────────────────────
        cols = {r[1] for r in conn.execute("PRAGMA table_info(requests)").fetchall()}
        for col in ['request_files', 'source_type', 'edit_reason', 'updated_by']:
            if col not in cols:
                conn.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT")
        for col, typ in {
            'incoming_number':        'TEXT',
            'answer_system_number':   'TEXT',
            'site_area_ha_min':       'REAL',
            'site_area_ha_max':       'REAL',
            'site_build_area_m2_min': 'REAL',
            'site_build_area_m2_max': 'REAL',
        }.items():
            if col not in cols:
                conn.execute(f"ALTER TABLE requests ADD COLUMN {col} {typ}")
        _migrate_request_cols(conn)  # #53: 16 новых колонок

        # ── users ───────────────────────────────────────────────────────────────────────
        _migrate_users_cols(conn)

        conn.execute("""
            UPDATE users SET can_create=1, can_export=1, can_view_all=1
            WHERE role='employee' AND can_create=0
        """)

        # ── login_log ─────────────────────────────────────────────────────────────────────
        conn.executescript("""
CREATE TABLE IF NOT EXISTS login_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    username   TEXT,
    event      TEXT NOT NULL,
    ip         TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ll_user  ON login_log(user_id);
CREATE INDEX IF NOT EXISTS idx_ll_event ON login_log(event);
""")

        # ── activity_log ────────────────────────────────────────────────────────────────
        conn.executescript("""
CREATE TABLE IF NOT EXISTS activity_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    action     TEXT NOT NULL,
    request_id INTEGER,
    detail     TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_al_user    ON activity_log(user_id);
CREATE INDEX IF NOT EXISTS idx_al_request ON activity_log(request_id);
CREATE INDEX IF NOT EXISTS idx_al_action  ON activity_log(action);
""")

        # ── phonebook ───────────────────────────────────────────────────────────────────
        conn.executescript("""
CREATE TABLE IF NOT EXISTS phonebook_orgs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE,
    address TEXT
);
CREATE TABLE IF NOT EXISTS phonebook (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    org_id         INTEGER REFERENCES phonebook_orgs(id) ON DELETE SET NULL,
    position       TEXT,
    room           TEXT,
    full_name      TEXT NOT NULL,
    phone_work     TEXT,
    phone_ext      TEXT,
    phone_personal TEXT,
    email          TEXT,
    notes          TEXT
);
CREATE INDEX IF NOT EXISTS idx_pb_org  ON phonebook(org_id);
CREATE INDEX IF NOT EXISTS idx_pb_name ON phonebook(full_name);
""")
        pb_cols = {r[1] for r in conn.execute("PRAGMA table_info(phonebook)").fetchall()}
        if 'source_type' not in pb_cols:
            conn.execute(
                "ALTER TABLE phonebook ADD COLUMN source_type TEXT DEFAULT 'general'"
            )

        conn.commit()
    finally:
        conn.close()


def migrate_districts():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    try:
        existing = set(row[0] for row in conn.execute(
            "SELECT value FROM classifiers WHERE category='district'").fetchall())
        target = set(DISTRICTS_DEFAULT)
        to_delete = existing - target
        if to_delete:
            conn.executemany(
                "DELETE FROM classifiers WHERE category='district' AND value=?",
                [(v,) for v in to_delete])
        to_add = target - existing
        if to_add:
            for i, v in enumerate(DISTRICTS_DEFAULT):
                if v in to_add:
                    conn.execute(
                        "INSERT INTO classifiers (category,value,sort_order) VALUES ('district',?,?)",
                        (v, i))
        conn.commit()
    finally:
        conn.close()
