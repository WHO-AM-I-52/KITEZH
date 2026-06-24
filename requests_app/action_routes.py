# ╔═══════════════════════════════════════════════
# ║              action_routes.py                             ║
# ║  Смена статусов, решения проверяющих,         ║
# ║  загрузка ответа, удаление обращений,        ║
# ║  соисполнители обращений.                       ║
# ╚═══════════════════════════════════════════════

import os
from datetime import datetime, date

from flask import request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename

from db import get_db, UPLOADS_DIR, STATUS_NORM_DAYS, _add_workdays
from core.auth_utils import login_required, permission_required
from core.activity_log import log_action
from utils.validators import allowed_file
from . import requests_bp


# ─── КОНСТАНТЫ ────────────────────────────────────────────────────────────────────────────────────

_VALID_STATUSES = (
    'draft', 'registered', 'in_progress',
    'under_review', 'ready_to_send', 'sent_to_applicant', 'closed'
)

_STATUS_EXTRA_FIELDS = {
    'sent_to_applicant': ['sent_to_applicant_at', 'send_method'],
    'closed':            ['applicant_feedback', 'applicant_feedback_at', 'result_type_id', 'taken_under_supervision'],
}

_INT_STATUS_FIELDS = {'result_type_id', 'taken_under_supervision'}

# Маппинг статуса → поле даты перехода
_STATUS_AT_FIELD = {
    'registered':        'at_registered',
    'in_progress':       'at_in_progress',
    'under_review':      'at_under_review',
    'ready_to_send':     'at_ready_to_send',
    'sent_to_applicant': 'at_sent_to_applicant',
    'closed':            'at_closed',
}

_MAX_REVIEWERS = 5


# ─── ХЕЛПЕР: уведомление ответственным лицам ──────────────

def _notify_responsible(conn, req, message):
    """
    Отправляет уведомление всем, кто связан с обращением:
    - исполнитель (assigned_to)
    - ответственный (responsible_id)
    - соисполнители (из request_coexecutors)
    Дедупликация через set().
    """
    rid = req['id']
    targets = set()
    if req['assigned_to']:
        targets.add(req['assigned_to'])
    if req['responsible_id']:
        targets.add(req['responsible_id'])

    # Добавляем соисполнителей
    coex_rows = conn.execute(
        "SELECT user_id FROM request_coexecutors WHERE request_id = ?", (rid,)
    ).fetchall()
    for row in coex_rows:
        targets.add(row['user_id'])

    for uid in targets:
        conn.execute(
            "INSERT INTO notifications (user_id, message, link) VALUES (?, ?, ?)",
            (uid, message, f'/view/{rid}')
        )


def _calc_deadline(status: str, transition_date_str: str) -> str | None:
    """
    Рассчитывает review_deadline для текущего этапа:
    deadline = дата перехода в status + норматив этапа (рабочих дней).
    Возвращает ISO-строку или None.
    """
    norm = STATUS_NORM_DAYS.get(status)
    if norm is None or not transition_date_str:
        return None
    try:
        start = date.fromisoformat(transition_date_str[:10])
        return _add_workdays(start, norm).isoformat()
    except (ValueError, TypeError):
        return None


# ─── СМЕНА СТАТУСА ────────────────────────────────────────────────────────────────────────────────────

