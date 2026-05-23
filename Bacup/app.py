# ╔══════════════════════════════════════════════════════════════╗
# ║                           app.py                             ║
# ║  Точка входа приложения InvestLand                           ║
# ║  - инициализация БД                                          ║
# ║  - регистрация blueprint-ов                                  ║
# ║  - глобальные переменные для шаблонов                        ║
# ╚══════════════════════════════════════════════════════════════╝

import os
from flask import Flask, session
import sqlite3, os, json
from datetime import datetime, date

# ─── ИМПОРТЫ ИЗ ВНУТРЕННИХ МОДУЛЕЙ ───────────────────────────────────────────

from db import get_db, DB_PATH, BASE_DIR, UPLOADS_DIR, REPORTS_DIR  # БД и пути
from auth_utils import hash_pw                                      # хэш паролей
from changelog import CHANGELOG, ROADMAP                           # журнал версий и roadmap
from spravochnik import LEGAL_FORMS_DEFAULT, DISTRICTS_DEFAULT, SOURCE_TYPES_DEFAULT  # справочники районов

# ─── СОЗДАНИЕ ПРИЛОЖЕНИЯ ─────────────────────────────────────────────────────

app = Flask(__name__)
# ВНИМАНИЕ: в бою ключ должен храниться в переменной окружения
app.secret_key = 'land_nn_2025_secret'


# ─── ИНИЦИАЛИЗАЦИЯ БД И МИГРАЦИИ ─────────────────────────────────────────────

