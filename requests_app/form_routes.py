import os
import shutil
import hashlib
import logging
import traceback
from datetime import datetime, date

from flask import render_template, request, redirect, url_for, session, flash
from werkzeug.utils import secure_filename

from db import get_db, UPLOADS_DIR, UPLOADS_TMP
from auth_utils import login_required
from form_utils import build_values, get_classifiers, ALL_FIELDS, REQUIRED_FIELDS, add_workdays
from validators import allowed_file, validate_inn
from activity_log import log_action
from ocr_utils import extract_anketa_fields
from request_history import save_history
from phonebook_routes import sync_request_to_phonebook
from tray import notify_error
from . import requests_bp

# ─── Логгер ошибок формы ────────────────────────────────────────────────────
_err_logger = logging.getLogger('kitezh.form_errors')
if not _err_logger.handlers:
    _h = logging.FileHandler('kitezh_errors.log', encoding='utf-8')
    _h.setFormatter(logging.Formatter('%(asctime)s  %(message)s'))
    _err_logger.addHandler(_h)
    _err_logger.setLevel(logging.ERROR)

_PRESERVE_FIELDS = [
    'review_days',
    'responsible_id',
    'responsible_not_in_system',
    'responsible_name_external',
    'reviewer_id',
    'reviewer_not_in_system',
    'reviewer_name_external',
    'sent_to_applicant_at',
    'send_method',
    'applicant_feedback',
    'applicant_feedback_at',
]


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


def _unique_filename(original: str) -> str:
    ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:19]
    return f"{ts}_{secure_filename(original)}"


def _save_files_transactional(file_list):
    pending = []
    for uf in file_list:
        if not (uf and uf.filename and allowed_file(uf.filename)):
            continue
        fn  = _unique_filename(uf.filename)
        tmp = os.path.join(UPLOADS_TMP, fn)
        uf.save(tmp)
        digest = _sha256(tmp)
        pending.append((fn, tmp, digest))
    return pending


def _commit_files(conn, pending, request_id):
    if not pending:
        return
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for fn, tmp, digest in pending:
        conn.execute(
            "INSERT INTO request_file_hashes (request_id, filename, sha256, created_at) "
            "VALUES (?, ?, ?, ?)",
            (request_id, fn, digest, now)
        )
    for fn, tmp, digest in pending:
        dst = os.path.join(UPLOADS_DIR, fn)
        shutil.move(tmp, dst)


def _cleanup_tmp(pending):
    for fn, tmp, _ in pending:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass


def _compute_review_deadline(form_date_str: str) -> str | None:
    """
    Вычисляет review_deadline = request_date + 7 рабочих дней.
    Возвращает ISO-строку 'YYYY-MM-DD' или None если дата не задана.
    """
    if not form_date_str:
        return None
    try:
        d = date.fromisoformat(form_date_str.strip())
        return add_workdays(d, 7).isoformat()
    except (ValueError, TypeError):
        return None