@requests_bp.route('/request/<int:rid>/status', methods=['POST'])
@login_required
def change_status(rid):
    ns = request.form.get('status')
    if ns not in _VALID_STATUSES:
        flash('Неверный статус', 'error')
        return redirect(url_for('requests.view_request', rid=rid))

    conn = get_db()
    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    today = now[:10]
    req  = conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not req:
        conn.close()
        flash('Обращение не найдено', 'error')
        return redirect(url_for('requests.index'))

    upd_fields = ['status=?', 'updated_at=?']
    upd_vals   = [ns, now]

    # ── Записываем дату перехода в новый статус
    at_field = _STATUS_AT_FIELD.get(ns)
    if at_field:
        upd_fields.append(f'{at_field}=?')
        upd_vals.append(today)

    # ── Пересчитываем review_deadline под новый этап
    deadline = _calc_deadline(ns, today)
    if deadline and ns not in ('draft', 'closed'):
        upd_fields.append('review_deadline=?')
        upd_vals.append(deadline)
    elif ns in ('closed',):
        upd_fields.append('review_deadline=?')
        upd_vals.append(None)

    for field in _STATUS_EXTRA_FIELDS.get(ns, []):
        val = request.form.get(field)
        if val is not None:
            upd_fields.append(f'{field}=?')
            if field == 'result_type_id':
                try:
                    upd_vals.append(int(val) if val else None)
                except (ValueError, TypeError):
                    upd_vals.append(None)
            elif field == 'taken_under_supervision':
                upd_vals.append(1 if val == '1' else 0)
            elif field in _INT_STATUS_FIELDS:
                try:
                    upd_vals.append(int(val) if val else None)
                except (ValueError, TypeError):
                    upd_vals.append(None)
            else:
                upd_vals.append(val.strip() if val else '')

    if ns == 'closed' and 'taken_under_supervision' not in [
        f.split('=')[0] for f in upd_fields
    ]:
        upd_fields.append('taken_under_supervision=?')
        upd_vals.append(0)

    # Совместимость: registered_at = at_registered
    if ns == 'registered':
        upd_fields.append('registered_at=?')
        upd_vals.append(today)

    # ════════════════════════════════════════════════════════════════
    # under_review: файлы + цепочка согласования
    # ════════════════════════════════════════════════════════════════
    if ns == 'under_review':
        uploaded_files = request.files.getlist('review_files')
        saved_names = []
        existing = conn.execute(
            "SELECT answer_file FROM requests WHERE id=?", (rid,)
        ).fetchone()
        if existing and existing['answer_file']:
            saved_names = [
                fn.strip() for fn in existing['answer_file'].split(',') if fn.strip()
            ]
        new_count = 0
        for f in uploaded_files:
            if f and f.filename and allowed_file(f.filename):
                fn = secure_filename(f.filename)
                f.save(os.path.join(UPLOADS_DIR, fn))
                if fn not in saved_names:
                    saved_names.append(fn)
                new_count += 1
        if new_count > 3:
            conn.close()
            flash('Можно прикрепить не более 3 файлов', 'error')
            return redirect(url_for('requests.view_request', rid=rid))
        if saved_names:
            upd_fields.append('answer_file=?')
            upd_vals.append(','.join(saved_names))

        reviewers = []
        for i in range(1, _MAX_REVIEWERS + 1):
            is_ext  = request.form.get(f'reviewer_not_in_system_{i}') == '1'
            ext_name = request.form.get(f'reviewer_ext_name_{i}', '').strip()
            uid_raw  = request.form.get(f'reviewer_id_{i}', '').strip()
            if is_ext and ext_name:
                reviewers.append({'user_id': None, 'external_name': ext_name})
            elif not is_ext and uid_raw:
                try:
                    reviewers.append({'user_id': int(uid_raw), 'external_name': None})
                except (ValueError, TypeError):
                    pass

        if not reviewers:
            conn.close()
            flash('Укажите хотя бы одного проверяющего', 'error')
            return redirect(url_for('requests.view_request', rid=rid))

        conn.execute("DELETE FROM review_chain WHERE request_id=?", (rid,))

        for step, rv in enumerate(reviewers, start=1):
            conn.execute(
                "INSERT INTO review_chain (request_id, user_id, external_name, step_order) "
                "VALUES (?, ?, ?, ?)",
                (rid, rv['user_id'], rv['external_name'], step)
            )

        first = reviewers[0]
        upd_fields.append('reviewer_id=?')
        upd_vals.append(first['user_id'])
        upd_fields.append('reviewer_name_external=?')
        upd_vals.append(first['external_name'] or '')
        upd_fields.append('reviewer_not_in_system=?')
        upd_vals.append(1 if first['user_id'] is None else 0)

        req_num = req['request_number'] or f'ID:{rid}'
        if first['user_id']:
            conn.execute(
                "INSERT INTO notifications (user_id, message, link) VALUES (?, ?, ?)",
                (first['user_id'],
                 f'Вам направлено обращение {req_num} на проверку (шаг 1 из {len(reviewers)})',
                 f'/view/{rid}')
            )

        chain_names = ', '.join(
            rv['external_name'] if rv['external_name'] else str(rv['user_id'])
            for rv in reviewers
        )
        _notify_responsible(
            conn, req,
            f'Обращение {req_num} направлено на согласование. Цепочка: {chain_names}'
        )

    # ════════════════════════════════════════════════════════════════

    upd_vals.append(rid)
    conn.execute(
        f"UPDATE requests SET {', '.join(upd_fields)} WHERE id=?",
        upd_vals
    )
    log_action(conn, session['user_id'], 'status', rid,
               f'Новый статус: {ns}'
               + (f'. Цепочка: {len(reviewers)} чел.' if ns == 'under_review' else ''))
    conn.commit()
    conn.close()
    return redirect(url_for('requests.view_request', rid=rid))


