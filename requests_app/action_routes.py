# ╔═══════════════════════════════════════════════
# ║              action_routes.py                             ║
# ║  Смена статусов, решения проверяющих,         ║
# ║  загрузка ответа, удаление обращений.        ║
# ╚═══════════════════════════════════════════════

import os
from datetime import datetime, date

from flask import request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename

from db import get_db, UPLOADS_DIR
from auth_utils import login_required, permission_required
from activity_log import log_action
from validators import allowed_file
from . import requests_bp


# ─── КОНСТАНТЫ ──────────────────────────────────────────────────────────────────────────────

_VALID_STATUSES = (
    'draft', 'registered', 'in_progress',
    'under_review', 'ready_to_send', 'sent_to_applicant', 'closed'
)

_STATUS_EXTRA_FIELDS = {
    'sent_to_applicant': ['sent_to_applicant_at', 'send_method'],
    'closed':            ['applicant_feedback', 'applicant_feedback_at', 'result_type_id', 'taken_under_supervision'],
}

_INT_STATUS_FIELDS = {'result_type_id', 'taken_under_supervision'}

# Максимальное количество проверяющих в цепочке
_MAX_REVIEWERS = 5


# ─── ХЕЛПЕР: уведомление ответственному лицу ──────────────────────────────────

def _notify_responsible(conn, req, message):
    """Отправляет уведомление ответственному лицу (поль зо assigned_to или responsible_id)."""
    rid = req['id']
    targets = set()
    if req['assigned_to']:
        targets.add(req['assigned_to'])
    if req['responsible_id']:
        targets.add(req['responsible_id'])
    for uid in targets:
        conn.execute(
            "INSERT INTO notifications (user_id, message, link) VALUES (?, ?, ?)",
            (uid, message, f'/view/{rid}')
        )


# ─── СМЕНА СТАТУСА ────────────────────────────────────────────────────────────────────────

@requests_bp.route('/request/<int:rid>/status', methods=['POST'])
@login_required
def change_status(rid):
    ns = request.form.get('status')
    if ns not in _VALID_STATUSES:
        flash('Неверный статус', 'error')
        return redirect(url_for('requests.view_request', rid=rid))

    conn = get_db()
    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    req  = conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not req:
        conn.close()
        flash('Обращение не найдено', 'error')
        return redirect(url_for('requests.index'))

    upd_fields = ['status=?', 'updated_at=?']
    upd_vals   = [ns, now]

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

    if ns == 'registered':
        upd_fields.append('registered_at=?')
        upd_vals.append(now[:10])

    # ════════════════════════════════════════════════════════════════
    # under_review: файлы + цепочка согласования
    # ════════════════════════════════════════════════════════════════
    if ns == 'under_review':
        # ─ Файлы для проверяющего
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

        # ─ Цепочка согласования: парсим reviewer_id_1..5 и ext_name_1..5
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

        # Сбрасываем старую цепочку для этого обращения
        conn.execute("DELETE FROM review_chain WHERE request_id=?", (rid,))

        # Записываем новую цепочку
        for step, rv in enumerate(reviewers, start=1):
            conn.execute(
                "INSERT INTO review_chain (request_id, user_id, external_name, step_order) "
                "VALUES (?, ?, ?, ?)",
                (rid, rv['user_id'], rv['external_name'], step)
            )

        # Обновляем поле reviewer_id в requests (первый в цепочке)
        first = reviewers[0]
        upd_fields.append('reviewer_id=?')
        upd_vals.append(first['user_id'])
        upd_fields.append('reviewer_name_external=?')
        upd_vals.append(first['external_name'] or '')
        upd_fields.append('reviewer_not_in_system=?')
        upd_vals.append(1 if first['user_id'] is None else 0)

        # ─ Уведомление первому проверяющему
        req_num = req['request_number'] or f'ID:{rid}'
        if first['user_id']:
            conn.execute(
                "INSERT INTO notifications (user_id, message, link) VALUES (?, ?, ?)",
                (first['user_id'],
                 f'Вам направлено обращение {req_num} на проверку (шаг 1 из {len(reviewers)})',
                 f'/view/{rid}')
            )

        # ─ Уведомление ответственному лицу (fix: раньше не отправлялось)
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


# ─── РЕШЕНИЕ ПРОВЕРЯЮЩЕГО ─────────────────────────────────────────────────────────

@requests_bp.route('/request/<int:rid>/reviewer_decision', methods=['POST'])
@login_required
def reviewer_decision(rid):
    """
    Решение проверяющего.
    approved: если есть следующий в цепочке → уведомляем его;
             иначе → ready_to_send + уведомление ответственному.
    rejected: цепочка прерывается → in_progress + уведомление ответственному.
    """
    decision = request.form.get('decision')
    comment  = request.form.get('reviewer_comment', '').strip()
    if decision not in ('approved', 'rejected'):
        flash('Неверное решение', 'error')
        return redirect(url_for('requests.view_request', rid=rid))

    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    req  = conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not req:
        conn.close()
        flash('Обращение не найдено', 'error')
        return redirect(url_for('requests.index'))

    req_num = req['request_number'] or f'ID:{rid}'

    # Фиксируем решение текущего проверяющего в review_chain
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
        # Ищем следующий в цепочке
        next_step = conn.execute(
            "SELECT * FROM review_chain "
            "WHERE request_id=? AND decision IS NULL "
            "ORDER BY step_order LIMIT 1",
            (rid,)
        ).fetchone()

        if next_step:
            # Есть следующий — уведомляем его
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
            # Статус остаётся under_review
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
            # Цепочка завершена — переводим в ready_to_send
            conn.execute(
                "UPDATE requests SET status='ready_to_send', reviewer_decision='approved', "
                "reviewer_comment=?, reviewer_decision_at=?, updated_at=? WHERE id=?",
                (comment, now, now, rid)
            )
            _notify_responsible(
                conn, req,
                f'Обращение {req_num} прошло все этапы согласования, статус → Готово к отправке'
            )
            log_action(conn, session['user_id'], 'review', rid,
                       'Все этапы одобрены, статус → ready_to_send')
    else:
        # rejected: прерываем цепочку
        conn.execute(
            "UPDATE requests SET status='in_progress', reviewer_decision='rejected', "
            "reviewer_comment=?, reviewer_decision_at=?, updated_at=? WHERE id=?",
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


# ─── ЗАГРУЗКА ОТВЕТА ─────────────────────────────────────────────────────────────────────

@requests_bp.route('/request/<int:rid>/answer', methods=['POST'])
@login_required
def answer_request(rid):
    conn   = get_db()
    now    = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
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


# ─── УДАЛЕНИЕ ───────────────────────────────────────────────────────────────────────────────

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
