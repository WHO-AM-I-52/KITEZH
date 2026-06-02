from datetime import datetime, date

from flask import request, redirect, url_for, session, flash, abort

from db import get_db
from auth_utils import login_required, permission_required
from activity_log import log_action
from . import requests_bp

# Допустимые переходы статусов для change_status (issue #53)
_VALID_STATUSES = (
    'draft', 'registered', 'in_progress',
    'under_review', 'ready_to_send', 'sent_to_applicant', 'closed'
)

# Дополнительные поля, которые могут приходить вместе со сменой статуса
_STATUS_EXTRA_FIELDS = {
    'sent_to_applicant': ['sent_to_applicant_at', 'send_method'],
    'closed':            ['applicant_feedback', 'applicant_feedback_at', 'result_type_id', 'taken_under_supervision'],
    'under_review':      ['reviewer_id', 'reviewer_not_in_system', 'reviewer_name_external'],
}


@requests_bp.route('/request/<int:rid>/confirm', methods=['POST'])
@login_required
def confirm_request(rid):
    from auth_utils import get_user_perm
    if session.get('role') != 'admin' and not get_user_perm('can_confirm'):
        flash('Недостаточно прав', 'error')
        return redirect(url_for('requests.index'))

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
            "UPDATE requests SET status='in_progress', request_number=?, "
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
    return redirect(url_for('requests.confirm_request', rid=rid))


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
        "UPDATE requests SET status='ready_to_send', answer_date=?, "
        "answer_method=?, answer_method_other=?, answer_notes=?, "
        "answer_file=?, answer_system_number=?, updated_at=? WHERE id=?",
        (date.today().isoformat(), method, m_other, notes, af, answer_sys_num, now, rid)
    )
    log_action(conn, session['user_id'], 'answer', rid,
               f'Подбор загружен, статус → ready_to_send. Способ: {method}'
               + (f' ({answer_sys_num})' if answer_sys_num else ''))
    conn.commit()
    conn.close()
    flash('Ответ зафиксирован, статус изменён на «Готово к отправке»', 'success')
    return redirect(url_for('requests.view_request', rid=rid))


@requests_bp.route('/request/<int:rid>/status', methods=['POST'])
@login_required
def change_status(rid):
    ns = request.form.get('status')
    if ns not in _VALID_STATUSES:
        flash('Неверный статус', 'error')
        return redirect(url_for('requests.view_request', rid=rid))

    conn = get_db()
    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Базовый UPDATE
    upd_fields = ['status=?', 'updated_at=?']
    upd_vals   = [ns, now]

    # Дополнительные поля в зависимости от нового статуса
    for field in _STATUS_EXTRA_FIELDS.get(ns, []):
        val = request.form.get(field)
        if val is not None:          # None = не пришло в форме, не трогаем
            upd_fields.append(f'{field}=?')
            # result_type_id — целое число или NULL
            if field == 'result_type_id':
                try:
                    upd_vals.append(int(val) if val else None)
                except (ValueError, TypeError):
                    upd_vals.append(None)
            elif field == 'taken_under_supervision':
                # checkbox: приходит '1' если отмечен, иначе отсутствует в форме
                upd_vals.append(1 if val == '1' else 0)
            else:
                upd_vals.append(val or None)

    # При переходе в closed без чекбокса — явно сбрасываем в 0
    if ns == 'closed' and 'taken_under_supervision' not in [
        f.split('=')[0] for f in upd_fields
    ]:
        upd_fields.append('taken_under_supervision=?')
        upd_vals.append(0)

    # При переходе в registered — фиксируем дату регистрации
    if ns == 'registered':
        upd_fields.append('registered_at=?')
        upd_vals.append(now[:10])

    upd_vals.append(rid)
    conn.execute(
        f"UPDATE requests SET {', '.join(upd_fields)} WHERE id=?",
        upd_vals
    )
    log_action(conn, session['user_id'], 'status', rid, f'Новый статус: {ns}')
    conn.commit()
    conn.close()
    return redirect(url_for('requests.view_request', rid=rid))


@requests_bp.route('/request/<int:rid>/reviewer_decision', methods=['POST'])
@login_required
def reviewer_decision(rid):
    """Решение проверяющего: approved → ready_to_send, rejected → in_progress."""
    decision = request.form.get('decision')
    comment  = request.form.get('reviewer_comment', '').strip()
    if decision not in ('approved', 'rejected'):
        flash('Неверное решение', 'error')
        return redirect(url_for('requests.view_request', rid=rid))

    new_status = 'ready_to_send' if decision == 'approved' else 'in_progress'
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    conn = get_db()
    conn.execute(
        "UPDATE requests SET status=?, reviewer_decision=?, "
        "reviewer_comment=?, reviewer_decision_at=?, updated_at=? WHERE id=?",
        (new_status, decision, comment or None, now, now, rid)
    )
    log_action(conn, session['user_id'], 'review', rid,
               f'Решение проверяющего: {decision}, статус → {new_status}')
    conn.commit()
    conn.close()
    flash('Решение зафиксировано', 'success')
    return redirect(url_for('requests.view_request', rid=rid))


@requests_bp.route('/request/<int:rid>/delete', methods=['POST'])
@login_required
@permission_required('can_delete')
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


@requests_bp.route('/requests/bulk_delete', methods=['POST'])
@login_required
@permission_required('can_delete')
def bulk_delete_requests():
    raw_ids = request.form.getlist('ids[]')

    # Валидация: только целые числа
    ids = []
    for v in raw_ids:
        try:
            ids.append(int(v))
        except (ValueError, TypeError):
            pass

    if not ids:
        flash('Не выбрано ни одного обращения', 'warning')
        return redirect(url_for('requests.index'))

    conn = get_db()
    placeholders = ','.join('?' * len(ids))
    rows = conn.execute(
        f"SELECT id, request_number, applicant_short_name FROM requests WHERE id IN ({placeholders})",
        ids
    ).fetchall()

    deleted_labels = []
    for row in rows:
        num  = row['request_number'] or f'ID:{row["id"]}'
        name = row['applicant_short_name'] or '—'
        log_action(conn, session['user_id'], 'delete', row['id'],
                   f'Массовое удаление: {num} ({name})')
        deleted_labels.append(num)

    conn.execute(
        f"DELETE FROM requests WHERE id IN ({placeholders})",
        ids
    )
    conn.commit()
    conn.close()

    count = len(deleted_labels)
    flash(f'Удалено обращений: {count}', 'success')
    return redirect(url_for('requests.index'))


@requests_bp.route('/request/<int:rid>/assign_number', methods=['POST'])
@login_required
def assign_number(rid):
    if session.get('role') not in ('admin', 'employee', 'manager'):
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
    now   = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    conn.execute(
        "UPDATE requests SET request_number=?, status='registered', "
        "registered_at=?, updated_at=? WHERE id=?",
        (num, now[:10], now, rid)
    )
    log_action(conn, session['user_id'], 'status', rid,
               f'Присвоен номер: {num}, статус → registered')
    conn.commit()
    conn.close()
    flash(f'Присвоен номер: {num}', 'success')
    return redirect(url_for('requests.view_request', rid=rid))
