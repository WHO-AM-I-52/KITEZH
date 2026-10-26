# phonebook_routes.py
# Blueprint: телефонный справочник (v3.0.0 — единый справочник адресатов)
# Маршруты:
#   GET  /phonebook                — список сотрудников с поиском  [can_view_phonebook]
#   GET  /phonebook/search         — AJAX: поиск, возвращает JSON  [can_view_phonebook]
#   POST /phonebook/add            — добавить контакт            [авторизован]
#   POST /phonebook/edit           — редактировать контакт        (админ)
#   POST /phonebook/delete         — удалить контакт              (админ)
#   POST /phonebook/archive        — архивировать контакт         (админ)
#   POST /phonebook/restore        — восстановить контакт         (админ)
#   GET  /phonebook/orgs           — список организаций           (админ)
#   POST /phonebook/orgs/add       — добавить организацию          [авторизован]
#   POST /phonebook/orgs/edit      — редактировать организацию     (админ)
#   POST /phonebook/orgs/delete    — удалить организацию           (админ, без каскада)
#   POST /phonebook/orgs/archive   — архивировать организацию     (админ)
#   POST /phonebook/orgs/restore  — восстановить организацию      (админ)
#   GET  /phonebook/org_address    — AJAX: получить адрес организации
#
# Единый справочник адресатов (этап «единый справочник»):
#   - создание карточек доступно любому авторизованному пользователю;
#   - изменение существующих общих карточек, архивирование,
#     восстановление и удаление — только администратору;
#   - удаление организации/контакта с зависимостями (письма, связи)
#     запрещено: предлагается архивирование;
#   - каскадное удаление людей вместе с организацией убрано;
#   - JSON-эндпоинты поиска/создания для формы письма (CSRF).
#
# Issue #PB-1 (v2.8.0):
#   sync_request_to_phonebook() — авто-создание орг. и контакта
#   из формы обращения по нажатию чекбокса «Добавить в справочник».
#   Теперь идёт через общий сервис contact_directory.

from hmac import compare_digest
import secrets

from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, jsonify, session)
from db import get_db
from core.auth_utils import login_required, admin_required, permission_required
from core.activity_log import log_action
from services import contact_directory
from services.contact_directory import DirectoryError

phonebook_bp = Blueprint('phonebook', __name__)


def get_all_orgs():
    conn = get_db()
    orgs = conn.execute("SELECT * FROM phonebook_orgs ORDER BY name").fetchall()
    conn.close()
    return orgs


def _row_matches(row, tokens: list[str]) -> bool:
    """Проверяет, содержит ли запись ВСЕ токены (регистронезависимо, включая кириллицу)."""
    haystack = ' '.join([
        row['full_name']     or '',
        row['position']      or '',
        row['org_name']      or '',
        row['phone_work']    or '',
        row['phone_ext']     or '',
        row['phone_personal'] or '',
        row['email']         or '',
        row['room']          or '',
        row['notes']         or '',
        row['inn']           or '',
    ]).lower()
    return all(t in haystack for t in tokens)


def get_all_contacts(search: str = ''):
    conn = get_db()
    rows = conn.execute("""
        SELECT p.*, o.name AS org_name, o.address AS org_address
        FROM phonebook p
        LEFT JOIN phonebook_orgs o ON p.org_id = o.id
        WHERE p.archived = 0 AND (o.id IS NULL OR o.archived = 0)
        ORDER BY o.name, p.full_name
    """).fetchall()
    conn.close()

    if search:
        tokens = search.lower().split()
        rows = [r for r in rows if _row_matches(r, tokens)]

    return rows


def get_contact_employments():
    """Активные места работы для отображения на карточках."""
    conn = get_db()
    rows = conn.execute("""
        SELECT e.contact_id, e.org_id, e.position, o.name AS org_name
        FROM phonebook_employment e
        LEFT JOIN phonebook_orgs o ON o.id = e.org_id
        WHERE e.archived = 0 AND (o.id IS NULL OR o.archived = 0)
    """).fetchall()
    conn.close()
    result = {}
    for r in rows:
        result.setdefault(r['contact_id'], []).append(dict(r))
    return result


