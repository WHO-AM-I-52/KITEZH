# ╔═══════════════════════════════════════════════
# ║              admin_routes.py                              ║
# ║  Административные действия: подтверждение/отклонение   ║
# ║  обращений, присвоение номера.             ║
# ╚═══════════════════════════════════════════════

from datetime import datetime

from flask import request, redirect, url_for, session, flash, abort

from db import get_db
from auth_utils import login_required
from activity_log import log_action
from . import requests_bp


# ─── Хелпер: генерация регистрационного номера ───────────────────────────────────

def generate_request_number(conn, subject_type_id):
    """
    Генерирует уникальный рег. номер по формату: PREFIX-ГОД-NNN
    Префикс берётся из subject_types.reg_prefix по subject_type_id.
    Если предмет не задан или reg_prefix пустой — используется 'БП'.
    Счётчик сбрасывается каждый год пер-prefix.
    """
    year = datetime.now().year

    prefix = 'БП'
    if subject_type_id:
        row = conn.execute(
            "SELECT reg_prefix FROM subject_types WHERE id=?",
            (subject_type_id,)
        ).fetchone()
        if row and row['reg_prefix'] and row['reg_prefix'].strip():
            prefix = row['reg_prefix'].strip()

    conn.execute(
        """
        INSERT INTO reg_number_sequences (prefix, year, last_seq)
        VALUES (?, ?, 1)
        ON CONFLICT(prefix, year) DO UPDATE SET last_seq = last_seq + 1
        """,
        (prefix, year)
    )
    seq = conn.execute(
        "SELECT last_seq FROM reg_number_sequences WHERE prefix=? AND year=?",
        (prefix, year)
    ).fetchone()['last_seq']

    return f"{prefix}-{year}-{seq:03d}"


# ─── Подтверждение / отклонение обращения ─────────────────────────────────

@requests_bp.route('/request/<int:rid>/confirm', methods=['POST'])
@login_required
def confirm_request(rid):
    from auth_utils import get_user_perm
    if session.get('role') != 'admin' and not get_user_perm('can_confirm'):
        flash('Недостаточно прав', 'error')
        return redirect(url_for('requests.index'))

    conn = get_db()
    req  = conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not req:
        conn.close()
        flash('Не найдено', 'error')
        return redirect(url_for('requests.index'))

    action  = request.form.get('action')
    comment = request.form.get('admin_comment', '').strip()
    now     = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if action == 'accept':
        if req['request_number']:
            conn.close()
            flash('Номер уже присвоен этому обращению', 'warning')
            return redirect(url_for('requests.confirm_request', rid=rid))

        num      = generate_request_number(conn, req['subject_type_id'])
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


# ─── Присвоение номера вручную ──────────────────────────────────────────

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

    num = generate_request_number(conn, req['subject_type_id'])
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

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