# ─── РЕШЕНИЕ ПРОВЕРЯЮЩЕГО ────────────────────────────────────────────────────────

@requests_bp.route('/request/<int:rid>/reviewer_decision', methods=['POST'])
@login_required
def reviewer_decision(rid):
    decision = request.form.get('decision')
    comment  = request.form.get('reviewer_comment', '').strip()
    if decision not in ('approved', 'rejected'):
        flash('Неверное решение', 'error')
        return redirect(url_for('requests.view_request', rid=rid))

    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    today = now[:10]
    conn = get_db()
    req  = conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not req:
        conn.close()
        flash('Обращение не найдено', 'error')
        return redirect(url_for('requests.index'))

    req_num = req['request_number'] or f'ID:{rid}'

    current_step = conn.execute(
        "SELECT * FROM review_chain "
        "WHERE request_id=? AND decision IS NULL "
        "ORDER BY step_order LIMIT 1",
        (rid,)
    ).fetchone()

    if current_step:
        conn.execute(
            "UPDATE review_chain SET decision=?, comment=?, decided_at=? WHERE id=?",
            (decision, comment, now, current_step['id'])
        )

    if decision == 'approved':
        next_step = conn.execute(
            "SELECT * FROM review_chain "
            "WHERE request_id=? AND decision IS NULL "
            "ORDER BY step_order LIMIT 1",
            (rid,)
        ).fetchone()

        if next_step:
            total = conn.execute(
                "SELECT COUNT(*) FROM review_chain WHERE request_id=?", (rid,)
            ).fetchone()[0]
            if next_step['user_id']:
                conn.execute(
                    "INSERT INTO notifications (user_id, message, link) VALUES (?, ?, ?)",
                    (next_step['user_id'],
                     f'Вам направлено обращение {req_num} на проверку '
                     f'(шаг {next_step["step_order"]} из {total})',
                     f'/view/{rid}')
                )
            conn.execute(
                "UPDATE requests SET reviewer_id=?, reviewer_name_external=?, "
                "reviewer_not_in_system=?, reviewer_decision=NULL, "
                "reviewer_comment=NULL, updated_at=? WHERE id=?",
                (next_step['user_id'],
                 next_step['external_name'] or '',
                 1 if next_step['user_id'] is None else 0,
                 now, rid)
            )
            log_action(conn, session['user_id'], 'review', rid,
                       f'Одобрено (шаг {current_step["step_order"]}), следующий: шаг {next_step["step_order"]}')
        else:
            # Цепочка завершена → ready_to_send
            deadline = _calc_deadline('ready_to_send', today)
            extra_upd = f", at_ready_to_send='{today}'"
            if deadline:
                extra_upd += f", review_deadline='{deadline}'"
            conn.execute(
                f"UPDATE requests SET status='ready_to_send', reviewer_decision='approved', "
                f"reviewer_comment=?, reviewer_decision_at=?, updated_at=?{extra_upd} WHERE id=?",
                (comment, now, now, rid)
            )
            _notify_responsible(
                conn, req,
                f'Обращение {req_num} прошло все этапы согласования, статус → Готово к отправке'
            )
            log_action(conn, session['user_id'], 'review', rid,
                       'Все этапы одобрены, статус → ready_to_send')
    else:
        # rejected → in_progress
        deadline = _calc_deadline('in_progress', today)
        extra_upd = f", at_in_progress='{today}'"
        if deadline:
            extra_upd += f", review_deadline='{deadline}'"
        conn.execute(
            f"UPDATE requests SET status='in_progress', reviewer_decision='rejected', "
            f"reviewer_comment=?, reviewer_decision_at=?, updated_at=?{extra_upd} WHERE id=?",
            (comment, now, now, rid)
        )
        step_num = current_step['step_order'] if current_step else '?'
        _notify_responsible(
            conn, req,
            f'Обращение {req_num} отклонено на шаге {step_num}. '
            f'Комментарий: {comment or "—"}'
        )
        log_action(conn, session['user_id'], 'review', rid,
                   f'Отклонено на шаге {step_num}, статус → in_progress')

    conn.commit()
    conn.close()
    flash('Решение зафиксировано', 'success')
    return redirect(url_for('requests.view_request', rid=rid))


