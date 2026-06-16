# ╔══════════════════════════════════════════════════════════════
# ║                      form_utils.py                           ║
# ║  Работа с формой обращения: поля, приведение типов,          ║
# ║  классификаторы                                              ║
# ║  v2.6: contact_position добавлен после contact_email         ║
# ║  v2.7: add_workdays() — +N рабочих дней (сб/вс — выходные) ║
# ║  v2.8: site_area_ha/site_build_area_m2 → _min/_max (багфикс)   ║
# ║  v2.9: review_days default=7 если не задан в форме           ║
# ╚══════════════════════════════════════════════════════════════

from datetime import date, timedelta
from validators import _int, _flt


# ─────────────────────────────────────────────────────────────────────────────
def add_workdays(start: date, days: int) -> date:
    """
    Возвращает дату = start + days рабочих дней.
    Суббота (weekday==5) и воскресенье (weekday==6) — выходные.
    Праздники РФ также считаются выходными (offcalendar).
    Текущая реализация учитывает только сб/вс.
    """
    current = start
    counted = 0
    while counted < days:
        current += timedelta(days=1)
        if current.weekday() < 5:  # 0=Пн … 4=Пт
            counted += 1
    return current


# ─── Issue #48: Коэффициенты перевода в базовые единицы ──────────────────
UNIT_FACTORS = {
    'elec_unit': {
        'кВт':  1.0,
        'МВт':  1000.0,
    },
    'heat_unit': {
        'Гкал/ч': 1.0,
        'МВт':   1.163,
        'кДж/ч':  1 / 4186.8,
    },
    'gas_unit_h': {
        'м³/ч':      1.0,
        'тыс.м³/ч': 1000.0,
    },
    'gas_unit_y': {
        'м³/год':      1.0,
        'тыс.м³/год': 1000.0,
    },
    'water_unit': {
        'м³/сут': 1.0,
        'м³/ч':  24.0,
    },
}

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
    if value is None:
        return None
    factor = UNIT_FACTORS.get(unit_key, {}).get(unit_value, 1.0)
    return round(value * factor, 6)


def denormalize_from_base(value, unit_key, unit_value):
    if value is None:
        return None
    factor = UNIT_FACTORS.get(unit_key, {}).get(unit_value, 1.0)
    if factor == 0:
        return None
    return round(value / factor, 6)


# ─────────────────────────────────────────────────────────────────────────────
# ALL_FIELDS — порядок соответствует порядку колонок в INSERT/UPDATE
# v2.8: site_area_ha       → site_area_ha_min + site_area_ha_max
#        site_build_area_m2 → site_build_area_m2_min + site_build_area_m2_max
ALL_FIELDS = [
    "request_date", "status", "consent_disclosure",
    "source_type",
    "applicant_full_name", "applicant_short_name", "applicant_legal_form",
    "applicant_inn", "applicant_msp_category", "applicant_okved_main",
    "postal_address", "legal_address", "project_name",
    "contact_person", "contact_phone", "contact_email",
    "contact_position",
    "jobs_total", "jobs_foreign",
    "investment_total", "investment_borrowed",
    "construction_stages", "construction_start", "operation_start",
    "product_nomenclature", "production_description", "object_composition",
    "site_type_free", "site_type_existing",
    "site_area_ha_min", "site_area_ha_max",   # ← v2.8: было site_area_ha
    "site_area_expansion",
    "site_build_area_m2_min", "site_build_area_m2_max",  # ← v2.8: было site_build_area_m2
    "site_right", "sanitary_zone_m", "hazard_class",
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
    "answer_date", "answer_method", "answer_method_other", "answer_notes", "answer_file",
    "request_files",
    "edit_reason",
    "subject_type_id",
    "feedback_date",
    "result_type_id",
    "incoming_number",
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
    "elec_unit",
    "heat_unit",
    "gas_unit_h",
    "gas_unit_y",
    "water_unit",
]

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
    "investment_total", "investment_borrowed",
    "site_area_ha_min", "site_area_ha_max",           # v2.8
    "site_build_area_m2_min", "site_build_area_m2_max", # v2.8
    "sanitary_zone_m", "site_length_min", "site_width_min",
    "water_household", "water_production", "sewage", "firefighting",
    "electricity_total", "electricity_cat1", "electricity_cat2", "electricity_cat3",
    "heat_gcal", "gas_m3h", "gas_m3y", "heated_area",
    "road_federal_dist", "road_regional_dist", "road_local_dist", "road_private_dist",
    "railway_dist", "railway_cargo", "distance_nn_max"
}

REQUIRED_FIELDS = {
    "request_date":        "Дата обращения",
    "source_type":         "Источник обращения",
    "applicant_full_name": "Полное наименование заявителя",
    "contact_phone":       "Контактный телефон",
    "project_name":        "Название проекта",
    "investment_total":    "Объём инвестиций",
    "jobs_total":          "Количество рабочих мест",
}

# Поля INT_F с обязательным дефолтом если форма не передала значение
_INT_DEFAULTS = {
    "review_days": 7,
}


def get_classifiers(conn):
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
        if f == 'site_right':
            selected = form.getlist('site_right')
            vals.append(', '.join(selected) if selected else None)
            continue
        v = form.get(f, '')
        if f in BOOL_F:
            vals.append(1 if v in ('1', 'on', 'true', 'yes') else 0)
        elif f in INT_F:
            iv = _int(v)
            if iv is None and f in _INT_DEFAULTS:
                iv = _INT_DEFAULTS[f]
            vals.append(iv)
        elif f in FLOAT_F:
            raw = _flt(v)
            unit_key = FIELD_UNIT_KEY.get(f)
            if unit_key and raw is not None:
                raw = normalize_to_base(raw, unit_key, units[unit_key])
            vals.append(raw)
        else:
            vals.append(v.strip() if v else '')
    return vals
