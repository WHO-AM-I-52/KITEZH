# ╔══════════════════════════════════════════════════════════════╗
# ║ migrations.py                                                ║
# ║ Инициализация и миграция БД (вынесено из app.py)             ║
# ║ +can_view_phonebook, can_view_investmap,                     ║
# ║  can_export_full, can_import_full, can_investmap_rules       ║
# ║ feat #95 — form_sections, subjects.sections_config,         ║
# ║            requests.appeal_text                              ║
# ╚══════════════════════════════════════════════════════════════╝

import sqlite3
import json

from db import DB_PATH
from core.auth_utils import hash_pw
from spravochnik import LEGAL_FORMS_DEFAULT, DISTRICTS_DEFAULT, SOURCE_TYPES_DEFAULT
from db import get_db
from services.investmap_rf_sync_migrations import (
    migrate_sync_plan_tables as _migrate_investmap_rf_sync_plan_tables,
    migrate_sync_retry_tables as _migrate_investmap_rf_sync_retry_tables,
)
from portal_analysis.analysis_history import create_analysis_history_tables
from services.investmap_data_updates_migrations import (
    migrate_investmap_data_updates,
)

# ──────────────────────────────────────────────────────────────────────────────
# НОВЫЕ КОЛОНКИ requests (#53)
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
    cols = {r[1] for r in conn.execute("PRAGMA table_info(requests)").fetchall()}
    for col, typ in _NEW_REQUEST_COLS:
        if col not in cols:
            conn.execute(f"ALTER TABLE requests ADD COLUMN {col} {typ}")


