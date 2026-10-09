"""Read-only планирование изменений справочника; запись выполняет другой слой."""
from collections import Counter, defaultdict

ORG_FIELDS = ('name', 'address', 'inn')
CONTACT_FIELDS = (
    'org_id', 'full_name', 'position', 'room', 'phone_work',
    'phone_ext', 'phone_personal', 'email', 'inn', 'notes',
)


def _norm(value):
    return ' '.join((value or '').split()).casefold()


def _snapshot(conn, table, fields):
    cursor = conn.execute('SELECT id, ' + ', '.join(fields) + ' FROM ' + table)
    names = [column[0] for column in cursor.description]
    return {row[0]: dict(zip(names, row)) for row in cursor.fetchall()}


def build_phonebook_plan(conn, parsed, resolutions=None):
    """resolutions: {'org:2': {'id': 1}, 'contact:3': {'create': True}}.

    Решения проверяются заново. Новая организация представлена ссылкой
    {'organization_row': N}, не пользовательским временным ключом.
    При errors/conflicts применение запрещено. conn не закрывается.
    """
    resolutions = resolutions or {}
    plan = {
        'organizations': [], 'contacts': [],
        'errors': list(parsed.get('errors', [])),
        'warnings': list(parsed.get('warnings', [])), 'conflicts': [],
    }
    if plan['errors']:
        plan['can_apply'] = False
        return plan
    orgs = _snapshot(conn, 'phonebook_orgs', ORG_FIELDS)
    contacts = _snapshot(conn, 'phonebook', CONTACT_FIELDS)
    plan['snapshot'] = {'organizations': list(orgs.values()), 'contacts': list(contacts.values())}

    def error(kind, row, text):
        plan['errors'].append({'sheet': kind, 'row': row, 'message': text})

    def choose(kind, r, candidates, allow_create=True):
        key = f"{kind}:{r['row']}"
        decision = resolutions.get(key)
        database = orgs if kind == 'org' else contacts
        if r.get('id') is not None:
            if decision:
                error(kind, r['row'], 'Решение не допускается для строки с ID')
            if r['id'] not in database:
                error(kind, r['row'], 'ID отсутствует в базе')
                return False, None
            return True, r['id']
        if r['action'] == 'delete':
            error(kind, r['row'], 'Удаление требует ID')
            return False, None
        if decision:
            if set(decision) == {'id'} and decision['id'] in candidates:
                return True, decision['id']
            if allow_create and decision == {'create': True}:
                return True, None
            error(kind, r['row'], 'Недопустимое решение сопоставления')
            return False, None
        if len(candidates) > 1:
            plan['conflicts'].append({'key': key, 'candidates': candidates,
                                      'allow_create': allow_create})
            return False, None
        return True, candidates[0] if candidates else None

    def operation(kind, r, record_id, data, fields, database):
        before = database.get(record_id)
        if r['action'] == 'delete':
            action, changes = 'delete', {}
        else:
            changes = {f: {'before': before.get(f) if before else None, 'after': data[f]}
                       for f in fields if before is None or (before.get(f) or '') != (data[f] or '')}
            action = 'create' if before is None else ('update' if changes else 'unchanged')
        item = {'row': r['row'], 'id': record_id, 'action': action,
                'before': before, 'after': None if action == 'delete' else data,
                'changes': changes}
        plan[kind].append(item)
        return item

    aliases = defaultdict(list)
    org_targets = set()
    for r in parsed['organizations']:
        if r.get('id') is None:
            by_inn = [i for i, o in orgs.items() if r.get('inn') and o['inn'] == r['inn']]
            by_name = [i for i, o in orgs.items() if _norm(o['name']) == _norm(r['name'])]
            if by_inn and by_name and set(by_inn) != set(by_name):
                error('org', r['row'], 'ИНН и название указывают на разные организации')
                continue
            candidates = by_inn or by_name
            if not by_inn and r.get('inn') and any(
                orgs[i]['inn'] and orgs[i]['inn'] != r['inn'] for i in by_name
            ):
                error('org', r['row'], 'Название совпало, но ИНН отличается; нужен ID')
                continue
        else:
            candidates = []
        ok, oid = choose('org', r, candidates, False)
        if not ok:
            continue
        if oid is not None and oid in org_targets:
            error('org', r['row'], 'Несколько строк изменяют одну организацию')
            continue
        if oid is not None:
            org_targets.add(oid)
        data = {f: r.get(f) for f in ORG_FIELDS}
        item = operation('organizations', r, oid, data, ORG_FIELDS, orgs)
        ref = oid if oid is not None else {'organization_row': r['row']}
        if item['action'] != 'delete':
            names = [data['name']]
            if oid is not None:
                names.append(orgs[oid]['name'])
            for name in set(_norm(n) for n in names):
                if ref not in aliases[name]:
                    aliases[name].append(ref)
    final_orgs = {i: dict(o) for i, o in orgs.items()}
    for item in plan['organizations']:
        if item['action'] == 'delete':
            final_orgs.pop(item['id'], None)
        else:
            key = item['id'] if item['id'] is not None else f"row:{item['row']}"
            final_orgs[key] = item['after']
    for field in ('name', 'inn'):
        owners = defaultdict(list)
        for key, o in final_orgs.items():
            value = _norm(o[field]) if field == 'name' else o[field]
            if value:
                owners[value].append(key)
        for value, keys in owners.items():
            if len(keys) > 1:
                error('org', None, f'Неоднозначное/неуникальное поле {field}: {value}')
    deleted = {x['id'] for x in plan['organizations'] if x['action'] == 'delete'}
    for oid, o in orgs.items():
        if oid not in deleted and oid not in org_targets:
            aliases[_norm(o['name'])].append(oid)
    final_contacts = {i: dict(c) for i, c in contacts.items()}
    contact_targets = set()
    for r in parsed['contacts']:
        if r['action'] == 'delete':
            ref = None
        elif r.get('org_id') is not None:
            ref = r['org_id']
            if ref not in final_orgs:
                error('contact', r['row'], 'Организация не существует или удаляется')
                continue
            if r.get('org_name') and ref not in aliases.get(_norm(r['org_name']), []):
                error('contact', r['row'], 'ID и название организации противоречат друг другу')
                continue
        elif r.get('org_name'):
            matches = aliases.get(_norm(r['org_name']), [])
            if len(matches) != 1:
                error('contact', r['row'], 'Название организации не найдено или неоднозначно')
                continue
            ref = matches[0]
        else:
            ref = None
        candidates = [i for i, c in contacts.items()
                      if c['org_id'] == ref and _norm(c['full_name']) == _norm(r.get('full_name'))]
        ok, cid = choose('contact', r, candidates)
        if not ok:
            continue
        if cid is not None and cid in contact_targets:
            error('contact', r['row'], 'Несколько строк изменяют один контакт')
            continue
        if cid is not None:
            contact_targets.add(cid)
        data = {f: r.get(f, '') for f in CONTACT_FIELDS}
        data['org_id'] = ref
        item = operation('contacts', r, cid, data, CONTACT_FIELDS, contacts)
        if item['action'] == 'delete':
            final_contacts.pop(cid, None)
        else:
            key = cid if cid is not None else f"row:{r['row']}"
            final_contacts[key] = data
    for oid in deleted:
        if any(c['org_id'] == oid for c in final_contacts.values()):
            error('org', None, f'Организация ID={oid} остаётся с контактами')
    plan['summary'] = {
        kind: dict(Counter(x['action'] for x in plan[kind]))
        for kind in ('organizations', 'contacts')
    }
    plan['can_apply'] = not plan['errors'] and not plan['conflicts']
    return plan