# ─── ЗАГРУЗКА ОТВЕТА ────────────────────────────────────────────────────────────────────────────────────

@requests_bp.route('/request/<int:rid>/answer', methods=['POST'])
@login_required
def answer_request(rid):
    conn   = get_db()
    now    = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    today  = now[:10]
    method = request.form.get('answer_method', '')
    m_other = request.form.get('answer_method_other', '')
    notes  = request.form.get('answer_notes', '')
    answer_sys_num = request.form.get('answer_system_number', '').strip()

    af_row = conn.execute(
        "SELECT answer_file FROM requests WHERE id=?", (rid,)
    ).fetchone()
    af = af_row['answer_file'] if af_row else None

    f = request.files.get('answer_file')
    if f and f.filename and allowed_file(f.filename):
        fn = secure_filename(f.filename)
        f.save(os.path.join(UPLOADS_DIR, fn))
        af = fn

    deadline = _calc_deadline('ready_to_send', today)
    conn.execute(
        "UPDATE requests SET status='ready_to_send', answer_date=?, "
        "answer_method=?, answer_method_other=?, answer_notes=?, "
        "answer_file=?, answer_system_number=?, updated_at=?, "
        "at_ready_to_send=?, review_deadline=? WHERE id=?",
        (today, method, m_other, notes, af, answer_sys_num, now,
         today, deadline, rid)
    )
    log_action(conn, session['user_id'], 'answer', rid,
               f'Подбор загружен, статус → ready_to_send. Способ: {method}'
               + (f' ({answer_sys_num})' if answer_sys_num else ''))
    conn.commit()
    conn.close()
    flash('Ответ зафиксирован, статус изменён на «Готово к отправке»', 'success')
    return redirect(url_for('requests.view_request', rid=rid))


# ─── УДАЛЕНИЕ ─────────────────────────────────────────────────────────────────────────────────────────

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
    flash(f'Удалено обращений: {len(deleted_labels)}', 'success')
    return redirect(url_for('requests.index'))


# ─── ЗАКРЕПЛЁННАЯ ЗАМЕТКА ───────────────────────────────────────────────────────────────────────

