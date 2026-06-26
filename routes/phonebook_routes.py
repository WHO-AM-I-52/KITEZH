# phonebook_routes.py
# Blueprint: телефонный справочник (v2.9.0)
# Маршруты:
#   GET  /phonebook                — список сотрудников с поиском  [can_view_phonebook]
#   GET  /phonebook/search         — AJAX: поиск, возвращает JSON       [can_view_phonebook]
#   POST /phonebook/add            — добавить сотрудника (админ)
#   POST /phonebook/edit           — редактировать сотрудника (админ)
#   POST /phonebook/delete         — удалить сотрудника (админ)
#   GET  /phonebook/orgs           — список организаций (админ)
#   POST /phonebook/orgs/add       — добавить организацию (админ)
#   POST /phonebook/orgs/edit      — редактировать организацию (админ)
#   POST /phonebook/orgs/delete    — удалить организацию (админ)
#   GET  /phonebook/org_address    — AJAX: получить адрес организации
#
# ИСПРАВЛЕНИЕ v2.6.2:
#   SQLite LOWER() не работает с кириллицей (только ASCII).
#   Фильтрация перенесена на Python — используем str.lower() / casefold().
#
# Issue #PB-1 (v2.8.0):
#   Добавлена sync_request_to_phonebook() — авто-создание орг. и контакта
#   из формы обращения по нажатию чекбокса «Добавить в справочник».
#
# v2.8.1:
#   Поле inn добавлено в phonebook_add(), phonebook_edit(), phonebook_search() (#14)
#
# v2.8.2 (#76):
#   sync_request_to_phonebook() теперь передаёт applicant_inn → phonebook.inn
#
# v2.9.0:
#   phonebook_orgs_delete() — каскадное удаление с подтверждением.
#   При наличии сотрудников выводит предупреждение (confirm=1).
#
# v2.9.1 (fix #sync-fix-1):
#   sync_request_to_phonebook() — БАГ #1:
#   - Приоритет applicant_short_name для org_name (fallback: legal_form + full_name)
#   - При пустом org_name — flash warning вместо молчаливого return

from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, jsonify, session)
from db import get_db
from core.auth_utils import login_required, admin_required, permission_required
from core.activity_log import log_action

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
        ORDER BY o.name, p.full_name
    """).fetchall()
    conn.close()

    if search:
        tokens = search.lower().split()
        rows = [r for r in rows if _row_matches(r, tokens)]

    return rows


@phonebook_bp.route('/phonebook')
@login_required
@permission_required('can_view_phonebook')
def phonebook():
    search   = request.args.get('q', '').strip()
    contacts = get_all_contacts(search)
    orgs     = get_all_orgs()
    groups   = {}
    for c in contacts:
        org = c['org_name'] or '—'
        groups.setdefault(org, []).append(c)
    return render_template('phonebook.html',
                           groups=groups, orgs=orgs,
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
@admin_required
def phonebook_add():
    d    = request.form
    name = d.get('full_name', '').strip()
    conn = get_db()
    conn.execute("""
        INSERT INTO phonebook
            (org_id, position, room, full_name,
             phone_work, phone_ext, phone_personal, email, inn, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        d.get('org_id') or None,
        d.get('position', '').strip(),
        d.get('room', '').strip(),
        name,
        d.get('phone_work', '').strip(),
        d.get('phone_ext', '').strip(),
        d.get('phone_personal', '').strip(),
        d.get('email', '').strip(),
        d.get('inn', '').strip(),
        d.get('notes', '').strip(),
    ))
    log_action(conn, session['user_id'], 'create', None,
               f'Справочник: добавлен сотрудник «{name}»')
    conn.commit()
    conn.close()
    flash('Сотрудник добавлен', 'success')
    return redirect(url_for('phonebook.phonebook'))