@requests_bp.route('/request/new', methods=['GET', 'POST'])
@login_required
def new_request():
    conn = get_db()

    if request.method == 'POST':
        now    = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        action = request.form.get('action', 'save')

        if action == 'ocr':
            uploaded_files = request.files.getlist('request_files')
            ocr_file = uploaded_files[0] if uploaded_files else None

            if not ocr_file or not ocr_file.filename:
                flash('Не выбран файл анкеты для OCR.', 'warning')
                conn.close()
                conn2 = get_db()
                lf2, di2, src2, emp2, subjects2, results2, all_users2 = get_classifiers(conn2)
                conn2.close()
                return render_template(
                    'form.html', req=None, today=date.today().isoformat(),
                    legal_forms=lf2, districts=di2, source_types=src2,
                    employees=emp2, required_fields=REQUIRED_FIELDS,
                    subjects=subjects2, results=results2, all_users=all_users2
                )

            pending_ocr = _save_files_transactional(uploaded_files)
            saved_names = [p[0] for p in pending_ocr]
            ocr_src = pending_ocr[0][1] if pending_ocr else None

            try:
                fields, msg = extract_anketa_fields(ocr_src) if ocr_src else ({}, '')
            finally:
                pass

            conn.close()
            conn2 = get_db()
            lf2, di2, src2, emp2, subjects2, results2, all_users2 = get_classifiers(conn2)
            conn2.close()

            if fields:
                fake_req = {f: '' for f in ALL_FIELDS}
                for k, v in fields.items():
                    if k in fake_req:
                        fake_req[k] = v
                fake_req['request_files'] = ','.join(saved_names) if saved_names else ''
                flash(
                    'Анкета распознана: часть полей заполнена автоматически. '
                    'Проверьте перед сохранением.', 'success'
                )
                return render_template(
                    'form.html', req=fake_req, today=date.today().isoformat(),
                    legal_forms=lf2, districts=di2, source_types=src2,
                    employees=emp2, required_fields=REQUIRED_FIELDS,
                    subjects=subjects2, results=results2, all_users=all_users2,
                    ocr_message=msg
                )
            else:
                _cleanup_tmp(pending_ocr)
                flash(
                    'Я ещё не слишком умный и не смог сопоставить данные анкеты. '
                    'Заполните поля вручную.', 'warning'
                )
                return render_template(
                    'form.html', req=None, today=date.today().isoformat(),
                    legal_forms=lf2, districts=di2, source_types=src2,
                    employees=emp2, required_fields=REQUIRED_FIELDS,
                    subjects=subjects2, results=results2, all_users=all_users2,
                    ocr_message=msg if 'msg' in locals() else ''
                )

        inn = request.form.get('applicant_inn', '').strip()
        ok_inn, inn_reason = validate_inn(inn)
        if inn_reason == 'format':
            flash('ИНН должен содержать только цифры.', 'warning')
        elif inn_reason == 'length':
            flash('Длина ИНН должна быть 10 цифр (юрлица) или 12 цифр (ИП).', 'warning')
        elif inn_reason == 'checksum':
            flash('ИНН указан с ошибкой. Контрольная сумма не совпадает.', 'warning')

        vals = build_values(request.form)

        uploaded_files = request.files.getlist('request_files')
        pending = _save_files_transactional(uploaded_files)
        saved_names = [p[0] for p in pending]
        vals[ALL_FIELDS.index('request_files')] = ','.join(saved_names) if saved_names else ''

        cols   = ', '.join(ALL_FIELDS) + ', created_by, created_at, updated_at'
        ph     = ','.join(['?'] * len(ALL_FIELDS)) + ',?,?,?'
        try:
            cursor = conn.execute(
                f"INSERT INTO requests ({cols}) VALUES ({ph})",
                vals + [session['user_id'], now, now]
            )
            new_id = cursor.lastrowid

            # ── Автоматически проставляем review_deadline = request_date + 7 раб. дней
            deadline = _compute_review_deadline(request.form.get('request_date', ''))
            if deadline:
                conn.execute(
                    "UPDATE requests SET review_deadline=? WHERE id=?",
                    (deadline, new_id)
                )

            applicant = (
                request.form.get('applicant_short_name', '') or
                request.form.get('applicant_full_name', '') or
                f'ID:{new_id}'
            )
            log_action(conn, session['user_id'], 'create', new_id,
                       f'Создано обращение: {applicant}'
                       + (f', deadline={deadline}' if deadline else ''))
            _commit_files(conn, pending, new_id)
            conn.commit()
            for fn, tmp, _ in pending:
                dst = os.path.join(UPLOADS_DIR, fn)
                if os.path.exists(tmp) and not os.path.exists(dst):
                    shutil.move(tmp, dst)
            if request.form.get('sync_to_phonebook') == '1':
                sync_request_to_phonebook(conn, request.form, new_id, session['user_id'])
                conn.commit()
        except Exception:
            _tb = traceback.format_exc()
            _err_logger.error('new_request:\n%s', _tb)
            notify_error('KITEZH: ошибка создания обращения', _tb.splitlines()[-1])
            _cleanup_tmp(pending)
            conn.close()
            flash('Ошибка сохранения обращения. Попробуйте ещё раз.', 'error')
            return redirect(url_for('requests.new_request'))

        conn.close()
        flash('Обращение сохранено', 'success')
        return redirect(url_for('requests.index'))

    lf, di, src, emp, subjects, results, all_users = get_classifiers(conn)
    conn.close()
    return render_template(
        'form.html', req=None, today=date.today().isoformat(),
        legal_forms=lf, districts=di, source_types=src,
        employees=emp, required_fields=REQUIRED_FIELDS,
        subjects=subjects, results=results, all_users=all_users
    )