@phonebook_bp.route('/phonebook')
@login_required
@permission_required('can_view_phonebook')
def phonebook():
    search   = request.args.get('q', '').strip()
    contacts = get_all_contacts(search)
    orgs     = get_all_orgs()
    employments = get_contact_employments()
    groups   = {}
    for c in contacts:
        org = c['org_name'] or '—'
        groups.setdefault(org, []).append(c)
    return render_template('phonebook.html',
                           groups=groups, orgs=orgs,
                           employments=employments,
                           search=search, total=len(contacts))


@phonebook_bp.route('/phonebook/search')
@login_required
@permission_required('can_view_phonebook')
def phonebook_search():
    """AJAX-эндпойнт: возвращает JSON для live-поиска."""
    search   = request.args.get('q', '').strip()
    contacts = get_all_contacts(search)
    groups   = {}
    for c in contacts:
        org = c['org_name'] or '—'
        if org not in groups:
            groups[org] = {'address': c['org_address'] or '', 'contacts': []}
        groups[org]['contacts'].append({
            'id':             c['id'],
            'full_name':      c['full_name'],
            'position':       c['position'] or '',
            'room':           c['room'] or '',
            'phone_work':     c['phone_work'] or '',
            'phone_ext':      c['phone_ext'] or '',
            'phone_personal': c['phone_personal'] or '',
            'email':          c['email'] or '',
            'inn':            c['inn'] or '',
            'notes':          c['notes'] or '',
            'org_name':       c['org_name'] or '',
        })
    return jsonify({'groups': groups, 'total': len(contacts), 'query': search})


@phonebook_bp.route('/phonebook/add', methods=['POST'])
@login_required
def phonebook_add():
    """Создание карточки контакта доступно любому авторизованному."""
    d    = request.form
    name = d.get('full_name', '').strip()
    org_id = d.get('org_id') or None
    if org_id:
        org_id = int(org_id)

    conn = get_db()
    try:
        # Предупреждение о совпадениях: точный дубль не создаём,
        # однофамильцев в других организациях — создаём с предупреждением.
        candidates = contact_directory.find_person_candidates(conn, name)
        same_org = [c for c in candidates if org_id is not None
                    and c['org_id'] == org_id]
        if same_org:
            flash(f'Контакт «{name}» уже есть в этой организации '
                  f'(id {same_org[0]["id"]}). Выберите существующую карточку.',
                  'warning')
            return redirect(url_for('phonebook.phonebook'))
        if candidates:
            names = ', '.join(f'«{c["full_name"]}» ({c["org_name"] or "без орг."})'
                              for c in candidates[:5])
            flash(f'Внимание: найдены однофамильцы: {names}. '
                  f'Убедитесь, что это другой человек.', 'warning')

        contact_directory.create_contact(
            conn, name, org_id=org_id,
            position=d.get('position', '').strip(),
            room=d.get('room', '').strip(),
            phone_work=d.get('phone_work', '').strip(),
            phone_ext=d.get('phone_ext', '').strip(),
            phone_personal=d.get('phone_personal', '').strip(),
            email=d.get('email', '').strip(),
            inn=d.get('inn', '').strip(),
            notes=d.get('notes', '').strip(),
            user_id=session['user_id'],
        )
        log_action(conn, session['user_id'], 'create', None,
                   f'Справочник: добавлен сотрудник «{name}»')
        conn.commit()
        flash('Сотрудник добавлен', 'success')
    except DirectoryError as exc:
        conn.rollback()
        flash(str(exc), 'error')
    finally:
        conn.close()
    return redirect(url_for('phonebook.phonebook'))


