# phonebook_routes.py
# Blueprint: телефонный справочник (v2.3.0)
# Маршруты:
#   GET  /phonebook                — список сотрудников с поиском
#   GET  /phonebook/search         — AJAX: поиск, возвращает JSON
#   POST /phonebook/add            — добавить сотрудника (админ)
#   POST /phonebook/edit           — редактировать сотрудника (админ)
#   POST /phonebook/delete         — удалить сотрудника (админ)
#   GET  /phonebook/orgs           — список организаций (админ)
#   POST /phonebook/orgs/add       — добавить организацию (админ)
#   POST /phonebook/orgs/edit      — редактировать организацию (админ)
#   POST /phonebook/orgs/delete    — удалить организацию (админ)
#   GET  /phonebook/org_address    — AJAX: получить адрес организации

from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, jsonify, session)
from db import get_db
from auth_utils import login_required, admin_required
from activity_log import log_action

phonebook_bp = Blueprint('phonebook', __name__)


def get_all_orgs():
    conn = get_db()
    orgs = conn.execute("SELECT * FROM phonebook_orgs ORDER BY name").fetchall()
    conn.close()
    return orgs


def get_all_contacts(search: str = ''):
    conn = get_db()
    if search:
        tokens = search.lower().split()
        base_sql = """
            SELECT p.*, o.name AS org_name, o.address AS org_address
            FROM phonebook p
            LEFT JOIN phonebook_orgs o ON p.org_id = o.id
            WHERE {conditions}
            ORDER BY o.name, p.full_name
        """
        token_clauses = []
        params = []
        for token in tokens:
            like = f'%{token}%'
            token_clauses.append("""(
                LOWER(p.full_name)     LIKE ?
                OR LOWER(p.position)   LIKE ?
                OR LOWER(o.name)       LIKE ?
                OR LOWER(p.phone_work) LIKE ?
                OR LOWER(p.phone_ext)  LIKE ?
                OR LOWER(p.email)      LIKE ?
                OR LOWER(p.room)       LIKE ?
                OR LOWER(p.notes)      LIKE ?
            )""")
            params.extend([like, like, like, like, like, like, like, like])
        sql = base_sql.format(conditions=' AND '.join(token_clauses))
        rows = conn.execute(sql, params).fetchall()
    else:
        rows = conn.execute("""
            SELECT p.*, o.name AS org_name, o.address AS org_address
            FROM phonebook p
            LEFT JOIN phonebook_orgs o ON p.org_id = o.id
            ORDER BY o.name, p.full_name
        """).fetchall()
    conn.close()
    return rows


@phonebook_bp.route('/phonebook')
@login_required
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
def phonebook_search():
    """AJAX-эндпоинт: возвращает JSON для live-поиска."""
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
             phone_work, phone_ext, phone_personal, email, notes)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        d.get('org_id') or None,
        d.get('position', '').strip(),
        d.get('room', '').strip(),
        name,
        d.get('phone_work', '').strip(),
        d.get('phone_ext', '').strip(),
        d.get('phone_personal', '').strip(),
        d.get('email', '').strip(),
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
    orgs = get_all_orgs()
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
    oid  = request.form.get('org_id')
    conn = get_db()
    count = conn.execute(
        "SELECT COUNT(*) FROM phonebook WHERE org_id=?", (oid,)
    ).fetchone()[0]
    if count > 0:
        flash(f'Нельзя удалить: к организации привязано {count} сотрудников', 'error')
    else:
        row  = conn.execute(
            "SELECT name FROM phonebook_orgs WHERE id=?", (oid,)
        ).fetchone()
        name = row['name'] if row else f'ID:{oid}'
        conn.execute("DELETE FROM phonebook_orgs WHERE id=?", (oid,))
        log_action(conn, session['user_id'], 'delete', None,
                   f'Справочник орг.: удалена «{name}»')
        conn.commit()
        flash('Организация удалена', 'success')
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