def init_db():
    """
    Первичная инициализация базы данных:
    - создание таблиц (users, requests, classifiers, notifications и др.)
    - создание таблицы okved и settings
    - первичное заполнение справочников (правовые формы, районы, источники)
    - создание администратора admin/admin123 при первом запуске
    """
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
    full_name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'employee',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
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
    user_id INTEGER NOT NULL,
    request_id INTEGER NOT NULL,
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
    source_type TEXT,
    applicant_full_name TEXT, applicant_short_name TEXT, applicant_legal_form TEXT,
    applicant_inn TEXT, applicant_msp_category TEXT, applicant_okved_main TEXT,
    postal_address TEXT, legal_address TEXT, project_name TEXT,
    contact_person TEXT, contact_phone TEXT, contact_email TEXT,
    jobs_total INTEGER, jobs_foreign INTEGER,
    investment_total REAL, investment_borrowed REAL,
    construction_stages TEXT, construction_start TEXT, operation_start TEXT,
    product_nomenclature TEXT, production_description TEXT, object_composition TEXT,
    site_type_free INTEGER DEFAULT 0, site_type_existing INTEGER DEFAULT 0,
    site_area_ha REAL, site_area_expansion INTEGER DEFAULT 0,
    site_build_area_m2 REAL, site_right TEXT, sanitary_zone_m REAL,
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
    answer_notes TEXT, answer_file TEXT,
    request_files TEXT,
    edit_reason TEXT,
    updated_by INTEGER
);
-- Таблица ОКВЭД
CREATE TABLE IF NOT EXISTS okved (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    parent_code TEXT,
    is_active INTEGER DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_okved_code ON okved(code);
CREATE INDEX IF NOT EXISTS idx_okved_name ON okved(name);

-- Таблица настроек/метаданных (для хранения timestamp синхронизаций и др.)
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
""")

    # Текущие столбцы таблицы requests (для миграций схемы)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(requests)").fetchall()]

    # Старые поля (на случай БД, созданной старой версией)
    for col in ['source_type', 'request_files', 'edit_reason', 'updated_by']:
        if col not in cols:
            conn.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT")

    # Новые поля карточки заявителя (ИНН, МСП, ОКВЭД)
    for col in ['applicant_inn', 'applicant_msp_category', 'applicant_okved_main']:
        if col not in cols:
            conn.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT")

    # Первичный админ: admin/admin123
    if not conn.execute("SELECT id FROM users WHERE username='admin'").fetchone():
        conn.execute(
            "INSERT INTO users (username,password,full_name,role) VALUES (?,?,?,?)",
            ('admin', hash_pw('admin123'), 'Администратор', 'admin')
        )

    # Первичное заполнение справочников (если они пустые)
    if not conn.execute("SELECT id FROM classifiers LIMIT 1").fetchone():
        for v in LEGAL_FORMS_DEFAULT:
            conn.execute(
                "INSERT INTO classifiers (category,value) VALUES ('legal_form',?)", (v,)
            )
        for v in DISTRICTS_DEFAULT:
            conn.execute(
                "INSERT INTO classifiers (category,value) VALUES ('district',?)", (v,)
            )
        for v in SOURCE_TYPES_DEFAULT:
            conn.execute(
                "INSERT INTO classifiers (category,value) VALUES ('source_type',?)", (v,)
            )
    else:
        # Если раньше не было источников обращений — добавляем их
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
    """
    Запасная миграция таблицы requests:
    проверяет наличие новых полей и добавляет их при необходимости.
    (Сохранена для обратной совместимости со старыми БД.)
    """
    conn = sqlite3.connect(DB_PATH, timeout=15)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(requests)").fetchall()]
    for col in ['request_files', 'source_type', 'edit_reason', 'updated_by']:
        if col not in cols:
            conn.execute(f"ALTER TABLE requests ADD COLUMN {col} TEXT")
    conn.commit()
    conn.close()


def migrate_districts():
    """
    Синхронизирует справочник 'district' в таблице classifiers с DISTRICTS_DEFAULT.
    - удаляет значения, которых нет в DISTRICTS_DEFAULT;
    - добавляет значения, которых ещё нет в БД;
    - существующие совпадающие значения не трогает.
    """
    conn = sqlite3.connect(DB_PATH, timeout=15)
    try:
        # Текущие значения в БД
        existing = set(
            row[0] for row in conn.execute(
                "SELECT value FROM classifiers WHERE category='district'"
            ).fetchall()
        )

        target = set(DISTRICTS_DEFAULT)

        # Удаляем устаревшие
        to_delete = existing - target
        if to_delete:
            conn.executemany(
                "DELETE FROM classifiers WHERE category='district' AND value=?",
                [(v,) for v in to_delete]
            )

        # Добавляем новые (в порядке из DISTRICTS_DEFAULT)
        to_add = target - existing
        if to_add:
            for i, v in enumerate(DISTRICTS_DEFAULT):
                if v in to_add:
                    conn.execute(
                        "INSERT INTO classifiers (category, value, sort_order) "
                        "VALUES ('district', ?, ?)",
                        (v, i)
                    )

        conn.commit()
    finally:
        conn.close()


# ─── ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ДЛЯ ШАБЛОНОВ ─────────────────────────────────────

@app.context_processor
def inject_globals():
    """
    Глобальные переменные для всех шаблонов:
    - текущая версия приложения (из CHANGELOG),
    - название и подзаголовок приложения,
    - количество непрочитанных уведомлений,
    - список пользователей для impersonation (для админа).
    """
    users_for_impersonate = []
    unread_count = 0

    if session.get('user_id'):
        db = get_db()
        unread_count = db.execute(
            'SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0',
            (session['user_id'],)
        ).fetchone()[0]

        if session.get('role') == 'admin':
            users_for_impersonate = db.execute(
                'SELECT id, full_name, role FROM users '
                'WHERE id != ? ORDER BY full_name',
                (session.get('user_id', 0),)
            ).fetchall()
        db.close()

    return dict(
        app_version=CHANGELOG[0]['version'] if CHANGELOG else '—',
        app_name='InvestLand',
        app_subtitle='Инвестиционный земельный модуль Нижегородской области',
        unread_count=unread_count,
        users_for_impersonate=users_for_impersonate,
    )
    
# ─── РЕГИСТРАЦИЯ BLUEPRINT-ОВ ────────────────────────────────────────────────

from login_routes   import auth_bp       # маршруты авторизации и логина
from request_routes import requests_bp   # обращения (форма, список, карточка)
from admin_routes   import admin_bp      # пользователи, справочники, админка
from export_routes  import report_bp     # экспорт и отчёты (Excel и т.п.)
from info_routes    import misc_bp       # уведомления и changelog
from okved_admin    import okved_bp      # админка ОКВЭД (справочник, синхронизация)
from okved_api      import okved_api_bp  # API автодополнения ОКВЭД

app.register_blueprint(okved_bp)
app.register_blueprint(okved_api_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(requests_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(report_bp)
app.register_blueprint(misc_bp)


# ─── ЗАПУСК ПРИЛОЖЕНИЯ ──────────────────────────────────────────────────────

if __name__ == '__main__':
    # Создание/обновление структуры БД
    init_db()
    migrate_db()
    migrate_districts()

    app_debug = os.getenv('APP_DEBUG', '0')
    debug_flag = app_debug == '1'

    print(f"Starting Flask with debug={debug_flag}, FLASK_ENV={os.getenv('FLASK_ENV', '')}")

    app.run(
        host='0.0.0.0',
        port=5000,
        debug=debug_flag,
        use_reloader=debug_flag
    )