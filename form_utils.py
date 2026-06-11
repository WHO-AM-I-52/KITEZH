# ╔══════════════════════════════════════════════════════════════╗
# ║                      form_utils.py                           ║
# ║  Работа с формой обращения: поля, приведение типов,          ║
# ║  классификаторы                                              ║
# ║  v2.6: contact_position добавлен после contact_email         ║
# ╚══════════════════════════════════════════════════════════════╝

from validators import _int, _flt


# ───────────────────────────────────────────────────────────────────────────────
# Issue #48: Коэффициенты перевода в базовые единицы
#
# Базовые единицы (в которых всё хранится в БД):
#   электро: кВт  | тепло: Гкал/ч  | газ: м³/ч и м³/год  | вода: м³/сут
#
# UNIT_FACTORS[поле_unit][единица] = коэффициент, на который умножается
# введённое значение для получения базового.
# Пример: 5 МВт * 1000 = 5000 кВт (база)
# ───────────────────────────────────────────────────────────────────────────────
UNIT_FACTORS = {
    'elec_unit': {
        'кВт':  1.0,          # базовая
        'МВт':  1000.0,       # 1 МВт = 1000 кВт
    },
    'heat_unit': {
        'Гкал/ч': 1.0,         # базовая
        'МВт':   1.163,       # 1 МВт = 1.163 Гкал/ч
        'кДж/ч':  1 / 4186.8,  # 1 кДЖ/ч = 0.000239 Гкал/ч
    },
    'gas_unit_h': {
        'м³/ч':      1.0,     # базовая
        'тыс.м³/ч': 1000.0,  # 1 тыс.м³/ч = 1000 м³/ч
    },
    'gas_unit_y': {
        'м³/год':      1.0,     # базовая
        'тыс.м³/год': 1000.0,  # 1 тыс.м³/год = 1000 м³/год
    },
    'water_unit': {
        'м³/сут': 1.0,   # базовая
        'м³/ч':  24.0,   # 1 м³/ч = 24 м³/сут
    },
}

# Соответствие: поле → какой unit-ключ ему соответствует
FIELD_UNIT_KEY = {
    'electricity_total': 'elec_unit',
    'electricity_cat1':  'elec_unit',
    'electricity_cat2':  'elec_unit',
    'electricity_cat3':  'elec_unit',
    'heat_gcal':         'heat_unit',
    'gas_m3h':           'gas_unit_h',
    'gas_m3y':           'gas_unit_y',
    'water_household':   'water_unit',
    'water_production':  'water_unit',
    'sewage':            'water_unit',
    'firefighting':      'water_unit',
}


def normalize_to_base(value, unit_key, unit_value):
    """
    Нормализует значение в базовые единицы.
    value     — float | None, введённое пользователем значение
    unit_key  — str, ключ в UNIT_FACTORS (напр., 'elec_unit')
    unit_value— str, выбранная единица (напр., 'МВт')
    """
    if value is None:
        return None
    factor = UNIT_FACTORS.get(unit_key, {}).get(unit_value, 1.0)
    return round(value * factor, 6)


def denormalize_from_base(value, unit_key, unit_value):
    """
    Обратный пересчёт: базовая единица → единица пользователя.
    Используется для подстановки в форму редактирования числа,
    которое пользователь видел при вводе.
    """
    if value is None:
        return None
    factor = UNIT_FACTORS.get(unit_key, {}).get(unit_value, 1.0)
    if factor == 0:
        return None
    result = value / factor
    # Округляем до 6 знаков, убираем незначимые нули
    return round(result, 6)


