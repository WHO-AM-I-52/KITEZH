# ╔══════════════════════════════════════════════════════════════╗
# ║                      form_utils.py                           ║
# ║  Работа с формой обращения: поля, приведение типов,          ║
# ║  классификаторы                                              ║
# ║  v2.2: +поля логики статусов (#53)                          ║
# ╚══════════════════════════════════════════════════════════════╝

from validators import _int, _flt


# Поля формы обращения (используются для массового чтения/записи)
ALL_FIELDS = [
    "request_date", "status", "consent_disclosure",
    "source_type",
    "applicant_full_name", "applicant_short_name", "applicant_legal_form",
    "applicant_inn", "applicant_msp_category", "applicant_okved_main",
    "postal_address", "legal_address", "project_name",
    "contact_person", "contact_phone", "contact_email",
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
    # ─ Сведения об ответе ──────────────────────────────────────────────────
    "answer_date", "answer_method", "answer_method_other", "answer_notes", "answer_file",
    "request_files",
    "edit_reason",
    # ─ МинЭК: новые поля (добавлены через миграцию db.py) ───────────
    "subject_type_id",   # Предмет обращения (FK → subject_types)
    "feedback_date",     # Дата получения обратной связи
    "result_type_id",    # Итоги работы по обращению (FK → result_types)
    # ─ Входящий номер (Directum / СЭДО) ────────────────────────────
    "incoming_number",
    # ─ Issue #53: новая логика статусов ───────────────────────────────
    "review_days",               # Срок рассмотрения (календарные дни, по умолчанию 7)
    "responsible_id",            # Ответственный за подбор площадок (FK → users)
    "responsible_not_in_system", # Галочка: не зарегистрирован в системе
    "responsible_name_external", # ФИО если не в системе
    "reviewer_id",               # Проверяющий площадки (FK → users)
    "reviewer_not_in_system",    # Галочка: не зарегистрирован в системе
    "reviewer_name_external",    # ФИО если не в системе
    "sent_to_applicant_at",      # Дата отправки документов заявителю
    "send_method",               # Способ отправки
    "applicant_feedback",        # Обратная связь от заявителя
    "applicant_feedback_at",     # Дата получения ОС от заявителя
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
    "review_days", "responsible_id", "reviewer_id",  # #53
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
    - список сотрудников (для назначения ответственных),
    - справочник предметов обращений (subject_types),
    - справочник итогов работы (result_types),
    - список пользователей для выбора ответственного/проверяющего (#53).
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
    # #53: все пользователи для выбора ответственного и проверяющего
    all_users = conn.execute(
        "SELECT id, full_name, role FROM users WHERE is_active=1 ORDER BY full_name"
    ).fetchall()
    return lf, di, src, emp, subjects, results, all_users


def build_values(form):
    """
    Собирает значения из формы Flask-WTF / request.form
    в том порядке, который соответствует ALL_FIELDS.
    Приводит типы чисел и булевых значений.
    """
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
        v = form.get(f, '')
        if f in BOOL_F:
            vals.append(1 if v in ('1', 'on', 'true', 'yes') else 0)
        elif f in INT_F:
            vals.append(_int(v))
        elif f in FLOAT_F:
            vals.append(_flt(v))
        else:
            vals.append(v.strip() if v else None)
    return vals
