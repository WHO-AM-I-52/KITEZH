# ╔══════════════════════════════════════════════════════════════╗
# ║ app.py                                                       ║
# ║ v2.5: todatetime Jinja-фильтр + статусы inject_globals (#53)  ║
# ╚══════════════════════════════════════════════════════════════╝

import os
import sqlite3
from datetime import timedelta, datetime, date

from flask import Flask, session

from db import get_db, DB_PATH, BASE_DIR
from auth_utils import hash_pw
from changelog import CHANGELOG
from spravochnik import LEGAL_FORMS_DEFAULT, DISTRICTS_DEFAULT, SOURCE_TYPES_DEFAULT

app = Flask(__name__)

# ─── SECRET_KEY ───────────────────────────────────────────────────────────
import secrets as _secrets
_KEY_FILE = os.path.join(BASE_DIR, '_secret.key')
_env_key  = os.environ.get('SECRET_KEY')
if _env_key:
    app.secret_key = _env_key
else:
    if os.path.exists(_KEY_FILE):
        app.secret_key = open(_KEY_FILE, 'r').read().strip()
    else:
        _new_key = _secrets.token_hex(32)
        try:
            with open(_KEY_FILE, 'w') as _f:
                _f.write(_new_key)
        except Exception:
            pass
        app.secret_key = _new_key

# ─── Настройки сессий ──────────────────────────────────────────────────────
app.config['PERMANENT_SESSION_LIFETIME']  = timedelta(minutes=15)
app.config['SESSION_REFRESH_EACH_REQUEST'] = True

