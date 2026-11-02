"""Транзакционное применение серверного плана Excel-справочника.

Создание/удаление организаций и контактов и смена занятости выполняются
через сервис общего справочника (services.contact_directory): одинаковые
бизнес-правила для UI и Excel. Прежние связи занятости не закрываются.
"""
from services import contact_directory
from services.contact_directory import DirectoryError
from services.phonebook_excel_plan import build_phonebook_plan

ORG_FIELDS = ('name', 'address', 'inn')
CONTACT_FIELDS = (
    'org_id', 'full_name', 'position', 'room', 'phone_work',
    'phone_ext', 'phone_personal', 'email', 'inn', 'notes',
)
# Поля контакта вне занятости: пишутся напрямую, занятость — сервисом.
CONTACT_DETAIL_FIELDS = (
    'full_name', 'room', 'phone_work', 'phone_ext',
    'phone_personal', 'email', 'inn', 'notes',
)


class PhonebookApplyError(ValueError):
    """Пакет не применён; требуется исправление или новый предпросмотр."""


def _fail(exc):
    raise PhonebookApplyError(str(exc)) from None


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

        def _apply_org(item):
            """Организации: создание и удаление — сервис, поля — напрямую."""
            data = dict(item['after'])
            data['inn'] = data['inn'] or None
            if item['action'] == 'create':
                try:
                    result = contact_directory.create_org(
                        conn, data['name'], address=data['address'] or '',
                        inn=data['inn'], user_id=user_id,
                    )
                except DirectoryError as exc:
                    _fail(exc)
                if result.get('status') != 'created':
                    raise PhonebookApplyError(
                        f"Организация уже существует: {data['name']}"
                    )
                return result['org_id']
            if item['action'] == 'update':
                assignments = ', '.join(f'{f}=?' for f in ORG_FIELDS)
                cur = conn.execute(
                    f'UPDATE phonebook_orgs SET {assignments} WHERE id=?',
                    tuple(data[f] for f in ORG_FIELDS) + (item['id'],),
                )
                if cur.rowcount != 1:
                    raise PhonebookApplyError('Обновляемая запись отсутствует')
            return item['id']

        def _apply_contact(item):
            """Контакты: создание/удаление и занятость — сервис."""
            if item['action'] == 'delete':
                try:
                    contact_directory.delete_contact(
                        conn, item['id'], user_id=user_id)
                except DirectoryError as exc:
                    _fail(exc)
                return item['id']
            data = dict(item['after'])
            if isinstance(data.get('org_id'), dict):
                row = data['org_id'].get('organization_row')
                if row not in org_map:
                    raise PhonebookApplyError('Новая организация не создана')
                data['org_id'] = org_map[row]
            if item['action'] == 'create':
                try:
                    result = contact_directory.create_contact(
                        conn, data['full_name'], org_id=data.get('org_id'),
                        position=data.get('position', ''),
                        room=data.get('room', ''),
                        phone_work=data.get('phone_work', ''),
                        phone_ext=data.get('phone_ext', ''),
                        phone_personal=data.get('phone_personal', ''),
                        email=data.get('email', ''),
                        inn=data.get('inn', ''),
                        notes=data.get('notes', ''),
                        user_id=user_id,
                    )
                except DirectoryError as exc:
                    _fail(exc)
                return result['contact_id']
            # update: смена организации/должности — сервис занятости,
            # остальные поля — прямое обновление.
            before = item['before']
            org_changed = (data.get('org_id') or None) != (before.get('org_id') or None)
            position_changed = (data.get('position') or '') != (before.get('position') or '')
            if org_changed or position_changed:
                try:
                    contact_directory.set_main_employment(
                        conn, item['id'], data.get('org_id'),
                        data.get('position', ''), user_id=user_id,
                    )
                except DirectoryError as exc:
                    _fail(exc)
            assignments = ', '.join(f'{f}=?' for f in CONTACT_DETAIL_FIELDS)
            cur = conn.execute(
                f'UPDATE phonebook SET {assignments} WHERE id=?',
                tuple(data.get(f) or '' for f in CONTACT_DETAIL_FIELDS)
                + (item['id'],),
            )
            if cur.rowcount != 1:
                raise PhonebookApplyError('Обновляемая запись отсутствует')
            return item['id']

        # 1. Организации без удалений (создание/обновление).
        for item in plan['organizations']:
            if item['action'] == 'delete':
                continue
            org_id = _apply_org(item)
            if item['action'] != 'unchanged':
                audit('organizations', dict(item, id=org_id))
                counts['organizations'][item['action']] += 1
            if item['action'] == 'create':
                org_map[item['row']] = org_id
        # 2. Контакты (включая удаления — до удаления организаций).
        for item in plan['contacts']:
            if item['action'] == 'unchanged':
                continue
            record_id = _apply_contact(item)
            audit('contacts', dict(item, id=record_id))
            counts['contacts'][item['action']] += 1
        # 3. Удаления организаций — в конце, с проверкой зависимостей.
        for item in plan['organizations']:
            if item['action'] != 'delete':
                continue
            try:
                contact_directory.delete_org(
                    conn, item['id'], user_id=user_id)
            except DirectoryError as exc:
                _fail(exc)
            audit('organizations', item)
            counts['organizations']['delete'] += 1
        conn.commit()
        return counts
    except Exception:
        conn.rollback()
        raise