@requests_bp.route('/requests/<int:rid>/note', methods=['POST'])
@login_required
def save_pinned_note(rid):
    text = request.form.get('text', '').strip()
    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    user_id = session['user_id']
    conn = get_db()
    conn.execute(
        """
        INSERT INTO pinned_notes (object_type, object_id, text, created_by, updated_at)
        VALUES ('request', ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        (rid, text, user_id, now)
    )
    # upsert: обновляем если запись уже есть
    conn.execute(
        """
        UPDATE pinned_notes
        SET text=?, updated_at=?
        WHERE object_type='request' AND object_id=? AND created_by=?
        """,
        (text, now, rid, user_id)
    )
    log_action(conn, user_id, 'note_save', rid, 'Заметка сохранена')
    conn.commit()
    conn.close()
    return redirect(url_for('requests.view_request', rid=rid))


# ─── СОИСПОЛНИТЕЛИ ────────────────────────────────────────────────────────────────────────────────────

@requests_bp.route('/request/<int:rid>/coexecutors', methods=['POST'])
@login_required
def add_coexecutor(rid):
    """POST — назначить соисполнителя.
    form: coexecutor_id (int)
    """
    try:
        coex_uid = int(request.form.get('coexecutor_id', ''))
    except (ValueError, TypeError):
        flash('Не выбран пользователь', 'error')
        return redirect(url_for('requests.view_request', rid=rid))

    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()

    req = conn.execute(
        "SELECT id, request_number FROM requests WHERE id=?", (rid,)
    ).fetchone()
    if not req:
        conn.close()
        flash('Обращение не найдено', 'error')
        return redirect(url_for('requests.index'))

    user_row = conn.execute(
        "SELECT id, full_name FROM users WHERE id=? AND is_active=1", (coex_uid,)
    ).fetchone()
    if not user_row:
        conn.close()
        flash('Пользователь не найден', 'error')
        return redirect(url_for('requests.view_request', rid=rid))

    try:
        conn.execute(
            "INSERT INTO request_coexecutors "
            "(request_id, user_id, assigned_by, assigned_at) "
            "VALUES (?, ?, ?, ?)",
            (rid, coex_uid, session['user_id'], now)
        )
    except Exception:
        # UNIQUE constraint — соисполнитель уже есть
        conn.close()
        flash(f'Пользователь «{user_row["full_name"]}» уже является соисполнителем', 'warning')
        return redirect(url_for('requests.view_request', rid=rid))

    req_num = req['request_number'] or f'ID:{rid}'
    conn.execute(
        "INSERT INTO notifications (user_id, message, link) VALUES (?, ?, ?)",
        (coex_uid,
         f'Вы назначены соисполнителем по обращению {req_num}',
         f'/view/{rid}')
    )
    log_action(conn, session['user_id'], 'coexecutor_add', rid,
               f'Добавлен соисполнитель: {user_row["full_name"]}')
    conn.commit()
    conn.close()
    flash(f'Соисполнитель «{user_row["full_name"]}» добавлен', 'success')
    return redirect(url_for('requests.view_request', rid=rid))


@requests_bp.route('/request/<int:rid>/coexecutors/remove/<int:coex_uid>', methods=['POST'])
@login_required
def remove_coexecutor(rid, coex_uid):
    """POST — снять соисполнителя."""
    conn = get_db()

    user_row = conn.execute(
        "SELECT full_name FROM users WHERE id=?", (coex_uid,)
    ).fetchone()
    name = user_row['full_name'] if user_row else f'ID:{coex_uid}'

    conn.execute(
        "DELETE FROM request_coexecutors WHERE request_id=? AND user_id=?",
        (rid, coex_uid)
    )
    log_action(conn, session['user_id'], 'coexecutor_remove', rid,
               f'Снят соисполнитель: {name}')
    conn.commit()
    conn.close()
    flash(f'Соисполнитель «{name}» снят', 'success')
    return redirect(url_for('requests.view_request', rid=rid))
