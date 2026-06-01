from datetime import datetime, date

from flask import request, redirect, url_for, session, flash, abort

from db import get_db
from auth_utils import login_required, admin_required
from activity_log import log_action
from . import requests_bp


@requests_bp.route('/request/<int:rid>/confirm', methods=['POST'])
@login_required
@admin_required
def confirm_request(rid):
    conn    = get_db()
    req     = conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not req:
        conn.close()
        flash('Не найдено', 'error')
        return redirect(url_for('requests.index'))

    action  = request.form.get('action')
    comment = request.form.get('admin_comment', '').strip()
    now     = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if action == 'accept':
        year     = datetime.now().year
        count    = conn.execute(
            "SELECT COUNT(*) FROM requests WHERE status!='draft'"
        ).fetchone()[0] + 1
        num      = f"ЗУ-{year}-{count:04d}"
        assigned = request.form.get('assigned_to') or req['assigned_to']
        if assigned:
            try:
                assigned = int(assigned)
            except (ValueError, TypeError):
                assigned = req['assigned_to']

        conn.execute(
            "UPDATE requests SET status='accepted', request_number=?, "
            "confirmed_by=?, confirmed_at=?, admin_comment=?, assigned_to=? WHERE id=?",
            (num, session['user_id'], now, comment, assigned, rid)
        )
        conn.execute(
            "INSERT INTO notifications (user_id,message,link) VALUES (?,?,?)",
            (req['created_by'],
             f'Обращение принято в работу. Номер: {num}', f'/view/{rid}')
        )
        if assigned:
            conn.execute(
                "INSERT INTO notifications (user_id,message,link) VALUES (?,?,?)",
                (assigned,
                 f'Вам назначено новое обращение. Номер: {num}', f'/view/{rid}')
            )
        log_action(conn, session['user_id'], 'accept', rid,
                   f'Принято в работу, номер: {num}, ответственный ID={assigned}')
        conn.commit()
        flash(f'Принято в работу, номер: {num}', 'success')

    elif action == 'reject':
        conn.execute(
            "UPDATE requests SET status='draft', admin_comment=? WHERE id=?",
            (comment, rid)
        )
        conn.execute(
            "INSERT INTO notifications (user_id,message,link) VALUES (?,?,?)",
            (req['created_by'],
             f'Обращение возвращено на доработку. Комментарий: {comment}',
             f'/view/{rid}')
        )
        log_action(conn, session['user_id'], 'reject', rid,
                   f'Возврат на доработку. Комментарий: {comment}')
        conn.commit()
        flash('Возвращено на доработку', 'warning')

    conn.close()
    return redirect(url_for('requests.view_request', rid=rid))


@requests_bp.route('/request/<int:rid>/answer', methods=['POST'])
@login_required
def answer_request(rid):
    import os
    from werkzeug.utils import secure_filename
    from db import UPLOADS_DIR
    from validators import allowed_file

    conn   = get_db()
    now    = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    method = request.form.get('answer_method', '')
    m_other= request.form.get('answer_method_other', '')
    notes  = request.form.get('answer_notes', '')
    answer_sys_num = request.form.get('answer_system_number', '').strip()

    af_row = conn.execute(
        "SELECT answer_file FROM requests WHERE id=?", (rid,)
    ).fetchone()
    af = af_row['answer_file'] if af_row else None

    file = request.files.get('answer_file')
    if file and file.filename and allowed_file(file.filename):
        fn2 = secure_filename(file.filename)
        file.save(os.path.join(UPLOADS_DIR, fn2))
        af = fn2

    conn.execute(
        "UPDATE requests SET status='answered', answer_date=?, "
        "answer_method=?, answer_method_other=?, answer_notes=?, "
        "answer_file=?, answer_system_number=?, updated_at=? WHERE id=?",
        (date.today().isoformat(), method, m_other, notes, af, answer_sys_num, now, rid)
    )
    log_action(conn, session['user_id'], 'answer', rid,
               f'Способ: {method}' + (f' ({answer_sys_num})' if answer_sys_num else ''))
    conn.commit()
    conn.close()
    flash('Ответ зафиксирован', 'success')
    return redirect(url_for('requests.view_request', rid=rid))


@requests_bp.route('/request/<int:rid>/status', methods=['POST'])
@login_required
def change_status(rid):
    ns = request.form.get('status')
    if ns not in ('draft', 'review', 'accepted', 'answered'):
        flash('Неверный статус', 'error')
        return redirect(url_for('requests.view_request', rid=rid))
    conn = get_db()
    conn.execute(
        "UPDATE requests SET status=?, updated_at=? WHERE id=?",
        (ns, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), rid)
    )
    log_action(conn, session['user_id'], 'status', rid, f'Новый статус: {ns}')
    conn.commit()
    conn.close()
    return redirect(url_for('requests.view_request', rid=rid))


@requests_bp.route('/request/<int:rid>/delete', methods=['POST'])
@login_required
@admin_required
def delete_request(rid):
    conn = get_db()
    req  = conn.execute(
        "SELECT request_number, applicant_short_name FROM requests WHERE id=?", (rid,)
    ).fetchone()
    num  = req['request_number'] or f'ID:{rid}' if req else f'ID:{rid}'
    name = req['applicant_short_name'] or '—' if req else '—'

    log_action(conn, session['user_id'], 'delete', rid,
               f'Удалено обращение {num} ({name})')
    conn.execute("DELETE FROM requests WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    flash('Обращение удалено', 'success')
    return redirect(url_for('requests.index'))


@requests_bp.route('/request/<int:rid>/assign_number', methods=['POST'])
@login_required
def assign_number(rid):
    if session.get('role') != 'admin':
        abort(403)
    conn = get_db()
    req  = conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not req or req['request_number']:
        conn.close()
        flash('Номер уже присвоен или обращение не найдено', 'warning')
        return redirect(url_for('requests.view_request', rid=rid))

    year  = datetime.now().year
    count = conn.execute(
        "SELECT COUNT(*) FROM requests WHERE request_number IS NOT NULL"
    ).fetchone()[0] + 1
    num   = f"ЗУ-{year}-{count:04d}"

    conn.execute("UPDATE requests SET request_number=? WHERE id=?", (num, rid))
    log_action(conn, session['user_id'], 'status', rid,
               f'Присвоен номер: {num}')
    conn.commit()
    conn.close()
    flash(f'Присвоен номер: {num}', 'success')
    return redirect(url_for('requests.view_request', rid=rid))