# ─── JINJA ФИЛЬТРЫ (#53) ───────────────────────────────────────────────────
@app.template_filter('todatetime')
def todatetime_filter(value):
    """
    Преобразует 'YYYY-MM-DD' в datetime.date.
    Используется в view.html для вычисления deadline_diff.
    При ошибке парсинга — возвращает текущую дату (deadline_diff = 0).
    """
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.strptime(str(value).strip()[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return date.today()


# ─── Blueprints (ранние: без зависимостей от DB) ──────────────────────────
from phonebook_routes import phonebook_bp
from search_routes    import search_bp
app.register_blueprint(phonebook_bp)
app.register_blueprint(search_bp)


def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
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
    can_classifiers      INTEGER DEFAULT 0,
    can_users            INTEGER DEFAULT 0,
    can_view_all         INTEGER DEFAULT 0,
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
    edit_reason TEXT, updated_by INTEGER
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

    # ── Миграция requests ───────────────────────────────────────────────────
    cols = [r[1] for r in conn.execute("PRAGMA table_info(requests)").fetchall()]
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

    # ── Миграция users ─────────────────────────────────────────────────────
    user_cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    for col in ['can_create', 'can_edit_others', 'can_confirm', 'can_delete',
                'can_rollback', 'can_export', 'can_classifiers', 'can_users',
                'can_view_all']:
        if col not in user_cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT 0")
    if 'must_change_password' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER DEFAULT 0")
    for col, definition in [
        ('email',               'TEXT DEFAULT NULL'),
        ('theme',               "TEXT DEFAULT 'light'"),
        ('email_notifications', 'INTEGER DEFAULT 0'),
        ('is_active',           'INTEGER NOT NULL DEFAULT 1'),  # #53
    ]:
        if col not in user_cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")

    # ── Создание admin если нет ──────────────────────────────────────────
    if not conn.execute("SELECT id FROM users WHERE username='admin'").fetchone():
        conn.execute(
            "INSERT INTO users (username,password,full_name,role,"
            "can_create,can_edit_others,can_confirm,can_delete,"
            "can_rollback,can_export,can_classifiers,can_users,can_view_all,is_active) "
            "VALUES (?,?,?,?,1,1,1,1,1,1,1,1,1,1)",
            ('admin', hash_pw('admin123'), 'Администратор', 'admin')
        )

    # ── Справочники ───────────────────────────────────────────────────────
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
    conn.close()


def migrate_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row

    # ── requests ───────────────────────────────────────────────────────────────
    cols = [r[1] for r in conn.execute("PRAGMA table_info(requests)").fetchall()]
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

    # ── users (v2.0 + #53 is_active) ───────────────────────────────────────────
    user_cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
    for col in ['can_create', 'can_edit_others', 'can_confirm', 'can_delete',
                'can_rollback', 'can_export', 'can_classifiers', 'can_users',
                'can_view_all']:
        if col not in user_cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT 0")
    if 'must_change_password' not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN must_change_password INTEGER DEFAULT 0")
    for col, definition in [
        ('email',               'TEXT DEFAULT NULL'),
        ('theme',               "TEXT DEFAULT 'light'"),
        ('email_notifications', 'INTEGER DEFAULT 0'),
        ('is_active',           'INTEGER NOT NULL DEFAULT 1'),  # #53
    ]:
        if col not in user_cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")

    # ── login_log (v2.0) ──────────────────────────────────────────────────────────
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

    conn.execute("""
        UPDATE users SET can_create=1, can_export=1, can_view_all=1
        WHERE role='employee' AND can_create=0
    """)

    # ── activity_log (v2.0) ─────────────────────────────────────────────────────
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

    # ── phonebook_orgs (v2.1.0) ─────────────────────────────────────────────────
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

    # ── phonebook source_type (v2.3) ──────────────────────────────────────────
    pb_cols = [r[1] for r in conn.execute("PRAGMA table_info(phonebook)").fetchall()]
    if 'source_type' not in pb_cols:
        conn.execute(
            "ALTER TABLE phonebook ADD COLUMN source_type TEXT DEFAULT 'general'"
        )

    conn.commit()
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


@app.context_processor
def inject_globals():
    users_for_impersonate = []
    unread_count = 0
    active_requests_count = 0

    if session.get('user_id'):
        db = get_db()

        unread_count = db.execute(
            'SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0',
            (session['user_id'],)
        ).fetchone()[0]

        try:
            # #53: активные = всё кроме closed и draft
            if session.get('role') == 'admin' or session.get('can_view_all'):
                active_requests_count = db.execute(
                    "SELECT COUNT(*) FROM requests "
                    "WHERE status NOT IN ('closed', 'draft')"
                ).fetchone()[0]
            else:
                active_requests_count = db.execute(
                    "SELECT COUNT(*) FROM requests "
                    "WHERE status NOT IN ('closed', 'draft') AND created_by=?",
                    (session['user_id'],)
                ).fetchone()[0]
        except Exception:
            active_requests_count = 0

        if session.get('role') == 'admin':
            users_for_impersonate = db.execute(
                'SELECT id,full_name,role FROM users WHERE id!=? ORDER BY full_name',
                (session.get('user_id', 0),)
            ).fetchall()
        db.close()

    from auth_utils import ALL_PERMISSIONS, get_user_perm
    perms = {key: get_user_perm(key) for key in ALL_PERMISSIONS}

    return dict(
        app_version=CHANGELOG[0]['version'] if CHANGELOG else '—',
        app_name='InvestLand',
        app_subtitle='Инвестиционный земельный модуль Нижегородской области',
        unread_count=unread_count,
        active_requests_count=active_requests_count,
        users_for_impersonate=users_for_impersonate,
        perms=perms,
        ALL_PERMISSIONS=ALL_PERMISSIONS,
    )


from login_routes      import auth_bp
from request_routes    import requests_bp
from admin_routes      import admin_bp
from export_routes     import report_bp
from info_routes       import misc_bp
from okved_admin       import okved_bp
from okved_api         import okved_api_bp
from egrul_api         import egrul_api_bp
from settings_routes   import settings_bp
from preview_routes    import preview_bp
from phonebook_import  import pb_import_bp
from investmap_routes  import investmap_bp

app.register_blueprint(okved_bp)
app.register_blueprint(okved_api_bp)
app.register_blueprint(egrul_api_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(requests_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(report_bp)
app.register_blueprint(misc_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(preview_bp)
app.register_blueprint(pb_import_bp)
app.register_blueprint(investmap_bp)

if __name__ == '__main__':
    init_db()
    migrate_db()
    migrate_districts()

    app_debug  = os.getenv('APP_DEBUG', '0')
    debug_flag = app_debug == '1'
    print(f"Starting Flask with debug={debug_flag}, FLASK_ENV={os.getenv('FLASK_ENV', '')}")
    app.run(host='0.0.0.0', port=5000, debug=debug_flag, use_reloader=debug_flag)