@phonebook_bp.route('/phonebook/edit', methods=['POST'])
@login_required
@admin_required
def phonebook_edit():
    d    = request.form
    cid  = d.get('contact_id')
    name = d.get('full_name', '').strip()
    conn = get_db()
    conn.execute("""
        UPDATE phonebook SET
            org_id        = ?,
            position      = ?,
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
        d.get('org_id') or None,
        d.get('position', '').strip(),
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
    conn.close()
    flash('Данные сотрудника обновлены', 'success')
    return redirect(url_for('phonebook.phonebook'))


@phonebook_bp.route('/phonebook/delete', methods=['POST'])
@login_required
@admin_required
def phonebook_delete():
    cid  = request.form.get('contact_id')
    conn = get_db()
    row  = conn.execute(
        "SELECT full_name FROM phonebook WHERE id=?", (cid,)
    ).fetchone()
    name = row['full_name'] if row else f'ID:{cid}'
    conn.execute("DELETE FROM phonebook WHERE id=?", (cid,))
    log_action(conn, session['user_id'], 'delete', None,
               f'Справочник: удалён сотрудник «{name}»')
    conn.commit()
    conn.close()
    flash('Сотрудник удалён', 'success')
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
            "SELECT org_id, COUNT(*) AS cnt FROM phonebook GROUP BY org_id"
        ).fetchall()
    }
    conn.close()
    orgs = [dict(o) | {'employee_count': counts.get(o['id'], 0)} for o in orgs_raw]
    return render_template('phonebook_orgs.html', orgs=orgs)


@phonebook_bp.route('/phonebook/orgs/add', methods=['POST'])
@login_required
@admin_required
def phonebook_orgs_add():
    name    = request.form.get('name', '').strip()
    address = request.form.get('address', '').strip()
    if name:
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO phonebook_orgs (name, address) VALUES (?,?)",
                (name, address)
            )
            log_action(conn, session['user_id'], 'create', None,
                       f'Справочник орг.: добавлена «{name}»')
            conn.commit()
            flash(f'Организация «{name}» добавлена', 'success')
        except Exception:
            flash('Такая организация уже существует', 'error')
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
    oid     = request.form.get('org_id')
    confirm = request.form.get('confirm')
    conn    = get_db()

    row = conn.execute(
        "SELECT name FROM phonebook_orgs WHERE id=?", (oid,)
    ).fetchone()
    if not row:
        flash('Организация не найдена', 'error')
        conn.close()
        return redirect(url_for('phonebook.phonebook_orgs'))

    name  = row['name']
    count = conn.execute(
        "SELECT COUNT(*) FROM phonebook WHERE org_id=?", (oid,)
    ).fetchone()[0]

    if count > 0 and confirm != '1':
        # Сотрудники есть, подтверждение не получено — передаём данные в шаблондля модального окна
        conn.close()
        orgs_raw = get_all_orgs()
        conn2  = get_db()
        counts = {
            r['org_id']: r['cnt']
            for r in conn2.execute(
                "SELECT org_id, COUNT(*) AS cnt FROM phonebook GROUP BY org_id"
            ).fetchall()
        }
        conn2.close()
        orgs = [dict(o) | {'employee_count': counts.get(o['id'], 0)} for o in orgs_raw]
        return render_template(
            'phonebook_orgs.html',
            orgs=orgs,
            confirm_delete={'org_id': oid, 'org_name': name, 'count': count},
        )

    # Подтверждение получено или сотрудников нет — каскадно удаляем
    if count > 0:
        conn.execute("DELETE FROM phonebook WHERE org_id=?", (oid,))
        log_action(conn, session['user_id'], 'delete', None,
                   f'Справочник: каскадно удалено {count} сотрудников орг. «{name}»')

    conn.execute("DELETE FROM phonebook_orgs WHERE id=?", (oid,))
    log_action(conn, session['user_id'], 'delete', None,
               f'Справочник орг.: удалена «{name}»')
    conn.commit()
    conn.close()
    flash(f'Организация «{name}» удалена '
          + (f'(вместе с {count} сотрудниками)' if count > 0 else ''), 'success')
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


# ── Issue #PB-1: Синхронизация из формы обращения ────────────────────────────
def sync_request_to_phonebook(conn, form_data, request_id: int, user_id: int) -> None:
    """
    Создаёт организацию и контакт в справочнике по данным формы обращения.

    Вызывается из form_routes.py ПОСЛЕ conn.commit() основного обращения.
    conn.commit() здесь НЕ вызывается — caller делает отдельный commit:

        sync_request_to_phonebook(conn, request.form, new_id, session['user_id'])
        conn.commit()  # ← отдельный commit в form_routes.py

    Дедупликация:
      - организация: по точному совпадению name (legal_form + full_name)
      - контакт:     по (org_id, full_name)

    Маппинг:
      applicant_short_name (приоритет)                → phonebook_orgs.name
      applicant_legal_form + applicant_full_name       → phonebook_orgs.name (fallback)
      legal_address                                    → phonebook_orgs.address
      contact_person                                   → phonebook.full_name
      contact_position                                 → phonebook.position
      contact_phone                                    → phonebook.phone_work
      contact_email                                    → phonebook.email
      applicant_inn                                    → phonebook.inn

    v2.9.1 (fix #sync-fix-1):
      - БАГ #1 исправлен: приоритет applicant_short_name для org_name.
        Если он заполнен — используем его. Иначе собираем из legal_form + full_name.
      - При пустом org_name — flash warning вместо молчаливого return.
    """
    # ── Читаем поля формы ────────────────────────────────────────────────────
    short_name    = (form_data.get('applicant_short_name') or '').strip()
    legal_form    = (form_data.get('applicant_legal_form') or '').strip()
    full_name_org = (form_data.get('applicant_full_name')  or '').strip()
    address       = (form_data.get('legal_address')        or '').strip()
    contact_name  = (form_data.get('contact_person')       or '').strip()
    contact_pos   = (form_data.get('contact_position')     or '').strip()
    phone_work    = (form_data.get('contact_phone')        or '').strip()
    email         = (form_data.get('contact_email')        or '').strip()
    inn           = (form_data.get('applicant_inn')        or '').strip()

    # ── Строим название организации: short_name → legal_form+full_name ───────
    # fix #sync-fix-1: раньше функция молча делала return если оба поля пусты.
    # Теперь: приоритет short_name, fallback — legal_form + full_name.
    if short_name:
        org_name = short_name
    else:
        org_name = f"{legal_form} {full_name_org}".strip()

    if not org_name:
        # Название не удалось определить — предупреждаем пользователя явно
        flash(
            'Организация не добавлена в справочник: не заполнено краткое или полное наименование заявителя.',
            'warning'
        )
        return

    # ── 1. Ищем или создаём организацию ─────────────────────────────────────
    row = conn.execute(
        "SELECT id FROM phonebook_orgs WHERE name = ?", (org_name,)
    ).fetchone()

    if row:
        org_id = row['id']
    else:
        cur = conn.execute(
            "INSERT INTO phonebook_orgs (name, address) VALUES (?, ?)",
            (org_name, address)
        )
        org_id = cur.lastrowid
        log_action(conn, user_id, 'create', request_id,
                   f'Справочник орг.: добавлена «{org_name}» из обращения #{request_id}')

    # ── 2. Ищем или создаём контакт (только если указано ФИО) ───────────────
    if contact_name:
        exists = conn.execute(
            "SELECT id FROM phonebook WHERE org_id = ? AND full_name = ?",
            (org_id, contact_name)
        ).fetchone()

        if not exists:
            conn.execute(
                """INSERT INTO phonebook
                       (org_id, full_name, position, phone_work, email, inn)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (org_id, contact_name, contact_pos, phone_work, email, inn)
            )
            log_action(conn, user_id, 'create', request_id,
                       f'Справочник: добавлен контакт «{contact_name}» ({org_name}) '
                       f'из обращения #{request_id}')
