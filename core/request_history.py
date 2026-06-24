# ╔══════════════════════════════════════════════════════════════╗
# ║ request_history.py                                           ║
# ║ История изменений обращений + откат                          ║
# ║ v2.6: per-field rows (field/old_val/new_val) для 7 полей     ║
# ╚══════════════════════════════════════════════════════════════╝

import json
from db import get_db

FIELD_LABELS = {
    'request_date':          'Дата обращения',
    'status':                'Статус',
    'source_type':           'Источник',
    'incoming_number':       '№ входящего',
    'consent_disclosure':    'Согласие',
    'applicant_full_name':   'Полн. наим.',
    'applicant_short_name':  'Краткое наим.',
    'applicant_legal_form':  'Правовая форма',
    'applicant_inn':         'ИНН',
    'applicant_msp_category':'Категория МСП',
    'applicant_okved_main':  'ОКВЭД',
    'postal_address':        'Почт. адрес',
    'legal_address':         'Юр. адрес',
    'contact_person':        'Контакт',
    'contact_phone':         'Телефон',
    'contact_email':         'E-mail',
    'project_name':          'Название проекта',
    'investment_total':      'Инвестиции, млн руб.',
    'investment_borrowed':   'в т.ч. заёмные',
    'jobs_total':            'Раб. мест всего',
    'jobs_foreign':          'Раб. мест иностр.',
    'construction_start':    'Начало строительства',
    'operation_start':       'Начало эксплуатации',
    'site_area_ha_min':      'Площадь з/у мин., га',
    'site_area_ha_max':      'Площадь з/у макс., га',
    'site_build_area_m2_min':'Площадь застройки мин., м²',
    'site_build_area_m2_max':'Площадь застройки макс., м²',
    'site_right':            'Право пользования',
    'hazard_class':          'Класс опасности',
    'preferred_districts':   'Районы',
    'answer_date':           'Дата ответа',
    'answer_method':         'Способ ответа',
    'answer_system_number':  '№ ответа в системе',
    'answer_notes':          'Примечание к ответу',
    'admin_comment':         'Комм. администратора',
    'request_files':         'Файлы',
    'edit_reason':           'Причина правки',
    'subject_type_id':       'Тип обращения',
    'result_type_id':        'Результат',
    'responsible_id':        'Ответственный',
    'reviewer_id':           'Проверяющий',
    'deadline':              'Дедлайн',
    'review_deadline':       'Дедлайн',
}

SKIP_FIELDS = {
    'updated_at', 'updated_by', 'created_at', 'created_by',
    'confirmed_at', 'confirmed_by', 'id'
}

# Поля, для которых дополнительно пишем отдельную строку c field/old_val/new_val
TRACKED_FIELDS = [
    'status',
    'subject_type_id',
    'result_type_id',
    'responsible_id',
    'reviewer_id',
    'review_deadline',
    'answer_method',
]


def save_history(conn, request_id: int, changed_by: int, old_row, new_row,
                 action: str = 'edit') -> None:
    """
    Сравнивает старое и новое состояние обращения.
    1) Пишет сводную строку с JSON-blob всех изменений.
    2) Дополнительно пишет отдельную строку для каждого поля из TRACKED_FIELDS
       (с field/old_val/new_val) — для детального отображения в history.html.
    action: 'edit' | 'rollback'
    """
    changes = {}

    old = dict(old_row) if not isinstance(old_row, dict) else old_row
    new = dict(new_row) if not isinstance(new_row, dict) else new_row

    all_keys = set(list(old.keys()) + list(new.keys()))
    for key in all_keys:
        if key in SKIP_FIELDS:
            continue
        old_v = str(old.get(key, '') or '').strip()
        new_v = str(new.get(key, '') or '').strip()
        if old_v != new_v:
            changes[key] = [old_v, new_v]

    if not changes:
        return

    # Сводная запись всех изменений (JSON-blob, обратная совместимость)
    conn.execute(
        "INSERT INTO request_history (request_id, changed_by, action, changes) VALUES (?,?,?,?)",
        (request_id, changed_by, action, json.dumps(changes, ensure_ascii=False))
    )

    # Детальные строки для отслеживаемых полей
    for fld in TRACKED_FIELDS:
        if fld not in changes:
            continue
        old_v, new_v = changes[fld]
        conn.execute(
            "INSERT INTO request_history "
            "(request_id, changed_by, action, changes, field, old_val, new_val) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                request_id, changed_by, action,
                json.dumps({fld: [old_v, new_v]}, ensure_ascii=False),
                fld, old_v, new_v,
            )
        )


def get_history(request_id: int) -> list:
    """
    Возвращает список записей истории для обращения.
    Отдельные строки (field IS NOT NULL) возвращаются только если history.html их использует.
    Здесь оставляем только сводные записи (field IS NULL) для рендеринга таблицы истории.
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT h.id, h.changed_at, h.action, u.full_name, h.changes, "
        "h.field, h.old_val, h.new_val "
        "FROM request_history h "
        "LEFT JOIN users u ON h.changed_by = u.id "
        "WHERE h.request_id=? AND h.field IS NULL "
        "ORDER BY h.changed_at DESC",
        (request_id,)
    ).fetchall()
    conn.close()

    result = []
    for row in rows:
        try:
            raw = json.loads(row['changes'])
        except Exception:
            raw = {}

        labeled = {FIELD_LABELS.get(k, k): v for k, v in raw.items()}
        action = row['action'] if row['action'] else 'edit'

        # Добавляем детальные поля из TRACKED_FIELDS если они попали в changes
        field_details = []
        for fld in TRACKED_FIELDS:
            if fld in raw:
                old_v, new_v = (raw[fld][0], raw[fld][1]) if isinstance(raw[fld], list) else ('', '')
                field_details.append({
                    'field':     fld,
                    'label':     FIELD_LABELS.get(fld, fld),
                    'old_val':   old_v,
                    'new_val':   new_v,
                })

        result.append({
            'id':            row['id'],
            'changed_at':    row['changed_at'],
            'user':          row['full_name'] or '—',
            'action':        action,
            'changes':       labeled,
            'field_details': field_details,
        })
    return result


def rollback_history(history_id: int, request_id: int) -> bool:
    """
    Откатывает обращение к состоянию ДО выбранной правки.
    Ключи в changes — всегда технические (snake_case), маппинг не нужен.
    Использует только сводные записи (field IS NULL).
    """
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM request_history WHERE id=? AND request_id=? AND field IS NULL",
        (history_id, request_id)
    ).fetchone()

    if not row:
        conn.close()
        return False

    try:
        changes = json.loads(row['changes'])
    except Exception:
        conn.close()
        return False

    set_parts = []
    vals = []
    for field, value in changes.items():
        if field in SKIP_FIELDS:
            continue
        old_val = value[0] if isinstance(value, list) and len(value) >= 1 else value
        set_parts.append(f"{field}=?")
        vals.append(old_val if old_val != '' else None)

    if not set_parts:
        conn.close()
        return False

    vals.append(request_id)
    conn.execute(
        f"UPDATE requests SET {', '.join(set_parts)} WHERE id=?",
        vals
    )
    conn.commit()
    conn.close()
    return True