def _migrate_users_cols(conn):
    user_cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    for col in [
        'can_create', 'can_edit_others', 'can_confirm', 'can_delete',
        'can_rollback', 'can_export', 'can_export_full', 'can_import_full',
        'can_classifiers', 'can_users', 'can_view_all',
        'can_view_investmap', 'can_view_investmap_dashboard',
        'can_view_investmap_analysis',
        'can_view_phonebook', 'can_investmap_rules',
        'can_refresh_investmap_rf_cards',
        'can_view_investmap_updates',
        'can_manage_investmap_updates',
        'can_view_contracts', 'can_edit_contracts',
        'can_view_all_contracts', 'can_manage_contracts',
        'can_contract_payments', 'can_contract_settings',
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
    conn.executescript("""
CREATE TABLE IF NOT EXISTS districts (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    name      TEXT NOT NULL UNIQUE,
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER DEFAULT 0
);
""")
    if not conn.execute("SELECT id FROM districts LIMIT 1").fetchone():
        for i, name in enumerate(DISTRICTS_DEFAULT):
            conn.execute(
                "INSERT OR IGNORE INTO districts (name, is_active, sort_order) VALUES (?,1,?)",
                (name, i)
            )


def _migrate_review_chain_table(conn):
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


def _migrate_tasks_tables(conn):
    """Создаёт таблицы tasks и task_assignees если их нет."""
    conn.executescript("""
CREATE TABLE IF NOT EXISTS tasks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    title            TEXT NOT NULL,
    description      TEXT,
    source           TEXT,
    deadline         TEXT,
    status           TEXT NOT NULL DEFAULT 'new',
    result           TEXT,
    created_by       INTEGER NOT NULL,
    created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
    closed_at        TEXT,
    revision_comment TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status     ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created_by ON tasks(created_by);
CREATE TABLE IF NOT EXISTS task_assignees (
    task_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    assigned_at TEXT DEFAULT CURRENT_TIMESTAMP,
    assigned_by INTEGER,
    PRIMARY KEY (task_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_ta_task ON task_assignees(task_id);
CREATE INDEX IF NOT EXISTS idx_ta_user ON task_assignees(user_id);
""")
    task_cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if 'revision_comment' not in task_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN revision_comment TEXT")


def _migrate_task_comments_table(conn):
    """Создаёт таблицу task_comments для истории и комментариев к задачам.

    event_type:
        'comment'        — обычный комментарий пользователя
        'status_change'  — автособытие при смене статуса
        'review_submit'  — итоговый комментарий при переводе на проверку
        'revision'       — комментарий при отправке на доработку
    """
    conn.executescript("""
CREATE TABLE IF NOT EXISTS task_comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    event_type TEXT    NOT NULL DEFAULT 'comment',
    body       TEXT,
    file_path  TEXT,
    created_at TEXT    DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_tc_task       ON task_comments(task_id);
CREATE INDEX IF NOT EXISTS idx_tc_user       ON task_comments(user_id);
CREATE INDEX IF NOT EXISTS idx_tc_event_type ON task_comments(event_type);
""")


def _migrate_suggestions_table(conn):
    """Создаёт таблицу suggestions — предложения пользователей по улучшению.

    status:
        'new'         — новое предложение (по умолчанию)
        'in_roadmap'  — принято в дорожную карту администратором
        'rejected'    — отклонено администратором
    """
    conn.executescript("""
CREATE TABLE IF NOT EXISTS suggestions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    comment     TEXT    NOT NULL,
    file_path   TEXT,
    status      TEXT    NOT NULL DEFAULT 'new',
    created_at  TEXT    DEFAULT CURRENT_TIMESTAMP,
    reviewed_by INTEGER,
    reviewed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sg_user   ON suggestions(user_id);
CREATE INDEX IF NOT EXISTS idx_sg_status ON suggestions(status);
""")


def _migrate_contracts_tables(conn):
    """Создаёт таблицы раздела «Договоры» если их нет.

    Дизайн:
    - contracts: карточка договора со снимками реквизитов заказчика,
      условиями оплаты/сроков, стадией и состоянием;
    - contract_tariff_editions / contract_tariff_items: редакции приказа
      и позиции тарифа (первая редакция - 81/26/КРНО от 03.06.2026);
    - contract_services: строки услуг со снимком цены;
    - contract_stage_log: история стадий (дата события и внесения раздельно);
    - contract_files: версии файлов;
    - contract_activities / contract_payments / contract_reviews /
      contract_tasks / contract_settings / work_calendar.
    Суммы хранятся в копейках (INTEGER).
    """
    conn.executescript("""
CREATE TABLE IF NOT EXISTS contract_tariff_editions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    doc_date   TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS contract_tariff_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    edition_id   INTEGER NOT NULL REFERENCES contract_tariff_editions(id),
    code         TEXT NOT NULL,
    section      TEXT,
    name         TEXT NOT NULL,
    unit         TEXT,
    price_kop    INTEGER NOT NULL,
    duration_days INTEGER,
    duration_type TEXT DEFAULT 'work',
    per_area     INTEGER NOT NULL DEFAULT 0,
    template_family TEXT,
    sort_order   INTEGER DEFAULT 0,
    is_active    INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cti_edition_code
    ON contract_tariff_items(edition_id, code);
CREATE TABLE IF NOT EXISTS contract_object_types (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id  INTEGER REFERENCES contract_object_types(id),
    name       TEXT NOT NULL,
    sort_order INTEGER DEFAULT 0,
    is_active  INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS contracts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    inner_number      TEXT UNIQUE,
    reg_number        TEXT,
    reg_date          TEXT,
    is_free          INTEGER NOT NULL DEFAULT 0,
    free_basis       TEXT,
    free_basis_date  TEXT,
    origin           TEXT NOT NULL DEFAULT 'new',
    customer_type    TEXT NOT NULL DEFAULT 'legal',
    customer_full_name TEXT,
    customer_short_name TEXT,
    customer_opf      TEXT,
    customer_inn      TEXT,
    customer_ogrn     TEXT,
    customer_kpp      TEXT,
    customer_address  TEXT,
    signatory_name   TEXT,
    signatory_position TEXT,
    signatory_basis  TEXT,
    contact_name     TEXT,
    contact_position TEXT,
    contact_phone    TEXT,
    contact_email    TEXT,
    directory_org_id INTEGER REFERENCES phonebook_orgs(id) ON DELETE SET NULL,
    directory_contact_id INTEGER REFERENCES phonebook(id) ON DELETE SET NULL,
    project_name     TEXT,
    object_description TEXT,
    object_type_id   INTEGER REFERENCES contract_object_types(id),
    address          TEXT,
    district_id      INTEGER REFERENCES districts(id),
    cadastral_numbers TEXT,
    area_ha          TEXT,
    executor_id      INTEGER NOT NULL REFERENCES users(id),
    stage            TEXT NOT NULL DEFAULT 'draft',
    state            TEXT NOT NULL DEFAULT 'active',
    payment_scheme   TEXT,
    payment_days     INTEGER,
    payment_day_type TEXT DEFAULT 'work',
    payment_event    TEXT,
    duration_days    INTEGER,
    duration_day_type TEXT DEFAULT 'work',
    work_start_basis TEXT,
    act_sign_method TEXT,
    incoming_docs_ok  INTEGER DEFAULT 0,
    incoming_docs_date TEXT,
    total_kop        INTEGER NOT NULL DEFAULT 0,
    vat_percent      TEXT DEFAULT '22',
    created_by       INTEGER NOT NULL REFERENCES users(id),
    created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_contracts_stage ON contracts(stage);
CREATE INDEX IF NOT EXISTS idx_contracts_executor ON contracts(executor_id);
CREATE INDEX IF NOT EXISTS idx_contracts_customer ON contracts(customer_full_name);
CREATE TABLE IF NOT EXISTS contract_services (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id  INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    tariff_item_id INTEGER REFERENCES contract_tariff_items(id),
    edition_code TEXT,
    code         TEXT,
    name         TEXT,
    qty          TEXT,
    area_ha      TEXT,
    price_kop    INTEGER,
    amount_kop   INTEGER,
    manual_edit  INTEGER DEFAULT 0,
    manual_reason TEXT,
    sort_order   INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cs_contract ON contract_services(contract_id);
CREATE TABLE IF NOT EXISTS contract_stage_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id  INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    from_stage   TEXT,
    to_stage     TEXT,
    event_date   TEXT,
    entered_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    user_id      INTEGER,
    comment      TEXT
);
CREATE INDEX IF NOT EXISTS idx_csl_contract ON contract_stage_log(contract_id);
CREATE TABLE IF NOT EXISTS contract_files (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id  INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    category     TEXT NOT NULL,
    stage        TEXT,
    version      INTEGER NOT NULL DEFAULT 1,
    is_current   INTEGER NOT NULL DEFAULT 1,
    orig_name    TEXT,
    stored_name  TEXT,
    user_id      INTEGER,
    sha256       TEXT,
    size_bytes   INTEGER,
    comment      TEXT,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cf_contract ON contract_files(contract_id);
CREATE INDEX IF NOT EXISTS idx_cf_current ON contract_files(contract_id, is_current);
CREATE TABLE IF NOT EXISTS contract_activities (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id  INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    assignee     TEXT,
    assignee_user_id INTEGER REFERENCES users(id),
    assigned_at  TEXT,
    deadline     TEXT,
    status       TEXT DEFAULT 'new',
    result       TEXT,
    note_file_id INTEGER REFERENCES contract_files(id),
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ca_contract ON contract_activities(contract_id);
CREATE TABLE IF NOT EXISTS contract_payments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id  INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL DEFAULT 'income',
    number       TEXT,
    pay_date     TEXT,
    amount_kop   INTEGER NOT NULL,
    note         TEXT,
    file_id      INTEGER REFERENCES contract_files(id),
    user_id      INTEGER,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cp_contract ON contract_payments(contract_id);
CREATE TABLE IF NOT EXISTS contract_reviews (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id   INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    body          TEXT NOT NULL,
    request_no    TEXT,
    sent_date     TEXT,
    accepted_date TEXT,
    decision_date TEXT,
    result        TEXT,
    comment       TEXT,
    file_id       INTEGER REFERENCES contract_files(id),
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_crv_contract ON contract_reviews(contract_id);
CREATE TABLE IF NOT EXISTS contract_tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id  INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    task_id      INTEGER NOT NULL REFERENCES tasks(id),
    stage        TEXT,
    kind         TEXT DEFAULT 'stage',
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    closed_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ct_contract ON contract_tasks(contract_id);
CREATE INDEX IF NOT EXISTS idx_ct_task ON contract_tasks(task_id);
CREATE TABLE IF NOT EXISTS contract_settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS work_calendar (
    day  TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    note TEXT
);
""")

    # Первая редакция тарифов — приказ 81/26/КРНО от 03.06.2026.
    if not conn.execute(
        "SELECT id FROM contract_tariff_editions LIMIT 1"
    ).fetchone():
        conn.execute(
            "INSERT INTO contract_tariff_editions (code, name, doc_date)"
            " VALUES ('81-26-KRNO', 'Приказ 81/26/КРНО от 03.06.2026',"
            " '2026-06-03')"
        )
        edition_id = conn.execute(
            "SELECT id FROM contract_tariff_editions"
            " WHERE code='81-26-KRNO'"
        ).fetchone()[0]
        tariff_rows = [
            ('1.1',  'Подготовка комплексной градостроительной справки земельного участка', 'га', 2000000, None, None, 1, 'council', 10),
            ('2.1',  'Подготовка пакета документов для предоставления земельного участка на Совете', 'комплект', 3000000, None, None, 0, 'council', 20),
            ('2.2',  'Подготовка выкопировки земельного участка', 'комплект', 4000000, None, None, 0, 'council', 30),
            ('2.3',  'Подготовка расчета нагрузок для сбора технических условий', 'комплект', 1500000, None, None, 0, 'council', 40),
            ('2.4',  'Подготовка архитектурного решения', 'комплект', 6000000, None, None, 0, 'council', 50),
            ('2.5',  'Подготовка бизнес-плана', 'комплект', 1500000, None, None, 0, 'council', 60),
            ('2.6',  'Полный пакет документов (2.1-2.5), справочная позиция', 'комплект', 16000000, None, None, 0, 'council', 70),
            ('3.1',  'Заполнение ходатайства и направление документов через ГИСОГД НО', 'комплект', 500000, None, None, 0, 'council', 80),
            ('4.1',  'Полный пакет документов для предоставления земельного участка на аукционе', 'комплект', 16000000, None, None, 0, 'auction', 90),
            ('5.1',  'Информация об условиях технологического присоединения к сетям газораспределения', 'комплект', 6500000, None, None, 0, 'tech', 100),
            ('5.2',  'Информация об условиях технологического присоединения к электрическим сетям', 'комплект', 2100000, None, None, 0, 'tech', 110),
            ('6.1',  'Подготовка заявления для получения разрешения на размещение объекта (ПП РФ 1300)', 'комплект', 500000, None, None, 0, 'placement', 120),
            ('6.2',  'Подготовка схемы границ земель с координатами (размещение объекта)', 'комплект', 4000000, None, None, 0, 'placement', 130),
            ('7.1',  'Заявление и графический материал: изменения в генеральный план (КЗЗ)', 'комплект', 800000, None, None, 0, 'kzz', 140),
            ('7.2',  'Заявление и графический материал: изменения в ПЗЗ (КЗЗ)', 'комплект', 800000, None, None, 0, 'kzz', 150),
            ('7.3',  'Заявление: условно-разрешённый вид использования (КЗЗ)', 'комплект', 700000, None, None, 0, 'kzz', 160),
            ('8.1',  'Подготовка и получение заданий на инженерные изыскания и ДПТ', 'комплект', 1600000, None, None, 0, 'dpt', 170),
        ]
        conn.executemany(
            """INSERT INTO contract_tariff_items
               (edition_id, code, section, name, unit, price_kop,
                per_area, template_family, sort_order)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [(edition_id, c, '', n, u, p, pa, fam, so)
             for (c, n, u, p, dd, dt, pa, fam, so) in tariff_rows]
        )

    # Начальные типы объектов (родитель -> подтип).
    if not conn.execute(
        "SELECT id FROM contract_object_types LIMIT 1"
    ).fetchone():
        roots = [
            ('Социально-культурного и коммунально-бытового назначения',
             ['Образование', 'Здравоохранение', 'Культура',
              'Физкультура и спорт', 'Социальное обслуживание',
              'Коммунальное хозяйство', 'Бытовое обслуживание']),
            ('Туристический и рекреационный объект', []),
            ('Промышленный объект',
             ['Обрабатывающее производство',
              'Переработка сельхозпродукции',
              'Логистика и склады', 'Энергетика',
              'Индустриальный парк / промплощадка']),
            ('Сельскохозяйственный объект', []),
            ('Торговый и сервисный объект', []),
            ('Жилищное строительство', []),
            ('Иное', []),
        ]
        for si, (root, subs) in enumerate(roots):
            cur = conn.execute(
                "INSERT INTO contract_object_types (name, sort_order)"
                " VALUES (?, ?)", (root, si)
            )
            root_id = cur.lastrowid
            for sj, sub in enumerate(subs):
                conn.execute(
                    "INSERT INTO contract_object_types"
                    " (parent_id, name, sort_order) VALUES (?, ?, ?)",
                    (root_id, sub, sj)
                )


def _migrate_letters_tables(conn):
    """Создаёт таблицы letters/letter_tags/letter_tag_links.
    Порядок: сначала CREATE TABLE (без executor_id),
    потом ALTER TABLE добавляет executor_id если её нет,
    затем создаётся индекс (колонка уже точно есть).
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS letters (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT NOT NULL,
            number     TEXT,
            subject    TEXT,
            note       TEXT,
            created_by INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
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
            letter_id INTEGER NOT NULL,
            tag_id    INTEGER NOT NULL,
            PRIMARY KEY (letter_id, tag_id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_letters_date ON letters(date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ltl_letter ON letter_tag_links(letter_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ltl_tag ON letter_tag_links(tag_id)"
    )

    letter_cols = {r[1] for r in conn.execute("PRAGMA table_info(letters)").fetchall()}
    if 'executor_id' not in letter_cols:
        conn.execute("ALTER TABLE letters ADD COLUMN executor_id INTEGER")

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_letters_executor ON letters(executor_id)"
    )


def _migrate_phonebook_orgs_inn(conn):
    """Добавляет поле inn в phonebook_orgs (fix #sync-fix-3).

    Требуется для дедупликации организаций по ИНН в sync_request_to_phonebook().
    Частичный уникальный индекс (WHERE inn IS NOT NULL) позволяет хранить
    несколько записей без ИНН не нарушая ограничение уникальности.
    """
    # 1. Создаём таблицу если ещё нет (свежая установка)
    conn.executescript("""
CREATE TABLE IF NOT EXISTS phonebook_orgs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT NOT NULL UNIQUE,
    address TEXT,
    inn     TEXT
);
""")
    # 2. Добавляем колонку если таблица уже была (обновление)
    pbo_cols = {r[1] for r in conn.execute("PRAGMA table_info(phonebook_orgs)").fetchall()}
    if 'inn' not in pbo_cols:
        conn.execute("ALTER TABLE phonebook_orgs ADD COLUMN inn TEXT")

    # 3. Частичный уникальный индекс: NULL не конкурирует между собой
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_pbo_inn
        ON phonebook_orgs(inn)
        WHERE inn IS NOT NULL
    """)


def _migrate_investmap_tables(conn):
    conn.executescript("""
CREATE TABLE IF NOT EXISTS investmap_classifiers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    classifier_num INTEGER NOT NULL,
    field_name     TEXT NOT NULL,
    value          TEXT NOT NULL,
    sort_order     INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS investmap_fields (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    tech_name          TEXT NOT NULL UNIQUE,
    display_name       TEXT NOT NULL,
    data_type          TEXT NOT NULL DEFAULT 'text',
    classifier_num     INTEGER,
    is_required        INTEGER NOT NULL DEFAULT 0,
    required_condition TEXT
);
CREATE TABLE IF NOT EXISTS investmap_rules (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source_field     TEXT NOT NULL,
    source_value     TEXT NOT NULL,
    target_field     TEXT NOT NULL,
    recommended_text TEXT NOT NULL
);
""")
    fields = [
        ('global_id', 'global_id', 'integer', None, 0, None),
        ('system_object_id', 'Код во внешнем источнике', 'text', None, 0, None),
        ('site_status_MAPS', 'Статус площадки', 'select', 606, 1, None),
        ('method_of_sale_MAPS', 'Способ продажи', 'select', 607, 0, 'site_status_MAPS=Продана'),
        ('other_method_of_sale_MAPS', 'Другой способ', 'text', None, 0, 'method_of_sale_MAPS=Другой способ'),
        ('transaction_information_MAPS', 'Информация о сделке', 'select', 608, 0, 'site_status_MAPS=Продана OR Предоставлена в аренду'),
        ('area_sold_site_MAPS', 'Площадь проданной площадки, га / кв.м', 'number', None, 0, 'transaction_information_MAPS=Продана часть площадки'),
        ('sales_price_MAPS', 'Стоимость продажи, рублей', 'number', None, 0, 'transaction_information_MAPS=Продана часть площадки OR Продана целиком'),
        ('investment_volume_for_project_MAPS', 'Объем инвестиций по проекту, рублей', 'number', None, 0, None),
        ('form_use_remaining_area_MAPS', 'Форма использования оставшейся площади', 'select', 609, 0, 'transaction_information_MAPS=Продана часть площадки'),
        ('other_form_use_remaining_area_MAPS', 'Другая форма использования', 'text', None, 0, 'form_use_remaining_area_MAPS=Другое'),
        ('area_rented_site_MAPS', 'Площадь арендованной площадки, га / кв.м', 'number', None, 0, 'transaction_information_MAPS=Сдана в аренду часть площадки'),
        ('rent_term_MAPS', 'Срок аренды, лет', 'integer', None, 0, 'transaction_information_MAPS=Сдана в аренду часть площадки'),
        ('rental_cost_MAPS', 'Стоимость аренды, рублей в месяц', 'number', None, 0, 'transaction_information_MAPS=Сдана в аренду целиком OR Сдана в аренду часть площадки'),
        ('project_cost_MAPS', 'Стоимость проекта, рублей', 'number', None, 0, None),
        ('used_for_other_purposes_MAPS', 'Использована для других целей', 'select', 610, 0, 'site_status_MAPS=Использована для других целей'),
        ('other_used_for_other_purposes_MAPS', 'Другая цель использования', 'text', None, 0, 'used_for_other_purposes_MAPS=Другое'),
        ('reasons_withdrawal_from_sale_MAPS', 'Причины снятия с реализации', 'select', 611, 0, 'site_status_MAPS=Снята с реализации'),
        ('other_reasons_withdrawal_from_sale_MAPS', 'Другая причина снятия', 'text', None, 0, 'reasons_withdrawal_from_sale_MAPS=Другое'),
        ('site_name_MAPS', 'Название площадки', 'text', None, 1, None),
        ('preferential_treatment_levels_MAPS', 'Преференциальный режим', 'select', 527, 1, None),
        ('preferential_business_link_MAPS', 'Наименование объекта преференциального режима', 'link', None, 0, 'preferential_treatment_levels_MAPS!=Отсутствует'),
        ('support_infrastructure_object_MAPS', 'Объект инфраструктуры поддержки', 'select', 467, 1, None),
        ('support_infrastructure_link_MAPS', 'Наименование объекта инфраструктуры поддержки', 'link', None, 0, 'support_infrastructure_object_MAPS!=Без льгот'),
        ('GISIP_link_MAPS', 'Индустриальные парки и промышленные технопарки из ГИСИП', 'link', None, 0, 'support_infrastructure_object_MAPS=Технопарк OR Индустриальный парк'),
        ('region_dict_MAPS', 'Регион', 'select', 520, 1, None),
        ('municipality_string_MAPS', 'Муниципальное образование', 'text', None, 1, None),
        ('adress_object_MAPS', 'Адрес объекта', 'text', None, 1, None),
        ('nearest_city_MAPS', 'Ближайший город', 'text', None, 1, None),
        ('format_site_MAPS', 'Формат площадки', 'select', 405, 1, None),
        ('type_site_MAPS', 'Тип площадки', 'select', 406, 1, None),
        ('priority_site_dict_MAPS', 'Приоритетная площадка', 'select', 602, 0, None),
        ('form_ownership_MAPS', 'Форма собственности объекта', 'select', 413, 1, None),
        ('transaction_form_MAPS', 'Форма сделки', 'select', 408, 1, None),
        ('cost_object_MAPS', 'Стоимость объекта, руб. (покупки или месячной аренды)', 'number', None, 0, None),
        ('cost_per_ha_MAPS', 'Стоимость, руб./год за га', 'number', None, 0, None),
        ('cost_per_sq_m_MAPS', 'Стоимость, руб./год за кв.м.', 'number', None, 0, None),
        ('max_min_rental_year_MAPS', 'min и max сроки аренды, лет', 'text', None, 0, 'transaction_form_MAPS=Аренда OR Аренда через аукцион'),
        ('procedure_determining_cost_str_MAPS', 'Порядок определения стоимости', 'text', None, 0, None),
        ('hazard_class_object_MAPS', 'Класс опасности объекта', 'select', 409, 0, None),
        ('characteristics_capital_buildings_MAPS', 'Характеристики объектов капитального строительства', 'text', None, 0, None),
        ('land_area_MAPS', 'Свободная площадь ЗУ, га', 'number', None, 0, 'format_site_MAPS=Земельный участок'),
        ('cadastral_land_number_MAPS', 'Кадастровый номер ЗУ', 'text', None, 0, 'format_site_MAPS=Земельный участок'),
        ('types_authorized_use_MAPS', 'Варианты разрешенного использования', 'select', 415, 0, 'format_site_MAPS=Земельный участок'),
        ('land_surveying_dic_MAPS', 'Межевание ЗУ', 'select', 518, 0, 'format_site_MAPS=Земельный участок'),
        ('land_category_MAPS', 'Категория земель', 'select', 426, 0, 'format_site_MAPS=Земельный участок'),
        ('area_property_complex_MAPS', 'Свободная площадь здания/сооружения/помещения, кв.м', 'number', None, 0, 'format_site_MAPS=Помещение OR Здания и сооружения'),
        ('cadastral_property_complex_num_MAPS', 'Кадастровый номер здания/сооружения/помещения', 'text', None, 0, 'format_site_MAPS=Помещение OR Здания и сооружения'),
        ('building_specifications_MAPS', 'Технические характеристики здания/сооружения/помещения', 'text', None, 0, None),
        ('Name_owner_MAPS', 'Наименование собственника / администратора объекта', 'text', None, 1, None),
        ('INN_MAPS', 'ИНН собственника', 'text', None, 0, None),
        ('contact_person_MAPS', 'Контактное лицо', 'text', None, 0, None),
        ('contact_phone_num_MAPS', 'Телефон контактного лица, e-mail', 'text', None, 1, None),
        ('website_contact_person_MAPS', 'Сайт', 'text', None, 0, None),
        ('notes_MAPS', 'Примечание', 'text', None, 0, None),
        ('water_supply_availability_MAPS', 'Водоснабжение Наличие', 'select', 441, 1, None),
        ('water_supply_tariff_consumption_MAPS', 'Водоснабжение Тариф на потребление, руб./куб.м', 'text', None, 0, 'water_supply_availability_MAPS=Да OR Возможно создание'),
        ('water_supply_tariff_transportation_MAPS', 'Водоснабжение Тариф на транспортировку, руб./куб.м', 'text', None, 0, 'water_supply_availability_MAPS=Да'),
        ('water_supply_available_capacity_MAPS', 'Водоснабжение Макс. допустимая мощность, куб.м/ч', 'number', None, 0, 'water_supply_availability_MAPS=Да'),
        ('free_power_water_supply_MAPS', 'Водоснабжение Свободная мощность, куб.м/ч', 'number', None, 0, 'water_supply_availability_MAPS=Да'),
        ('other_free_power_water_supply_MAPS', 'Водоснабжение Иные характеристики', 'text', None, 0, None),
        ('bandwidth_water_supply_MAPS', 'Сети водоснабжения Пропускная способность, куб.м/ч', 'number', None, 0, 'water_supply_availability_MAPS=Да'),
        ('water_disposal_availability_MAPS', 'Водоотведение Наличие', 'select', 441, 1, None),
        ('water_disposal_tariff_consumption_MAPS', 'Водоотведение Тариф на потребление, руб./куб.м', 'text', None, 0, 'water_disposal_availability_MAPS=Да OR Возможно создание'),
        ('water_disposal_tariff_transportation_MAPS', 'Водоотведение Тариф на транспортировку, руб./куб.м', 'text', None, 0, 'water_disposal_availability_MAPS=Да'),
        ('water_disposal_available_capacity_MAPS', 'Водоотведение Макс. допустимая мощность, куб.м/ч', 'number', None, 0, 'water_disposal_availability_MAPS=Да'),
        ('free_power_water_disposal_MAPS', 'Водоотведение Свободная мощность, куб.м/ч', 'number', None, 0, 'water_disposal_availability_MAPS=Да'),
        ('other_free_power_water_disposal_MAPS', 'Водоотведение Иные характеристики', 'text', None, 0, None),
        ('bandwidth_water_disposal_MAPS', 'Сети водоотведения Пропускная способность, куб.м/ч', 'number', None, 0, 'water_disposal_availability_MAPS=Да'),
        ('gas_supply_availability_MAPS', 'Газоснабжение Наличие', 'select', 441, 1, None),
        ('gas_supply_tariff_consumption_MAPS', 'Газоснабжение Тариф на потребление, руб./куб.м', 'text', None, 0, 'gas_supply_availability_MAPS=Да OR Возможно создание'),
        ('gas_supply_tariff_transportation_MAPS', 'Газоснабжение Тариф на транспортировку, руб./куб.м', 'text', None, 0, 'gas_supply_availability_MAPS=Да'),
        ('gas_supply_available_capacity_MAPS', 'Газоснабжение Макс. допустимая мощность, куб.м/ч', 'number', None, 0, 'gas_supply_availability_MAPS=Да OR Возможно создание'),
        ('free_power_gas_supply_MAPS', 'Газоснабжение Свободная мощность, куб.м/ч', 'number', None, 0, 'gas_supply_availability_MAPS=Да'),
        ('other_free_power_gas_supply_MAPS', 'Газоснабжение Иные характеристики', 'text', None, 0, None),
        ('bandwidth_gas_supply_MAPS', 'Сети газоснабжения Пропускная способность, куб.м/ч', 'number', None, 0, 'gas_supply_availability_MAPS=Да'),
        ('power_supply_availability_MAPS', 'Электроснабжение Наличие', 'select', 441, 1, None),
        ('power_supply_tariff_consumption_MAPS', 'Электроснабжение Тариф на потребление, руб./МВт*ч', 'text', None, 0, 'power_supply_availability_MAPS=Да OR Возможно создание'),
        ('power_supply_tariff_transportation_MAPS', 'Электроснабжение Тариф на транспортировку, руб./МВт*ч', 'text', None, 0, 'power_supply_availability_MAPS=Да'),
        ('power_supply_available_capacity_MAPS', 'Электроснабжение Макс. допустимая мощность, МВт/ч', 'number', None, 0, 'power_supply_availability_MAPS=Да OR Возможно создание'),
        ('free_power_electrosupply_MAPS', 'Электроснабжение Свободная мощность, МВт/ч', 'number', None, 0, 'power_supply_availability_MAPS=Да'),
        ('other_free_power_electrosupply_MAPS', 'Электроснабжение Иные характеристики', 'text', None, 0, None),
        ('bandwidth_electrosupply_MAPS', 'Сети электроснабжения Пропускная способность, МВт/ч', 'number', None, 0, 'power_supply_availability_MAPS=Да'),
        ('heat_supply_availability_MAPS', 'Теплоснабжение Наличие', 'select', 441, 1, None),
        ('heat_supply_tariff_consumption_MAPS', 'Теплоснабжение Тариф на потребление, руб./Гкал*ч', 'text', None, 0, 'heat_supply_availability_MAPS=Да OR Возможно создание'),
        ('heat_supply_tariff_transportation_MAPS', 'Теплоснабжение Тариф на транспортировку, руб./Гкал*ч', 'text', None, 0, 'heat_supply_availability_MAPS=Да'),
        ('heat_supply_available_capacity_MAPS', 'Теплоснабжение Макс. допустимая мощность, Гкал/ч', 'number', None, 0, 'heat_supply_availability_MAPS=Да OR Возможно создание'),
        ('free_power_heat_supply_MAPS', 'Теплоснабжение Свободная мощность, Гкал/ч', 'number', None, 0, 'heat_supply_availability_MAPS=Да'),
        ('other_free_power_heat_supply_MAPS', 'Теплоснабжение Иные характеристики', 'text', None, 0, None),
        ('bandwidth_heat_supply_MAPS', 'Сети теплоснабжения Пропускная способность, Гкал/ч', 'number', None, 0, 'heat_supply_availability_MAPS=Да'),
        ('MSW_removal_availability_MAPS', 'Вывоз ТКО Наличие', 'select', 441, 1, None),
        ('MSW_removal_tariff_MAPS', 'Вывоз ТКО Тариф, руб./тонна', 'number', None, 0, None),
        ('MSW_removal_tariff_2_MAPS', 'Вывоз ТКО Тариф, руб./куб.м', 'number', None, 0, None),
        ('access_roads_availability_MAPS', 'Наличие подъездных путей', 'select', 441, 1, None),
        ('railway_availability_MAPS', 'Наличие ж/д', 'select', 441, 1, None),
        ('truck_parking_availability_MAPS', 'Наличие парковки грузового транспорта', 'select', 441, 1, None),
        ('access_roads_other_MAPS', 'Транспорт Иные характеристики', 'text', None, 0, None),
        ('description_application_procedure_MAPS', 'Описание процедуры подачи заявки', 'text', None, 1, None),
        ('list_of_documents_for_application_MAPS', 'Перечень документов для подачи заявки', 'text', None, 0, None),
        ('Email_address_for_applying_MAPS', 'Адрес эл. почты для подачи заявки', 'text', None, 0, None),
        ('link_to_application_form_MAPS', 'Ссылка на форму подачи заявки', 'text', None, 0, None),
        ('list_economic_activities_for_implementation_dict_MAPS', 'Виды экономической деятельности на площадке', 'select', 513, 1, None),
        ('urban_plan_characteristics_and_limits_MAPS', 'Градостроительные характеристики и ограничения', 'text', None, 0, None),
        ('territorial_plan_documents_file_MAPS', 'Документы территориального планирования', 'file', None, 0, None),
        ('other_information_site_MAPS', 'Иные сведения', 'text', None, 0, None),
        ('photos_object_MAPS', 'Фотографии объекта', 'file', None, 0, None),
        ('documents_object_MAPS', 'Документы по объекту', 'file', None, 0, None),
        ('flag_MAIP_MAPS', 'Наличие МАИП', 'bool', None, 0, None),
        ('description_of_benefits_MAPS', 'Описание льготы', 'text', None, 0, 'flag_MAIP_MAPS=1'),
        ('geodata', 'Геоданные (координаты точки/линии/полигона)', 'geo', None, 0, None),
        ('coordinate_flag_MAPS', 'Не могу отметить координаты на карте', 'bool', None, 1, None),
        ('object_geotype_SubRegIP', 'Геотип объекта', 'select', 439, 0, 'coordinate_flag_MAPS=1'),
        ('latitude_MAPS', 'Широта объекта в координатах WGS-84', 'number', None, 0, 'object_geotype_SubRegIP=точка'),
        ('longitude_MAPS', 'Долгота объекта в координатах WGS-84', 'number', None, 0, 'object_geotype_SubRegIP=точка'),
        ('line_coordinates_SubRegIP', 'Набор координат линии в WGS-84', 'text', None, 0, 'object_geotype_SubRegIP=линия'),
        ('polygon_coordinates_SubRegIP', 'Набор координат полигона в WGS-84', 'text', None, 0, 'object_geotype_SubRegIP=полигон'),
    ]
    for row in fields:
        conn.execute(
            "INSERT OR IGNORE INTO investmap_fields "
            "(tech_name, display_name, data_type, classifier_num, is_required, required_condition) "
            "VALUES (?,?,?,?,?,?)",
            row
        )
def _migrate_investmap_rf_snapshot_tables(conn):
    """Создаёт read-only хранилище API-снимков Инвестиционной карты РФ."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS investmap_rf_card_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            global_id INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            fetched_at_utc TEXT NOT NULL,
            filling_level INTEGER,
            region_code INTEGER,
            UNIQUE(global_id, payload_sha256)
        );

        CREATE INDEX IF NOT EXISTS idx_investmap_rf_snapshots_card_fetched
            ON investmap_rf_card_snapshots(global_id, fetched_at_utc);

        CREATE TABLE IF NOT EXISTS investmap_rf_card_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            global_id INTEGER NOT NULL,
            previous_snapshot_id INTEGER NOT NULL,
            current_snapshot_id INTEGER NOT NULL,
            field_path TEXT NOT NULL,
            old_value_json TEXT,
            new_value_json TEXT,
            detected_at_utc TEXT NOT NULL,
            FOREIGN KEY (previous_snapshot_id)
                REFERENCES investmap_rf_card_snapshots(id),
            FOREIGN KEY (current_snapshot_id)
                REFERENCES investmap_rf_card_snapshots(id)
        );

        CREATE INDEX IF NOT EXISTS idx_investmap_rf_changes_card_detected
            ON investmap_rf_card_changes(global_id, detected_at_utc);
        """
    )

# ──────────────────────────────────────────────────────────────────────────────
# feat #95 — Умный справочник предметов обращения
# ──────────────────────────────────────────────────────────────────────────────

# Матрица видимости секций по умолчанию для 7 стандартных предметов.
# Ключи совпадают с data-section-key в шаблонах формы.
_SUBJECT_SECTIONS_DEFAULT = {
    # prefix → {"show": [...], "require": [...]}
    "ПЗУ":  {"show": ["project", "location", "site", "engineering", "transport", "investments"], "require": []},
    "ПМП":  {"show": ["project", "investments"], "require": []},
    "ПЗ":   {"show": ["project", "location", "building", "investments"], "require": []},
    "К":    {"show": ["appeal_text"], "require": ["appeal_text"]},
    "ПВ":   {"show": ["location", "appeal_text"], "require": []},
    "ПРС":  {"show": ["location", "site", "appeal_text"], "require": []},
}

# Начальное наполнение системной таблицы form_sections
_FORM_SECTIONS_SEED = [
    ("project",      "Проект",                       "Название, ОКВЭД проекта",                     1),
    ("location",     "Локация",                      "Район НО, адрес",                             2),
    ("site",         "Участок / Площадка",           "Кадастровый номер, площадь з/у",              3),
    ("building",     "Здание / Помещение",           "Площадь, тип помещения",                      4),
    ("engineering",  "Инженерная инфраструктура",    "Электричество, газ, вода, теплоснабжение",    5),
    ("transport",    "Транспортная доступность",     "Дороги, ж/д, аэропорт",                       6),
    ("investments",  "Инвестиции",                   "Объём инвестиций, рабочие места",             7),
    ("appeal_text",  "Суть обращения",               "Текстовое описание проблемы или вопроса",     8),
]

def _migrate_investmap_rf_monitor_registry_tables(conn):
    """Создаёт реестр отслеживаемых площадок и журнал событий импорта."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS investmap_rf_monitored_cards (
            global_id INTEGER PRIMARY KEY,
            is_active INTEGER NOT NULL DEFAULT 1,
            source_filename TEXT NOT NULL,
            imported_at_utc TEXT NOT NULL,
            last_seen_import_at_utc TEXT NOT NULL,
            last_source_status TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_investmap_rf_monitored_cards_active
            ON investmap_rf_monitored_cards(is_active, global_id);

        CREATE TABLE IF NOT EXISTS investmap_rf_monitor_registry_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            global_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            previous_status TEXT,
            current_status TEXT NOT NULL,
            source_filename TEXT NOT NULL,
            occurred_at_utc TEXT NOT NULL,
            reason TEXT,
            changed_by_user_id INTEGER
        );

        CREATE INDEX IF NOT EXISTS idx_investmap_rf_registry_events_card
            ON investmap_rf_monitor_registry_events(global_id, id DESC);
        """
    )
    event_columns = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(investmap_rf_monitor_registry_events)"
        ).fetchall()
    }

    if "reason" not in event_columns:
        conn.execute(
            """
            ALTER TABLE investmap_rf_monitor_registry_events
            ADD COLUMN reason TEXT
            """
        )

    if "changed_by_user_id" not in event_columns:
        conn.execute(
            """
            ALTER TABLE investmap_rf_monitor_registry_events
            ADD COLUMN changed_by_user_id INTEGER
            """
        )
    card_columns = {
        row["name"]
        for row in conn.execute(
            "PRAGMA table_info(investmap_rf_monitored_cards)"
        ).fetchall()
    }

    card_columns_to_add = (
        ("last_api_check_at_utc", "TEXT"),
        ("last_api_check_status", "TEXT"),
        ("last_api_check_error", "TEXT"),
        (
            "api_not_found_pending_decision",
            "INTEGER NOT NULL DEFAULT 0",
        ),
        ("api_not_found_detected_at_utc", "TEXT"),
        ("object_created_at", "TEXT"),
    )

    for column_name, column_type in card_columns_to_add:
        if column_name not in card_columns:
            conn.execute(
                "ALTER TABLE investmap_rf_monitored_cards "
                f"ADD COLUMN {column_name} {column_type}"
            )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_investmap_rf_monitored_cards_pending_decision
        ON investmap_rf_monitored_cards(
            api_not_found_pending_decision,
            is_active,
            global_id
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_investmap_rf_monitored_cards_object_created_at
        ON investmap_rf_monitored_cards(
            object_created_at,
            global_id
        )
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS
            idx_investmap_rf_registry_events_period_type
        ON investmap_rf_monitor_registry_events(
            occurred_at_utc,
            event_type,
            global_id
        )
        """
    )