@requests_bp.route('/request/<int:rid>', methods=['GET', 'POST'])
@login_required
def edit_request(rid):
    conn = get_db()
    req  = conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not req:
        conn.close()
        flash('Не найдено', 'error')
        return redirect(url_for('requests.index'))

    old_req = dict(req)

    if request.method == 'POST':
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        inn = request.form.get('applicant_inn', '').strip()
        ok_inn, inn_reason = validate_inn(inn)
        if inn_reason == 'format':
            flash('ИНН должен содержать только цифры.', 'warning')
        elif inn_reason == 'length':
            flash('Длина ИНН должна быть 10 цифр (юрлица) или 12 цифр (ИП).', 'warning')
        elif inn_reason == 'checksum':
            flash('ИНН указан с ошибкой. Контрольная сумма не совпадает.', 'warning')

        vals = build_values(request.form)

        for field in _PRESERVE_FIELDS:
            if field in ALL_FIELDS:
                idx = ALL_FIELDS.index(field)
                if not vals[idx]:
                    vals[idx] = req[field]

        af   = req['answer_file']
        file = request.files.get('answer_file')
        if file and file.filename and allowed_file(file.filename):
            fn2 = _unique_filename(file.filename)
            file.save(os.path.join(UPLOADS_DIR, fn2))
            af = fn2

        uploaded_files = request.files.getlist('request_files')
        pending = _save_files_transactional(uploaded_files)
        saved_names = [p[0] for p in pending]
        if saved_names:
            vals[ALL_FIELDS.index('request_files')] = ','.join(saved_names)
        else:
            vals[ALL_FIELDS.index('request_files')] = req['request_files'] or ''

        edit_reason = request.form.get('edit_reason', '').strip()
        updated_by  = session.get('user_id')

        # ── Пересчитываем deadline если пользователь изменил request_date
        new_date_str = request.form.get('request_date', '').strip()
        if new_date_str and new_date_str != (req['request_date'] or ''):
            new_deadline = _compute_review_deadline(new_date_str)
        else:
            new_deadline = req['review_deadline']  # сохраняем старый

        set_clause = ', '.join([f"{f}=?" for f in ALL_FIELDS])
        try:
            conn.execute(
                f"UPDATE requests SET {set_clause}, updated_at=?, updated_by=?, "
                f"edit_reason=?, answer_file=?, review_deadline=? WHERE id=?",
                vals + [now, updated_by, edit_reason, af, new_deadline, rid]
            )
            new_req = conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
            save_history(conn, rid, session['user_id'], old_req, new_req)
            num = req['request_number'] or f'ID:{rid}'
            reason_str = f' | Причина: {edit_reason}' if edit_reason else ''
            log_action(conn, session['user_id'], 'edit', rid,
                       f'Обращение {num}{reason_str}')
            if pending:
                _commit_files(conn, pending, rid)
            conn.commit()
            if pending:
                for fn, tmp, _ in pending:
                    dst = os.path.join(UPLOADS_DIR, fn)
                    if os.path.exists(tmp) and not os.path.exists(dst):
                        shutil.move(tmp, dst)
            if request.form.get('sync_to_phonebook') == '1':
                sync_request_to_phonebook(conn, request.form, rid, session['user_id'])
                conn.commit()
        except Exception:
            _tb = traceback.format_exc()
            _err_logger.error('edit_request rid=%s:\n%s', rid, _tb)
            notify_error('KITEZH: ошибка сохранения обращения', _tb.splitlines()[-1])
            _cleanup_tmp(pending)
            conn.close()
            flash('Ошибка обновления обращения. Попробуйте ещё раз.', 'error')
            return redirect(url_for('requests.edit_request', rid=rid))

        conn.close()
        flash('Обращение обновлено', 'success')
        return redirect(url_for('requests.index'))

    lf, di, src, emp, subjects, results, all_users = get_classifiers(conn)
    conn.close()
    return render_template(
        'form.html', req=req, today=date.today().isoformat(),
        legal_forms=lf, districts=di, source_types=src,
        employees=emp, required_fields=REQUIRED_FIELDS,
        subjects=subjects, results=results, all_users=all_users
    )
