"""Транзакционное применение серверного плана Excel-справочника."""
from services.phonebook_excel_plan import build_phonebook_plan

ORG_FIELDS = ('name', 'address', 'inn')
CONTACT_FIELDS = (
    'org_id', 'full_name', 'position', 'room', 'phone_work',
    'phone_ext', 'phone_personal', 'email', 'inn', 'notes',
)


class PhonebookApplyError(ValueError):
    """Пакет не применён; требуется исправление или новый предпросмотр."""


def apply_phonebook_plan(conn, parsed, approved_plan, user_id,
                         log_action, resolutions=None):
    """Применяет доверенный серверный план одной собственной транзакцией.

    Требует свободного conn. При успехе commit, при ошибке rollback.
    conn не закрывает. log_action передаётся вызывающим кодом.
    approved_plan нельзя получать из клиентской формы. Авторизация,
    одноразовый токен и подтверждение пользователя обеспечиваются route.
    """
    if conn.in_transaction:
        raise PhonebookApplyError('Соединение уже имеет активную транзакцию')
    if not isinstance(user_id, int) or isinstance(user_id, bool) or user_id <= 0:
        raise PhonebookApplyError('Некорректный пользователь')
    if not approved_plan.get('can_apply'):
        raise PhonebookApplyError('План содержит ошибки или конфликты')
    conn.execute('BEGIN IMMEDIATE')
    try:
        plan = build_phonebook_plan(conn, parsed, resolutions)
        if not plan.get('can_apply') or plan != approved_plan:
            raise PhonebookApplyError(
                'Данные или решения изменились. Повторите предпросмотр.'
            )
        org_map = {}
        counts = {kind: {'create': 0, 'update': 0, 'delete': 0}
                  for kind in ('organizations', 'contacts')}

        def audit(kind, item):
            detail = (
                f"Excel-справочник: {kind}, строка {item['row']}, "
                f"действие {item['action']}, ID {item['id']}"
            )
            action = 'edit' if item['action'] == 'update' else item['action']
            if log_action(conn, user_id, action, None, detail) is not True:
                raise PhonebookApplyError('Не удалось записать журнал действий')

        def write(kind, item, data, fields, table):
            action = item['action']
            if action == 'unchanged':
                return item['id']
            if action == 'create':
                columns = ', '.join(fields)
                placeholders = ', '.join('?' for _ in fields)
                cur = conn.execute(
                    f'INSERT INTO {table} ({columns}) VALUES ({placeholders})',
                    tuple(data[f] for f in fields),
                )
                record_id = cur.lastrowid
            elif action == 'update':
                assignments = ', '.join(f'{f}=?' for f in fields)
                cur = conn.execute(
                    f'UPDATE {table} SET {assignments} WHERE id=?',
                    tuple(data[f] for f in fields) + (item['id'],),
                )
                record_id = item['id']
                if cur.rowcount != 1:
                    raise PhonebookApplyError('Обновляемая запись отсутствует')
            elif action == 'delete':
                cur = conn.execute(f'DELETE FROM {table} WHERE id=?', (item['id'],))
                record_id = item['id']
                if cur.rowcount != 1:
                    raise PhonebookApplyError('Удаляемая запись отсутствует')
            else:
                raise PhonebookApplyError('Неизвестная операция')
            audit(kind, dict(item, id=record_id))
            counts[kind][action] += 1
            return record_id

        for item in plan['organizations']:
            if item['action'] == 'delete':
                continue
            data = dict(item['after'])
            data['inn'] = data['inn'] or None
            oid = write('organizations', item, data, ORG_FIELDS, 'phonebook_orgs')
            if item['action'] == 'create':
                org_map[item['row']] = oid
        for item in plan['contacts']:
            data = dict(item['after']) if item['after'] is not None else {}
            if isinstance(data.get('org_id'), dict):
                row = data['org_id'].get('organization_row')
                if row not in org_map:
                    raise PhonebookApplyError('Новая организация не создана')
                data['org_id'] = org_map[row]
            oid = data.get('org_id')
            if item['action'] != 'delete' and oid is not None:
                if conn.execute('SELECT id FROM phonebook_orgs WHERE id=?', (oid,)).fetchone() is None:
                    raise PhonebookApplyError('Организация контакта отсутствует')
            write('contacts', item, data, CONTACT_FIELDS, 'phonebook')
        for item in plan['organizations']:
            if item['action'] != 'delete':
                continue
            if conn.execute('SELECT id FROM phonebook WHERE org_id=? LIMIT 1',
                            (item['id'],)).fetchone() is not None:
                raise PhonebookApplyError('У организации остаются контакты')
            write('organizations', item, {}, ORG_FIELDS, 'phonebook_orgs')
        conn.commit()
        return counts
    except Exception:
        conn.rollback()
        raise