@phonebook_bp.route('/phonebook/edit', methods=['POST'])
@login_required
@admin_required
def phonebook_edit():
    d    = request.form
    cid  = d.get('contact_id')
    name = d.get('full_name', '').strip()
    org_id = d.get('org_id') or None
    if org_id:
        org_id = int(org_id)

    conn = get_db()
    try:
        # Смена места работы: прежняя связь сохраняется,
        # выбирается/создаётся новая (исправление опечатки админом
        # не переписывает историю).
        contact_directory.set_main_employment(
            conn, cid, org_id, d.get('position', '').strip(),
            user_id=session['user_id'],
        )
        conn.execute("""
            UPDATE phonebook SET
                room          = ?,
                full_name     = ?,
                phone_work    = ?,
                phone_ext     = ?,
                phone_personal= ?,
                email         = ?,
                inn           = ?,
                notes         = ?
            WHERE id = ?
        """, (
            d.get('room', '').strip(),
            name,
            d.get('phone_work', '').strip(),
            d.get('phone_ext', '').strip(),
            d.get('phone_personal', '').strip(),
            d.get('email', '').strip(),
            d.get('inn', '').strip(),
            d.get('notes', '').strip(),
            cid,
        ))
        log_action(conn, session['user_id'], 'edit', None,
                   f'Справочник: изменён сотрудник «{name}»')
        conn.commit()
        flash('Данные сотрудника обновлены', 'success')
    except DirectoryError as exc:
        conn.rollback()
        flash(str(exc), 'error')
    finally:
        conn.close()
    return redirect(url_for('phonebook.phonebook'))


@phonebook_bp.route('/phonebook/delete', methods=['POST'])
@login_required
@admin_required
def phonebook_delete():
    cid  = request.form.get('contact_id')
    conn = get_db()
    try:
        contact_directory.delete_contact(conn, cid, user_id=session['user_id'])
        row = conn.execute(
            "SELECT full_name FROM phonebook WHERE id=?", (cid,)
        ).fetchone()
        name = row['full_name'] if row else f'ID:{cid}'
        log_action(conn, session['user_id'], 'delete', None,
                   f'Справочник: удалён сотрудник «{name}»')
        conn.commit()
        flash('Сотрудник удалён', 'success')
    except DirectoryError as exc:
        flash(str(exc), 'error')
    finally:
        conn.close()
    return redirect(url_for('phonebook.phonebook'))


@phonebook_bp.route('/phonebook/archive', methods=['POST'])
@login_required
@admin_required
def phonebook_archive():
    cid = request.form.get('contact_id')
    conn = get_db()
    try:
        contact_directory.archive_contact(conn, cid, user_id=session['user_id'])
        log_action(conn, session['user_id'], 'edit', None,
                   f'Справочник: контакт id={cid} архивирован')
        conn.commit()
        flash('Контакт архивирован', 'success')
    except DirectoryError as exc:
        flash(str(exc), 'error')
    finally:
        conn.close()
    return redirect(url_for('phonebook.phonebook'))


@phonebook_bp.route('/phonebook/restore', methods=['POST'])
@login_required
@admin_required
def phonebook_restore():
    cid = request.form.get('contact_id')
    conn = get_db()
    try:
        contact_directory.restore_contact(conn, cid, user_id=session['user_id'])
        log_action(conn, session['user_id'], 'edit', None,
                   f'Справочник: контакт id={cid} восстановлен из архива')
        conn.commit()
        flash('Контакт восстановлен', 'success')
    except DirectoryError as exc:
        flash(str(exc), 'error')
    finally:
        conn.close()
    return redirect(url_for('phonebook.phonebook'))


@phonebook_bp.route('/phonebook/orgs')
@login_required
@admin_required
def phonebook_orgs():
    conn  = get_db()
    orgs_raw = conn.execute("SELECT * FROM phonebook_orgs ORDER BY name").fetchall()
    counts   = {
        row['org_id']: row['cnt']
        for row in conn.execute(
            "SELECT org_id, COUNT(*) AS cnt FROM phonebook"
            " WHERE archived = 0 GROUP BY org_id"
        ).fetchall()
    }
    conn.close()
    orgs = [dict(o) | {'employee_count': counts.get(o['id'], 0)} for o in orgs_raw]
    return render_template('phonebook_orgs.html', orgs=orgs)


