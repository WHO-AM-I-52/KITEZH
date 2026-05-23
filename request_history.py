# ╔══════════════════════════════════════════════════════════════╗
# ║ request_history.py                                           ║
# ║ История изменений обращений + откат                          ║
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
}

SKIP_FIELDS = {
    'updated_at', 'updated_by', 'created_at', 'created_by',
    'confirmed_at', 'confirmed_by', 'id'
}


def save_history(conn, request_id: int, changed_by: int, old_row, new_row) -> None:
    """
    Сравнивает старое и новое состояние обращения,
    сохраняет только изменённые поля в таблицу request_history.
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
        return  # ничего не изменилось — не пишем запись

    conn.execute(
        "INSERT INTO request_history (request_id, changed_by, changes) VALUES (?,?,?)",
        (request_id, changed_by, json.dumps(changes, ensure_ascii=False))
    )


def get_history(request_id: int) -> list:
    """
    Возвращает список записей истории для обращения,
    с человекочитаемыми названиями полей.
    """
    conn = get_db()
    rows = conn.execute(
        "SELECT h.id, h.changed_at, u.full_name, h.changes "
        "FROM request_history h "
        "LEFT JOIN users u ON h.changed_by = u.id "
        "WHERE h.request_id=? ORDER BY h.changed_at DESC",
        (request_id,)
    ).fetchall()
    conn.close()

    result = []
    for row in rows:
        try:
            raw = json.loads(row['changes'])
        except Exception:
            raw = {}

        # Подменяем технические ключи на читаемые метки
        labeled = {FIELD_LABELS.get(k, k): v for k, v in raw.items()}

        result.append({
            'id':         row['id'],
            'changed_at': row['changed_at'],
            'user':       row['full_name'] or '—',
            'action':     'edit',
            'changes':    labeled,
        })
    return result


def rollback_history(history_id: int, request_id: int) -> bool:
    """
    Откатывает обращение к состоянию ДО выбранной правки:
    берёт поле [0] (старое значение) из каждого изменения
    и применяет UPDATE к таблице requests.
    Возвращает True если откат выполнен, False если запись не найдена.
    """
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM request_history WHERE id=? AND request_id=?",
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
    for field, (old_val, _) in changes.items():
        # Пропускаем служебные поля — их не откатываем
        if field in SKIP_FIELDS:
            continue
        # Ищем оригинальный технический ключ (обратный маппинг из FIELD_LABELS)
        tech_key = field
        for k, v in FIELD_LABELS.items():
            if v == field:
                tech_key = k
                break
        set_parts.append(f"{tech_key}=?")
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