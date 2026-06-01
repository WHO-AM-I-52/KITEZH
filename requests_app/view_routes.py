from flask import render_template, redirect, url_for, session, flash

from db import get_db
from auth_utils import login_required, admin_required
from request_history import get_history, rollback_history
from activity_log import log_action
from . import requests_bp


@requests_bp.route('/view/<int:rid>')
@login_required
def view_request(rid):
    conn = get_db()
    req  = conn.execute(
        "SELECT r.*, u.full_name AS employee_name, ass.full_name AS assigned_name, "
        "adm.full_name AS admin_name, upd.full_name AS updated_by_name, "
        "st.name AS subject_type_name, rt.name AS result_type_name, rt.color_hex AS result_color "
        "FROM requests r "
        "LEFT JOIN users u   ON r.created_by   = u.id "
        "LEFT JOIN users ass ON r.assigned_to  = ass.id "
        "LEFT JOIN users adm ON r.confirmed_by = adm.id "
        "LEFT JOIN users upd ON r.updated_by   = upd.id "
        "LEFT JOIN subject_types st ON r.subject_type_id = st.id "
        "LEFT JOIN result_types  rt ON r.result_type_id  = rt.id "
        "WHERE r.id=?", (rid,)
    ).fetchone()
    if not req:
        conn.close()
        flash('Не найдено', 'error')
        return redirect(url_for('requests.index'))

    okved_name = None
    if req['applicant_okved_main']:
        row = conn.execute(
            "SELECT name FROM okved WHERE code=? AND is_active=1",
            (req['applicant_okved_main'],)
        ).fetchone()
        if row:
            okved_name = row['name']

    employees = conn.execute(
        "SELECT id,full_name FROM users WHERE role IN ('employee','admin','manager') "
        "ORDER BY full_name"
    ).fetchall()
    conn.close()
    return render_template('view.html', req=req, employees=employees, okved_name=okved_name)


@requests_bp.route('/view/<int:rid>/history')
@login_required
@admin_required
def request_history_view(rid):
    conn = get_db()
    req  = conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    conn.close()
    if not req:
        flash('Не найдено', 'error')
        return redirect(url_for('requests.index'))
    history = get_history(rid)
    return render_template('history.html', history=history, req=req, rid=rid)


@requests_bp.route('/view/<int:rid>/rollback/<int:hid>', methods=['POST'])
@login_required
@admin_required
def rollback_request(rid, hid):
    conn = get_db()
    ok   = rollback_history(hid, rid)
    if ok:
        log_action(conn, session['user_id'], 'rollback', rid,
                   f'Откат к версии history_id={hid}')
        conn.commit()
        flash('Обращение откачено к выбранной версии', 'success')
    else:
        flash('Не удалось выполнить откат — запись не найдена', 'error')
    conn.close()
    return redirect(url_for('requests.view_request', rid=rid))