@phonebook_bp.route('/phonebook/orgs/add', methods=['POST'])
@login_required
def phonebook_orgs_add():
    """Создание организации доступно любому авторизованному."""
    name    = request.form.get('name', '').strip()
    address = request.form.get('address', '').strip()
    inn     = request.form.get('inn', '').strip() or None
    if not name:
        flash('Укажите название организации', 'error')
        return redirect(url_for('phonebook.phonebook_orgs'))

    conn = get_db()
    try:
        result = contact_directory.create_org(
            conn, name, address=address, inn=inn, user_id=session['user_id']
        )
        if result['status'] == 'exists':
            org = result['org']
            flash(f'Организация «{org["name"]}» уже существует (id {org["id"]})',
                  'warning')
        else:
            log_action(conn, session['user_id'], 'create', None,
                       f'Справочник орг.: добавлена «{name}»')
            conn.commit()
            flash(f'Организация «{name}» добавлена', 'success')
    except DirectoryError as exc:
        flash(str(exc), 'error')
    finally:
        conn.close()
    return redirect(url_for('phonebook.phonebook_orgs'))


@phonebook_bp.route('/phonebook/orgs/edit', methods=['POST'])
@login_required
@admin_required
def phonebook_orgs_edit():
    oid     = request.form.get('org_id')
    name    = request.form.get('name', '').strip()
    address = request.form.get('address', '').strip()
    conn    = get_db()
    conn.execute(
        "UPDATE phonebook_orgs SET name=?, address=? WHERE id=?",
        (name, address, oid)
    )
    log_action(conn, session['user_id'], 'edit', None,
               f'Справочник орг.: изменена «{name}»')
    conn.commit()
    conn.close()
    flash('Организация обновлена', 'success')
    return redirect(url_for('phonebook.phonebook_orgs'))


@phonebook_bp.route('/phonebook/orgs/delete', methods=['POST'])
@login_required
@admin_required
def phonebook_orgs_delete():
    """Удаление организации без каскадного удаления людей.

    При зависимостях (письма, контакты) удаление запрещено —
    предлагается архивирование.
    """
    oid  = request.form.get('org_id')
    conn = get_db()

    row = conn.execute(
        "SELECT name FROM phonebook_orgs WHERE id=?", (oid,)
    ).fetchone()
    if not row:
        flash('Организация не найдена', 'error')
        conn.close()
        return redirect(url_for('phonebook.phonebook_orgs'))

    name = row['name']
    try:
        contact_directory.delete_org(conn, oid, user_id=session['user_id'])
        log_action(conn, session['user_id'], 'delete', None,
                   f'Справочник орг.: удалена «{name}»')
        conn.commit()
        flash(f'Организация «{name}» удалена', 'success')
    except DirectoryError as exc:
        flash(f'«{name}»: {exc}', 'error')
    finally:
        conn.close()
    return redirect(url_for('phonebook.phonebook_orgs'))


@phonebook_bp.route('/phonebook/orgs/archive', methods=['POST'])
@login_required
@admin_required
def phonebook_orgs_archive():
    oid = request.form.get('org_id')
    conn = get_db()
    try:
        contact_directory.archive_org(conn, oid, user_id=session['user_id'])
        log_action(conn, session['user_id'], 'edit', None,
                   f'Справочник орг.: организация id={oid} архивирована')
        conn.commit()
        flash('Организация архивирована', 'success')
    except DirectoryError as exc:
        flash(str(exc), 'error')
    finally:
        conn.close()
    return redirect(url_for('phonebook.phonebook_orgs'))


@phonebook_bp.route('/phonebook/orgs/restore', methods=['POST'])
@login_required
@admin_required
def phonebook_orgs_restore():
    oid = request.form.get('org_id')
    conn = get_db()
    try:
        contact_directory.restore_org(conn, oid, user_id=session['user_id'])
        log_action(conn, session['user_id'], 'edit', None,
                   f'Справочник орг.: организация id={oid} восстановлена')
        conn.commit()
        flash('Организация восстановлена', 'success')
    except DirectoryError as exc:
        flash(str(exc), 'error')
    finally:
        conn.close()
    return redirect(url_for('phonebook.phonebook_orgs'))


