# ╔══════════════════════════════════════════════════════════════╗
# ║ migrations.py                                                ║
# ║ Инициализация и миграция БД (вынесено из app.py)             ║
# ║ +can_view_phonebook, can_view_investmap,                     ║
# ║  can_export_full, can_import_full, can_investmap_rules       ║
# ╚══════════════════════════════════════════════════════════════╝

import sqlite3

from db import DB_PATH
from core.auth_utils import hash_pw
from spravochnik import LEGAL_FORMS_DEFAULT, DISTRICTS_DEFAULT, SOURCE_TYPES_DEFAULT
from db import get_db

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


def _migrate_letters_tables(conn):
    """Создаёт таблицы letters/letter_tags/letter_tag_links.
    Порядок: сначала CREATE TABLE (без executor_id),
    потом ALTER TABLE добавляет executor_id если её нет,
    затем создаётся индекс (колонка уже точно есть).
    """
    # 1. Создаём таблицу letters без executor_id (безопасно если уже есть)
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

    # 2. Добавляем executor_id если ещё нет
    letter_cols = {r[1] for r in conn.execute("PRAGMA table_info(letters)").fetchall()}
    if 'executor_id' not in letter_cols:
        conn.execute("ALTER TABLE letters ADD COLUMN executor_id INTEGER")

    # 3. Индекс по executor_id создаём только после того как колонка точно есть
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_letters_executor ON letters(executor_id)"
    )


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

        _migrate_classifiers_tables(conn)
        _migrate_districts_table(conn)
        _migrate_review_chain_table(conn)
        _migrate_investmap_tables(conn)
        _migrate_letters_tables(conn)

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
        _migrate_letters_tables(conn)

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
        if 'inn' not in pb_cols:
            conn.execute("ALTER TABLE phonebook ADD COLUMN inn TEXT DEFAULT ''")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_phonebook_inn ON phonebook(inn)"
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