# ───────────────────────────────────────────────────────────────────────────────
# Поля формы обращения (используются для массового чтения/записи)
# ВАЖНО: порядок элементов должен строго совпадать с порядком колонок
# в INSERT/UPDATE запросах (form_routes.py использует позиционный маппинг).
# contact_position добавлен СРАЗУ ПОСЛЕ contact_email.
# ───────────────────────────────────────────────────────────────────────────────
ALL_FIELDS = [
    "request_date", "status", "consent_disclosure",
    "source_type",
    "applicant_full_name", "applicant_short_name", "applicant_legal_form",
    "applicant_inn", "applicant_msp_category", "applicant_okved_main",
    "postal_address", "legal_address", "project_name",
    "contact_person", "contact_phone", "contact_email",
    "contact_position",   # ← Должность уполномоченного лица (phonebook sync)
    "jobs_total", "jobs_foreign",
    "investment_total", "investment_borrowed",
    "construction_stages", "construction_start", "operation_start",
    "product_nomenclature", "production_description", "object_composition",
    "site_type_free", "site_type_existing", "site_area_ha", "site_area_expansion",
    "site_build_area_m2", "site_right", "sanitary_zone_m", "hazard_class",
    "site_shape", "site_length_min", "site_width_min", "site_other",
    "water_household", "water_production", "sewage", "firefighting",
    "electricity_total", "electricity_cat1", "electricity_cat2", "electricity_cat3",
    "heat_source", "heat_gcal", "gas_m3h", "gas_m3y", "gas_purpose", "heated_area",
    "internet", "phones_qty", "engineering_extra",
    "road_federal_dist", "road_regional_dist", "road_local_dist", "road_private_dist", "road_extra",
    "railway_needed", "railway_dist", "railway_cargo", "railway_extra", "transport_extra",
    "distance_nn_matters", "distance_nn_max", "preferred_districts", "location_extra",
    "staff_management", "staff_workers", "staff_other", "staff_it", "staff_admin",
    "raw_materials", "raw_extra", "additional_info",
    "assigned_to",
    # ─ Сведения об ответе ────────────────────────────────────────────────────────────
    "answer_date", "answer_method", "answer_method_other", "answer_notes", "answer_file",
    "request_files",
    "edit_reason",
    # ─ МинЭК: новые поля (добавлены через миграцию db.py) ───────
    "subject_type_id",   # Предмет обращения (FK → subject_types)
    "feedback_date",     # Дата получения обратной связи
    "result_type_id",    # Итоги работы по обращению (FK → result_types)
    # ─ Входящий номер (Directum / СЭДО) ─────────────────────────
    "incoming_number",
    # ─ Issue #53: новая логика статусов ────────────────────────────────
    "review_days",
    "responsible_id",
    "responsible_not_in_system",
    "responsible_name_external",
    "reviewer_id",
    "reviewer_not_in_system",
    "reviewer_name_external",
    "sent_to_applicant_at",
    "send_method",
    "applicant_feedback",
    "applicant_feedback_at",
    # ─ Issue #48: единицы измерения инфраструктуры ───────────────────
    "elec_unit",
    "heat_unit",
    "gas_unit_h",
    "gas_unit_y",
    "water_unit",
]

# Наборы полей по типу
BOOL_F = {
    "consent_disclosure", "site_type_free", "site_type_existing",
    "site_area_expansion", "railway_needed", "distance_nn_matters",
    "responsible_not_in_system", "reviewer_not_in_system",
}

INT_F = {
    "jobs_total", "jobs_foreign", "phones_qty", "staff_management",
    "staff_workers", "staff_other", "staff_it", "staff_admin", "assigned_to",
    "subject_type_id", "result_type_id",
    "review_days", "responsible_id", "reviewer_id",
}

FLOAT_F = {
    "investment_total", "investment_borrowed", "site_area_ha", "site_build_area_m2",
    "sanitary_zone_m", "site_length_min", "site_width_min", "water_household",
    "water_production", "sewage", "firefighting", "electricity_total",
    "electricity_cat1", "electricity_cat2", "electricity_cat3", "heat_gcal",
    "gas_m3h", "gas_m3y", "heated_area", "road_federal_dist", "road_regional_dist",
    "road_local_dist", "road_private_dist", "railway_dist", "railway_cargo", "distance_nn_max"
}