@phonebook_bp.route('/phonebook/org_address')
@login_required
def org_address():
    oid = request.args.get('org_id')
    conn = get_db()
    row  = conn.execute(
        "SELECT address FROM phonebook_orgs WHERE id=?", (oid,)
    ).fetchone()
    conn.close()
    return jsonify({'address': row['address'] if row else ''})


# ─── JSON-эндпоинты для формы письма (единый справочник) ─────────────────────

def _dir_csrf_token():
    if not session.get('dir_csrf'):
        session['dir_csrf'] = secrets.token_hex(32)
    return session['dir_csrf']


@phonebook_bp.app_context_processor
def _dir_context():
    return {'dir_csrf_token': _dir_csrf_token}


def _dir_check_csrf():
    expected = session.get('dir_csrf', '')
    supplied = (request.headers.get('X-Dir-CSRF')
                or request.form.get('dir_csrf')
                or (request.get_json(silent=True) or {}).get('dir_csrf')
                or '')
    if not expected or not compare_digest(expected, supplied):
        return False
    return True


@phonebook_bp.route('/phonebook/api/search')
@login_required
def api_search():
    """Минимальный поиск адресатов для писем: без личных телефонов.

    Полный просмотр справочника по-прежнему требует can_view_phonebook.
    """
    q = request.args.get('q', '').strip()
    conn = get_db()
    try:
        results = contact_directory.search_directory(conn, q)
    finally:
        conn.close()
    return jsonify(results)


@phonebook_bp.route('/phonebook/api/orgs', methods=['POST'])
@login_required
def api_create_org():
    if not _dir_check_csrf():
        return jsonify({'error': 'CSRF token недействителен'}), 400
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'Для организации необходимо название'}), 400

    conn = get_db()
    try:
        result = contact_directory.create_org(
            conn, name,
            address=(data.get('address') or '').strip(),
            inn=(data.get('inn') or '').strip() or None,
            user_id=session['user_id'],
        )
        if result['status'] == 'exists':
            org = result['org']
            conn.rollback()
            return jsonify({
                'status': 'exists', 'org_id': org['id'],
                'name': org['name'],
                'message': 'Организация уже есть в справочнике — выбрана она',
            })
        log_action(conn, session['user_id'], 'create', None,
                   f'Справочник орг.: добавлена «{name}» (из формы письма)')
        conn.commit()
        return jsonify({'status': 'created', 'org_id': result['org_id'],
                        'name': name}), 201
    except DirectoryError as exc:
        conn.rollback()
        return jsonify({'error': str(exc)}), 400
    finally:
        conn.close()