_INVESTMAP_RF_MUNICIPALITY_MANAGER_RULES = [
    ("Балахнинской муниципальный округ", "Балахн", "Гусев Олег Викторович"),
    ("м.о.г.Чкаловск", "Чкалов", "Гусев Олег Викторович"),
    ("Городецкий муниципальный округ", "Городец", "Гусев Олег Викторович"),
    ("м.о.Сокольский", "Сокольск", "Гусев Олег Викторович"),
    ("Городской округ город Бор", "Бор", "Гусев Олег Викторович"),
    ("Ковернинский муниципальный округ", "Ковернин", "Гусев Олег Викторович"),
    ("м.о.г.м.о.Семеновский", "Семенов", "Гусев Олег Викторович"),
    ("Воскресенский муниципальный округ", "Воскресенск", "Зайцева Галина Павловна"),
    ("Краснобаковский муниципальный округ", "Краснобак", "Зайцева Галина Павловна"),
    ("Варнавинский муниципальный округ", "Варнав", "Зайцева Галина Павловна"),
    ("Ветлужский муниципальный округ", "Ветлуж", "Зайцева Галина Павловна"),
    ("Уренский муниципальный округ", "Урен", "Зайцева Галина Павловна"),
    ("Тонкинский муниципальный округ", "Тонкин", "Зайцева Галина Павловна"),
    ("Шарангский муниципальный округ", "Шаранг", "Зайцева Галина Павловна"),
    ("м.о.г.Шахунья", "Шахун", "Зайцева Галина Павловна"),
    ("Тоншаевский муниципальный округ", "Тоншаев", "Зайцева Галина Павловна"),
    ("г. Нижний Новгород", "Новгород", "Алюков Дмитрий Владимирович"),
    ("Володарский муниципальный округ", "Володарск", "Алюков Дмитрий Владимирович"),
    ("г.о.г.Дзержинск", "Дзержинск", "Алюков Дмитрий Владимирович"),
    ("Павловский муниципальный округ", "Павловск", "Кашин Юрий Александрович"),
    ("Богородский муниципальный округ", "Богородск", "Кашин Юрий Александрович"),
    ("г.о.г.Выкса", "Выкс", "Кашин Юрий Александрович"),
    ("Сосновский муниципальный округ", "Сосновск", "Кашин Юрий Александрович"),
    ("Вачский муниципальный округ", "Вачск", "Кашин Юрий Александрович"),
    ("м.о.Навашинский", "Наваш", "Кашин Юрий Александрович"),
    ("г.о.г.Кулебаки", "Кулебак", "Кашин Юрий Александрович"),
    ("Большемурашкинский муниципальный округ", "Большемурашк", "Зимин Дмитрий Валерьевич"),
    ("Бутурлинский муниципальный округ", "Бутурлинск", "Зимин Дмитрий Валерьевич"),
    ("м.о.Воротынский", "Воротын", "Зимин Дмитрий Валерьевич"),
    ("Княгининский муниципальный округ", "Княгин", "Зимин Дмитрий Валерьевич"),
    ("Лысковский муниципальный округ", "Лысков", "Зимин Дмитрий Валерьевич"),
    ("м.о.Перевозский", "Перевоз", "Зимин Дмитрий Валерьевич"),
    ("Сергачский муниципальный округ", "Сергач", "Зимин Дмитрий Валерьевич"),
    ("Спасский муниципальный округ", "Спасск", "Зимин Дмитрий Валерьевич"),
    ("г.о.г.Нижний Новгород, Кстовский район", "Кстов", "Зимин Дмитрий Валерьевич"),
    ("Дальнеконстантиновский муниципальный округ", "Дальнеконст", "Зимин Дмитрий Валерьевич"),
    ("Большеболдинский муниципальный округ", "Большеболдин", "Марушкин Николай Васильевич"),
    ("Гагинский муниципальный округ", "Гагинск", "Марушкин Николай Васильевич"),
    ("Краснооктябрьский муниципальный округ", "Краснооктябрь", "Марушкин Николай Васильевич"),
    ("Лукояновский муниципальный округ", "Лукоянов", "Марушкин Николай Васильевич"),
    ("м.о.г.Первомайск", "Первомай", "Марушкин Николай Васильевич"),
    ("Пильнинский муниципальный округ", "Пильн", "Марушкин Николай Васильевич"),
    ("Починковский муниципальный округ", "Починк", "Марушкин Николай Васильевич"),
    ("Сеченовский муниципальный округ", "Сеченов", "Марушкин Николай Васильевич"),
    ("Шатковский муниципальный округ", "Шатковск", "Марушкин Николай Васильевич"),
    ("Ардатовский муниципальный округ", "Ардатов", "Марушкин Николай Васильевич"),
    ("г.о.г. Саров", "Саров", "Марушкин Николай Васильевич"),
    ("г.о.г.Арзамас", "Арзамас", "Марушкин Николай Васильевич"),
    ("Вадский муниципальный округ", "Вадск", "Марушкин Николай Васильевич"),
    ("Вознесенский муниципальный округ", "Вознесенск", "Марушкин Николай Васильевич"),
    ("Дивеевский муниципальный округ", "Дивеев", "Марушкин Николай Васильевич"),
]