# Обязательные поля и их подписи для уведомлений
REQUIRED_FIELDS = {
    "request_date":        "Дата обращения",
    "source_type":         "Источник обращения",
    "applicant_full_name": "Полное наименование заявителя",
    "contact_phone":       "Контактный телефон",
    "project_name":        "Название проекта",
    "investment_total":    "Объём инвестиций",
    "jobs_total":          "Количество рабочих мест",
}


def get_classifiers(conn):
    """
    Возвращает справочники:
    - список правовых форм,
    - список районов,
    - список источников обращений,
    - список сотрудников,
    - справочник предметов обращений (subject_types),
    - справочник итогов работы (result_types),
    - список пользователей (#53).
    """
    lf  = [r['value'] for r in conn.execute(
        "SELECT value FROM classifiers WHERE category='legal_form' "
        "ORDER BY sort_order,value"
    ).fetchall()]
    di  = [r['value'] for r in conn.execute(
        "SELECT value FROM classifiers WHERE category='district' "
        "ORDER BY sort_order,value"
    ).fetchall()]
    src = [r['value'] for r in conn.execute(
        "SELECT value FROM classifiers WHERE category='source_type' "
        "ORDER BY sort_order,value"
    ).fetchall()]
    emp = conn.execute(
        "SELECT id,full_name FROM users "
        "WHERE role IN ('employee','admin','manager') "
        "ORDER BY full_name"
    ).fetchall()
    subjects = conn.execute(
        "SELECT id, name FROM subject_types ORDER BY id"
    ).fetchall()
    results = conn.execute(
        "SELECT id, name, color_hex FROM result_types ORDER BY id"
    ).fetchall()
    all_users = conn.execute(
        "SELECT id, full_name, role FROM users WHERE is_active=1 ORDER BY full_name"
    ).fetchall()
    return lf, di, src, emp, subjects, results, all_users


def build_values(form):
    """
    Собирает значения из формы Flask-WTF / request.form
    в том порядке, который соответствует ALL_FIELDS.
    Приводит типы чисел и булевых значений.

    Текстовые поля: пустой ввод сохраняется как '' (не NULL),
    чтобы избежать накопления NULL в БД при редактировании форм.
    NULL остаётся только для числовых и FK-полей (INT_F, FLOAT_F).

    Issue #48: числовые инфраструктурные поля нормализуются перед
    записью в базовые единицы (см. normalize_to_base).
    """
    # Сначала извлекаем единицы, чтобы использовать при нормализации
    units = {
        uk: (form.get(uk, '') or '').strip() or list(UNIT_FACTORS[uk].keys())[0]
        for uk in UNIT_FACTORS
    }

    vals = []
    for f in ALL_FIELDS:
        if f == 'source_type':
            selected = form.getlist('source_type')
            vals.append(', '.join(selected) if selected else None)
            continue
        if f == 'preferred_districts':
            selected = form.getlist('preferred_districts')
            vals.append(', '.join(selected) if selected else None)
            continue
        # #47: множественный выбор права пользования
        if f == 'site_right':
            selected = form.getlist('site_right')
            vals.append(', '.join(selected) if selected else None)
            continue
        v = form.get(f, '')
        if f in BOOL_F:
            vals.append(1 if v in ('1', 'on', 'true', 'yes') else 0)
        elif f in INT_F:
            vals.append(_int(v))
        elif f in FLOAT_F:
            # #48: нормализация если поле привязано к единице
            raw = _flt(v)
            unit_key = FIELD_UNIT_KEY.get(f)
            if unit_key and raw is not None:
                raw = normalize_to_base(raw, unit_key, units[unit_key])
            vals.append(raw)
        else:
            # Текстовые поля: '' вместо NULL — не затираем данные при редактировании
            vals.append(v.strip() if v else '')
    return vals