@phonebook_bp.route('/phonebook/api/contacts', methods=['POST'])
@login_required
def api_create_contact():
    if not _dir_check_csrf():
        return jsonify({'error': 'CSRF token недействителен'}), 400
    data = request.get_json(silent=True) or {}
    full_name = (data.get('full_name') or '').strip()
    org_id = data.get('org_id') or None
    if org_id:
        org_id = int(org_id)
    if not full_name:
        return jsonify({'error': 'Для контакта необходимо ФИО'}), 400

    conn = get_db()
    try:
        # Совпадения не объединяем: точный дубль возвращаем как existing.
        candidates = contact_directory.find_person_candidates(conn, full_name)
        same_org = [c for c in candidates
                    if org_id is not None and c['org_id'] == org_id]
        if same_org:
            c = same_org[0]
            conn.rollback()
            return jsonify({
                'status': 'exists', 'contact_id': c['id'],
                'full_name': c['full_name'],
                'message': 'Такой контакт уже есть в этой организации — выбран он',
            })
        if candidates:
            names = '; '.join(f'{c["full_name"]} ({c["org_name"] or "без орг."})'
                              for c in candidates[:5])
            return jsonify({
                'error': 'Найдены однофамильцы. Выберите существующего '
                         f'или создайте отдельного человека: {names}',
                'candidates': [{'contact_id': c['id'], 'full_name': c['full_name'],
                               'org_name': c['org_name'] or ''} for c in candidates],
            }), 409

        result = contact_directory.create_contact(
            conn, full_name, org_id=org_id,
            position=(data.get('position') or '').strip(),
            room=(data.get('room') or '').strip(),
            phone_work=(data.get('phone_work') or '').strip(),
            phone_ext=(data.get('phone_ext') or '').strip(),
            phone_personal=(data.get('phone_personal') or '').strip(),
            email=(data.get('email') or '').strip(),
            inn=(data.get('inn') or '').strip(),
            notes=(data.get('notes') or '').strip(),
            user_id=session['user_id'],
        )
        log_action(conn, session['user_id'], 'create', None,
                   f'Справочник: добавлен контакт «{full_name}» (из формы письма)')
        conn.commit()
        return jsonify({'status': 'created',
                        'contact_id': result['contact_id'],
                        'employment_id': result['employment_id'],
                        'full_name': full_name}), 201
    except DirectoryError as exc:
        conn.rollback()
        return jsonify({'error': str(exc)}), 400
    finally:
        conn.close()


# ── Issue #PB-1: Синхронизация из формы обращения ────────────────────────────
def sync_request_to_phonebook(conn, form_data, request_id: int, user_id: int) -> None:
    """
    Создаёт организацию и контакт в справочнике по данным формы обращения.

    Вызывается из form_routes.py ПОСЛЕ conn.commit() основного обращения.
    conn.commit() здесь НЕ вызывается — caller делает отдельный commit.
    Идёт через общий сервис contact_directory: дедупликация организации
    по ИНН (приоритет) или точному имени, контакта — по (org_id, full_name).
    """
    short_name    = (form_data.get('applicant_short_name') or '').strip()
    legal_form    = (form_data.get('applicant_legal_form') or '').strip()
    full_name_org = (form_data.get('applicant_full_name')  or '').strip()
    address       = (form_data.get('legal_address')        or '').strip()
    contact_name  = (form_data.get('contact_person')       or '').strip()
    contact_pos   = (form_data.get('contact_position')     or '').strip()
    phone_work    = (form_data.get('contact_phone')        or '').strip()
    email         = (form_data.get('contact_email')        or '').strip()
    inn           = (form_data.get('applicant_inn')        or '').strip()

    if short_name:
        org_name = short_name
    else:
        org_name = f"{legal_form} {full_name_org}".strip()

    if not org_name:
        flash(
            'Организация не добавлена в справочник: '
            'не заполнено краткое или полное наименование заявителя.',
            'warning'
        )
        return

    try:
        org_result = contact_directory.create_org(
            conn, org_name, address=address, inn=inn or None, user_id=user_id
        )
        if org_result['status'] == 'created':
            org_id = org_result['org_id']
            inn_display = inn if inn else '—'
            log_action(conn, user_id, 'create', request_id,
                       f'Справочник орг.: добавлена «{org_name}» '
                       f'(ИНН: {inn_display}) из обращения #{request_id}')
        else:
            org_id = org_result['org']['id']

        if contact_name:
            exists = conn.execute(
                "SELECT id FROM phonebook WHERE org_id = ? AND full_name = ?",
                (org_id, contact_name)
            ).fetchone()

            if not exists:
                result = contact_directory.create_contact(
                    conn, contact_name, org_id=org_id, position=contact_pos,
                    phone_work=phone_work, email=email, user_id=user_id,
                )
                log_action(conn, user_id, 'create', request_id,
                           f'Справочник: добавлен контакт «{contact_name}» '
                           f'({org_name}) из обращения #{request_id}')
    except DirectoryError as exc:
        flash(f'Не удалось добавить в справочник: {exc}', 'warning')