def _migrate_investmap_rf_manager_assignment_tables(conn):
    """Создаёт справочник муниципалитетов, назначения и проблемы сопоставления."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS investmap_rf_municipality_manager_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            municipality_name TEXT NOT NULL,
            municipality_normalized TEXT NOT NULL UNIQUE,
            manager_name TEXT NOT NULL,
            match_mode TEXT NOT NULL DEFAULT 'contains',
            is_active INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL DEFAULT 'imported',
            created_by_user_id INTEGER,
            created_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(created_by_user_id) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_investmap_rf_manager_rules_active
        ON investmap_rf_municipality_manager_rules(
            is_active,
            municipality_normalized
        );

        CREATE TABLE IF NOT EXISTS investmap_rf_card_manager_assignments (
            global_id INTEGER PRIMARY KEY,
            municipality_raw TEXT,
            municipality_normalized TEXT,
            manager_name TEXT,
            rule_id INTEGER,
            assignment_source TEXT NOT NULL,
            match_status TEXT NOT NULL,
            assigned_by_user_id INTEGER,
            updated_at_utc TEXT NOT NULL,
            FOREIGN KEY(rule_id)
                REFERENCES investmap_rf_municipality_manager_rules(id),
            FOREIGN KEY(assigned_by_user_id) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_investmap_rf_card_manager_assignments_manager
        ON investmap_rf_card_manager_assignments(manager_name, global_id);

        CREATE INDEX IF NOT EXISTS idx_investmap_rf_card_manager_assignments_status
        ON investmap_rf_card_manager_assignments(match_status, global_id);

        CREATE TABLE IF NOT EXISTS investmap_rf_manager_match_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            global_id INTEGER NOT NULL,
            municipality_raw TEXT,
            municipality_normalized TEXT,
            issue_type TEXT NOT NULL,
            details TEXT,
            is_resolved INTEGER NOT NULL DEFAULT 0,
            resolved_by_user_id INTEGER,
            resolved_at_utc TEXT,
            created_at_utc TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL,
            FOREIGN KEY(resolved_by_user_id) REFERENCES users(id)
        );

        CREATE INDEX IF NOT EXISTS idx_investmap_rf_manager_match_issues_open
        ON investmap_rf_manager_match_issues(
            is_resolved,
            issue_type,
            global_id
        );
        """
    )

    issue_index_names = {
        row["name"]
        for row in conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'index'
              AND tbl_name = 'investmap_rf_manager_match_issues'
            """
        ).fetchall()
    }

    if "idx_investmap_rf_manager_match_issues_open_unique" not in issue_index_names:
        conn.executescript(
            """
            CREATE TABLE investmap_rf_manager_match_issues_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                global_id INTEGER NOT NULL,
                municipality_raw TEXT,
                municipality_normalized TEXT,
                issue_type TEXT NOT NULL,
                details TEXT,
                is_resolved INTEGER NOT NULL DEFAULT 0,
                resolved_by_user_id INTEGER,
                resolved_at_utc TEXT,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL,
                FOREIGN KEY(resolved_by_user_id) REFERENCES users(id)
            );

            INSERT INTO investmap_rf_manager_match_issues_new (
                id,
                global_id,
                municipality_raw,
                municipality_normalized,
                issue_type,
                details,
                is_resolved,
                resolved_by_user_id,
                resolved_at_utc,
                created_at_utc,
                updated_at_utc
            )
            SELECT
                id,
                global_id,
                municipality_raw,
                municipality_normalized,
                issue_type,
                details,
                is_resolved,
                resolved_by_user_id,
                resolved_at_utc,
                created_at_utc,
                updated_at_utc
            FROM investmap_rf_manager_match_issues;

            DROP TABLE investmap_rf_manager_match_issues;

            ALTER TABLE investmap_rf_manager_match_issues_new
            RENAME TO investmap_rf_manager_match_issues;

            CREATE INDEX idx_investmap_rf_manager_match_issues_open
            ON investmap_rf_manager_match_issues(
                is_resolved,
                issue_type,
                global_id
            );

            CREATE UNIQUE INDEX idx_investmap_rf_manager_match_issues_open_unique
            ON investmap_rf_manager_match_issues(
                global_id,
                municipality_normalized,
                issue_type
            )
            WHERE is_resolved = 0;
            """
        )

    conn.executemany(
        """
        INSERT INTO investmap_rf_municipality_manager_rules (
            municipality_name,
            municipality_normalized,
            manager_name,
            match_mode,
            is_active,
            source
        )
        VALUES (?, ?, ?, 'contains', 1, 'imported')
        ON CONFLICT(municipality_normalized) DO UPDATE SET
            municipality_name = excluded.municipality_name,
            manager_name = excluded.manager_name,
            match_mode = 'contains',
            is_active = 1,
            source = 'imported',
            updated_at_utc = CURRENT_TIMESTAMP
        """,
        _INVESTMAP_RF_MUNICIPALITY_MANAGER_RULES,
    )
    
def _migrate_form_sections(conn):
    """feat #95 — системная таблица form_sections и расширение subject_types/requests."""

    # 1. Создать таблицу form_sections
    conn.executescript("""
CREATE TABLE IF NOT EXISTS form_sections (
    key         TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    description TEXT,
    sort_order  INTEGER DEFAULT 0
);
""")

    # 2. Наполнить form_sections начальными значениями (только если пусто)
    if not conn.execute("SELECT key FROM form_sections LIMIT 1").fetchone():
        conn.executemany(
            "INSERT OR IGNORE INTO form_sections (key, label, description, sort_order) VALUES (?,?,?,?)",
            _FORM_SECTIONS_SEED
        )

    # 3. Добавить sections_config в subject_types
    st_cols = {r[1] for r in conn.execute("PRAGMA table_info(subject_types)").fetchall()}
    if 'sections_config' not in st_cols:
        conn.execute("ALTER TABLE subject_types ADD COLUMN sections_config TEXT DEFAULT '{}'")

    # 4. Добавить appeal_text в requests
    req_cols = {r[1] for r in conn.execute("PRAGMA table_info(requests)").fetchall()}
    if 'appeal_text' not in req_cols:
        conn.execute("ALTER TABLE requests ADD COLUMN appeal_text TEXT")

    # 5. Проставить sections_config для существующих предметов у которых он ещё пустой/NULL
    #    Матрица: сопоставляем по reg_prefix (или по вхождению префикса в name).
    rows = conn.execute("SELECT id, name, reg_prefix, sections_config FROM subject_types").fetchall()
    for row in rows:
        sid, name, prefix, cfg_raw = row[0], row[1], row[2], row[3]
        # Пропускаем, если уже настроен (не NULL и не пустой объект)
        try:
            existing = json.loads(cfg_raw) if cfg_raw else {}
            if existing.get("show"):
                continue
        except Exception:
            pass

        # Ищем подходящую матрицу: сначала по reg_prefix, потом по вхождению в name
        matched_cfg = None
        if prefix and prefix in _SUBJECT_SECTIONS_DEFAULT:
            matched_cfg = _SUBJECT_SECTIONS_DEFAULT[prefix]
        else:
            for key in _SUBJECT_SECTIONS_DEFAULT:
                if key in (name or ""):
                    matched_cfg = _SUBJECT_SECTIONS_DEFAULT[key]
                    break

        if matched_cfg:
            conn.execute(
                "UPDATE subject_types SET sections_config=? WHERE id=?",
                (json.dumps(matched_cfg, ensure_ascii=False), sid)
            )


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
    applicant_feedback_at TEXT,
    appeal_text TEXT
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

        _migrate_classifiers_tables(conn)
        _migrate_districts_table(conn)
        _migrate_review_chain_table(conn)
        _migrate_investmap_tables(conn)
        _migrate_investmap_rf_snapshot_tables(conn)
        _migrate_investmap_rf_monitor_registry_tables(conn)
        _migrate_investmap_rf_sync_plan_tables(conn)
        migrate_investmap_data_updates(conn)
        _migrate_investmap_rf_sync_retry_tables(conn)
        _migrate_investmap_rf_manager_assignment_tables(conn)
        _migrate_letters_tables(conn)
        _migrate_contracts_tables(conn)
        _migrate_tasks_tables(conn)
        _migrate_task_comments_table(conn)
        _migrate_suggestions_table(conn)
        _migrate_phonebook_orgs_inn(conn)     # ← fix #sync-fix-3
        _migrate_form_sections(conn)          # ← feat #95

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
        _migrate_request_cols(conn)
        _migrate_users_cols(conn)

        if not conn.execute("SELECT id FROM users WHERE username='admin'").fetchone():
            conn.execute(
                "INSERT INTO users (username,password,full_name,role,"
                "can_create,can_edit_others,can_confirm,can_delete,"
                "can_rollback,can_export,can_classifiers,can_users,can_view_all,is_active) "
                "VALUES (?,?,?,?,1,1,1,1,1,1,1,1,1,1)",
                ('admin', hash_pw('admin123'), 'Администратор', 'admin')
            )

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

        create_analysis_history_tables(conn)
        conn.commit()
    finally:
        conn.close()


def migrate_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        _migrate_classifiers_tables(conn)
        _migrate_districts_table(conn)
        _migrate_review_chain_table(conn)
        _migrate_investmap_tables(conn)
        _migrate_investmap_rf_snapshot_tables(conn)
        _migrate_investmap_rf_monitor_registry_tables(conn)
        _migrate_investmap_rf_sync_plan_tables(conn)
        _migrate_investmap_rf_sync_retry_tables(conn)
        _migrate_investmap_rf_manager_assignment_tables(conn)
        _migrate_letters_tables(conn)
        _migrate_tasks_tables(conn)
        _migrate_task_comments_table(conn)
        _migrate_suggestions_table(conn)
        _migrate_phonebook_orgs_inn(conn)     # ← fix #sync-fix-3
        _migrate_form_sections(conn)          # ← feat #95

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
        _migrate_request_cols(conn)
        _migrate_users_cols(conn)

        conn.execute("""
            UPDATE users SET can_create=1, can_export=1, can_view_all=1
            WHERE role='employee' AND can_create=0
        """)

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

        conn.executescript("""
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
        if 'inn' not in pb_cols:
            conn.execute("ALTER TABLE phonebook ADD COLUMN inn TEXT DEFAULT ''")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_phonebook_inn ON phonebook(inn)"
        )

        create_analysis_history_tables(conn)
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
