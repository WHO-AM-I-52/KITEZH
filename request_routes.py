# ╔══════════════════════════════════════════════════════════════╗
# ║ request_routes.py                                            ║
# ║ v3.0: новая логика статусов (#53)                       ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, session, flash, send_file, jsonify, abort
)
from datetime import datetime, date, timedelta
from werkzeug.utils import secure_filename
import os
import json
import math

from dashboard import build_dash
from db import get_db, UPLOADS_DIR
from auth_utils import login_required, admin_required
from form_utils import build_values, get_classifiers, ALL_FIELDS, REQUIRED_FIELDS
from validators import allowed_file, validate_inn
from request_history import save_history, get_history, rollback_history
from activity_log import log_action
from ocr_utils import extract_anketa_fields

requests_bp = Blueprint('requests', __name__)

PAGE_SIZE = 50

# ─── СТАТУСЫ ──────────────────────────────────────────────────────────────
VALID_STATUSES = {
    'draft', 'registered', 'in_progress',
    'under_review', 'ready_to_send', 'sent_to_applicant', 'closed'
}

# Допустимые переходы для обычных пользователей (employee/manager)
ALLOWED_TRANSITIONS = {
    'registered':      ('in_progress',),
    'in_progress':     ('under_review',),
    'under_review':    ('ready_to_send', 'in_progress'),  # ready=одобрено, in_progress=отклонено
    'ready_to_send':   ('sent_to_applicant',),
    'sent_to_applicant': ('closed',),
}

STATUS_LABELS = {
    'draft':             'Черновик',
    'registered':        'Зарегистрировано',
    'in_progress':       'В работе',
    'under_review':      'На проверке',
    'ready_to_send':     'Готово для отправки',
    'sent_to_applicant': 'Документы отправлены',
    'closed':            'Обращение закрыто',
}


# ─── ФИЛЬТР СПИСКА ────────────────────────────────────────────────────────
def _build_filter(sf, df, dt, af, ef, src_f, search, quick, user_id, for_count=False):
    where = "WHERE 1=1 "
    params = [user_id]

    if sf:
        where += " AND r.status=?"
        params.append(sf)
    if df:
        where += " AND r.request_date>=?"
        params.append(df)
    if dt:
        where += " AND r.request_date<=?"
        params.append(dt)
    if af:
        where += " AND (r.applicant_full_name LIKE ? OR r.applicant_short_name LIKE ?)"
        params += [f"%{af}%"] * 2
    if ef:
        where += " AND r.assigned_to=?"
        params.append(ef)
    if src_f:
        where += " AND r.source_type LIKE ?"
        params.append(f"%{src_f}%")
    if search:
        where += """ AND (
            r.applicant_full_name LIKE ? OR r.applicant_short_name LIKE ? OR
            r.project_name LIKE ? OR r.contact_person LIKE ? OR
            r.contact_phone LIKE ? OR r.contact_email LIKE ? OR
            r.preferred_districts LIKE ? OR r.additional_info LIKE ?
        )"""
        params += [f"%{search}%"] * 8

    if quick == 'overdue':
        # Обращения с просроченным сроком (#53: используем review_deadline)
        where += (
            " AND r.status IN ('registered','in_progress','under_review','ready_to_send') "
            " AND r.review_deadline IS NOT NULL "
            " AND r.review_deadline < date('now')"
        )
    elif quick == 'mine':
        where += " AND r.assigned_to=?"
        params.append(user_id)
    elif quick == 'unassigned':
        where += " AND (r.assigned_to IS NULL OR r.assigned_to=0)"
    elif quick == 'favorites':
        if for_count:
            where += " AND f.id IS NOT NULL"
        else:
            where += " AND favorite_flag = 1"

    return where, params


# ─── ГЛАВНАЯ СТРАНИЦА ────────────────────────────────────────────────────
@requests_bp.route('/')
@login_required
def index():
    today  = date.today()
    period = request.args.get('period', 'month')
    sf     = request.args.get('status', '')
    df     = request.args.get('date_from', '')
    dt     = request.args.get('date_to', '')
    af     = request.args.get('applicant', '')
    ef     = request.args.get('employee', '')
    src_f  = request.args.get('source', '')
    search = request.args.get('search', '').strip()
    quick  = request.args.get('quick', '')
    try:
        page = max(1, int(request.args.get('page', 1)))
    except (ValueError, TypeError):
        page = 1

    conn = get_db()
    dash = build_dash(conn, period)
    uid  = session['user_id']

    where, params = _build_filter(sf, df, dt, af, ef, src_f, search, quick, uid, for_count=False)
    q = (
        "SELECT r.*, u.full_name AS employee_name, "
        "ass.full_name AS assigned_name, "
        "CASE WHEN f.id IS NULL THEN 0 ELSE 1 END AS favorite_flag "
        "FROM requests r "
        "LEFT JOIN users u   ON r.created_by  = u.id "
        "LEFT JOIN users ass ON r.assigned_to = ass.id "
        "LEFT JOIN favorites f ON f.request_id = r.id AND f.user_id = ? "
        f"{where} ORDER BY r.id DESC"
    )

    total_all = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]

    count_where, count_params = _build_filter(sf, df, dt, af, ef, src_f, search, quick, uid, for_count=True)
    count_q = (
        "SELECT COUNT(*) FROM requests r "
        "LEFT JOIN users u   ON r.created_by  = u.id "
        "LEFT JOIN users ass ON r.assigned_to = ass.id "
        "LEFT JOIN favorites f ON f.request_id = r.id AND f.user_id = ? "
        f"{count_where}"
    )

    total_filtered = conn.execute(count_q, count_params).fetchone()[0]
    total_pages    = max(1, math.ceil(total_filtered / PAGE_SIZE))
    page           = min(page, total_pages)
    offset         = (page - 1) * PAGE_SIZE

    reqs = conn.execute(q + f" LIMIT {PAGE_SIZE} OFFSET {offset}", params).fetchall()

    employees = conn.execute(
        "SELECT id,full_name FROM users WHERE role IN ('employee','admin','manager') "
        "ORDER BY full_name"
    ).fetchall()
    src_types = conn.execute(
        "SELECT value FROM classifiers WHERE category='source_type' ORDER BY sort_order,value"
    ).fetchall()
    sf_rows = conn.execute("SELECT * FROM saved_filters ORDER BY sort_order,id").fetchall()
    active_filter_id = request.args.get('active_filter', '')
    saved_filter_list = []
    for sfr in sf_rows:
        try:
            sp = json.loads(sfr['params'])
        except Exception:
            sp = {}
        saved_filter_list.append({
            'id': sfr['id'], 'name': sfr['name'],
            'description': sfr['description'], 'params': sp,
        })

    conn.close()

    filters = {
        'status': sf, 'date_from': df, 'date_to': dt,
        'applicant': af, 'employee': ef, 'source': src_f,
        'search': search, 'quick': quick,
    }
    return render_template(
        'index.html', requests=reqs, filters=filters, employees=employees,
        source_types=src_types, dash=dash, today_str=today.isoformat(),
        saved_filter_list=saved_filter_list, active_filter_id=active_filter_id,
        total_all=total_all, total_filtered=total_filtered, active_quick=quick,
        page=page, total_pages=total_pages,
        status_labels=STATUS_LABELS,
    )


@requests_bp.route('/dashboard')
@login_required
def dashboard():
    period = request.args.get('period', 'month')
    conn   = get_db()
    dash   = build_dash(conn, period)
    conn.close()
    return render_template('dashboard.html', dash=dash)


# ─── СОЗДАНИЕ ОБРАЩЕНИЯ ───────────────────────────────────────────────
@requests_bp.route('/request/new', methods=['GET', 'POST'])
@login_required
def new_request():
    conn = get_db()

    if request.method == 'POST':
        now    = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        action = request.form.get('action', 'save')

        if action == 'ocr':
            ocr_file = request.files.get('ocr_form')
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

            orig_name = ocr_file.filename or ''
            safe_orig = secure_filename(orig_name)
            _, ext = os.path.splitext(safe_orig)
            ext = (ext or '').lower()
            tmp_name = f'_ocr_tmp_anketa{ext}'
            tmp_path = os.path.join(UPLOADS_DIR, tmp_name)

            try:
                ocr_file.save(tmp_path)
                fields, msg = extract_anketa_fields(tmp_path)
            finally:
                try:
                    os.remove(tmp_path)
                except Exception:
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
                flash(
                    'я ещё не слишком умный и не смог сопоставить данные анкеты. '
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
        saved_names = []
        for uf in uploaded_files:
            if uf and uf.filename and allowed_file(uf.filename):
                fn2 = secure_filename(uf.filename)
                uf.save(os.path.join(UPLOADS_DIR, fn2))
                saved_names.append(fn2)
        vals[ALL_FIELDS.index('request_files')] = ','.join(saved_names) if saved_names else None

        # ─ Проверка обязательных полей — авторегистрация
        missing = [label for field, label in REQUIRED_FIELDS.items()
                   if not request.form.get(field, '').strip()]
        if not missing:
            # Все обязательные поля заполнены — статус draft,
            # регномер будет присвоен после нажатия "Присвоить номер"
            vals[ALL_FIELDS.index('status')] = 'draft'
            flash('Черновик сохранён. Нажмите «Присвоить номер» для регистрации.', 'info')
        else:
            vals[ALL_FIELDS.index('status')] = 'draft'
            flash(f'Не заполнены обязательные поля: {", ".join(missing)}', 'warning')

        cols    = ', '.join(ALL_FIELDS) + ', created_by, created_at, updated_at'
        ph      = ','.join(['?'] * len(ALL_FIELDS)) + ',?,?,?'
        cursor  = conn.execute(
            f"INSERT INTO requests ({cols}) VALUES ({ph})",
            vals + [session['user_id'], now, now]
        )
        new_id = cursor.lastrowid

        applicant = (
            request.form.get('applicant_short_name', '') or
            request.form.get('applicant_full_name', '') or
            f'ID:{new_id}'
        )
        log_action(conn, session['user_id'], 'create', new_id,
                   f'Создано обращение: {applicant}')
        conn.commit()
        conn.close()
        flash('Обращение сохранено', 'success')
        return redirect(url_for('requests.view_request', rid=new_id))

    lf, di, src, emp, subjects, results, all_users = get_classifiers(conn)
    conn.close()
    return render_template(
        'form.html', req=None, today=date.today().isoformat(),
        legal_forms=lf, districts=di, source_types=src,
        employees=emp, required_fields=REQUIRED_FIELDS,
        subjects=subjects, results=results, all_users=all_users
    )


# ─── РЕДАКТИРОВАНИЕ ОБРАЩЕНИЯ ─────────────────────────────────────────
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

        af   = req['answer_file']
        file = request.files.get('answer_file')
        if file and file.filename and allowed_file(file.filename):
            fn2 = secure_filename(file.filename)
            file.save(os.path.join(UPLOADS_DIR, fn2))
            af = fn2

        uploaded_files = request.files.getlist('request_files')
        saved_names = []
        for uf in uploaded_files:
            if uf and uf.filename and allowed_file(uf.filename):
                fn2 = secure_filename(uf.filename)
                uf.save(os.path.join(UPLOADS_DIR, fn2))
                saved_names.append(fn2)
        if saved_names:
            vals[ALL_FIELDS.index('request_files')] = ','.join(saved_names)

        edit_reason = request.form.get('edit_reason', '').strip()
        updated_by  = session.get('user_id')

        set_clause = ', '.join([f"{f}=?" for f in ALL_FIELDS])
        conn.execute(
            f"UPDATE requests SET {set_clause}, updated_at=?, updated_by=?, "
            f"edit_reason=?, answer_file=? WHERE id=?",
            vals + [now, updated_by, edit_reason, af, rid]
        )

        new_req = conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
        save_history(conn, rid, session['user_id'], old_req, new_req)

        num = req['request_number'] or f'ID:{rid}'
        reason_str = f' | Причина: {edit_reason}' if edit_reason else ''
        log_action(conn, session['user_id'], 'edit', rid,
                   f'Обращение {num}{reason_str}')
        conn.commit()
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


# ─── ПРОСМОТР ОБРАЩЕНИЯ ────────────────────────────────────────────────
@requests_bp.route('/view/<int:rid>')
@login_required
def view_request(rid):
    conn = get_db()
    req  = conn.execute(
        "SELECT r.*, u.full_name AS employee_name, ass.full_name AS assigned_name, "
        "adm.full_name AS admin_name, upd.full_name AS updated_by_name, "
        "st.name AS subject_type_name, rt.name AS result_type_name, rt.color_hex AS result_color, "
        "resp.full_name AS responsible_name, rev.full_name AS reviewer_name "
        "FROM requests r "
        "LEFT JOIN users u    ON r.created_by    = u.id "
        "LEFT JOIN users ass  ON r.assigned_to   = ass.id "
        "LEFT JOIN users adm  ON r.confirmed_by  = adm.id "
        "LEFT JOIN users upd  ON r.updated_by    = upd.id "
        "LEFT JOIN subject_types st  ON r.subject_type_id = st.id "
        "LEFT JOIN result_types  rt  ON r.result_type_id  = rt.id "
        "LEFT JOIN users resp ON r.responsible_id = resp.id "
        "LEFT JOIN users rev  ON r.reviewer_id    = rev.id "
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

    all_users = conn.execute(
        "SELECT id, full_name, role FROM users WHERE is_active=1 ORDER BY full_name"
    ).fetchall()
    conn.close()

    today_str = date.today().isoformat()
    return render_template(
        'view.html', req=req, all_users=all_users,
        okved_name=okved_name, today_str=today_str,
        status_labels=STATUS_LABELS,
    )


# ─── ИСТОРИЯ ───────────────────────────────────────────────────────────────
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


# ─── РЕГИСТРАЦИЯ: ПРИСВОИТЬ НОМЕР (#53) ────────────────────────────
@requests_bp.route('/request/<int:rid>/register', methods=['POST'])
@login_required
def register_request(rid):
    """
    Кнопка «Присвоить номер»:
    1. Проверяет обязательные поля
    2. Присваивает регномер ЗУ-ГГГГ-XXXX
    3. Получает ответственное лицо из модального окна
    4. Сразу переводит в in_progress + запускает срок
    """
    conn = get_db()
    req  = conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not req:
        conn.close()
        flash('Не найдено', 'error')
        return redirect(url_for('requests.index'))

    if req['request_number']:
        conn.close()
        flash('Номер уже присвоен', 'warning')
        return redirect(url_for('requests.view_request', rid=rid))

    # Проверка обязательных полей
    missing = [label for field, label in REQUIRED_FIELDS.items() if not req[field]]
    if missing:
        conn.close()
        flash(f'Нельзя зарегистрировать: не заполнены поля: {", ".join(missing)}', 'error')
        return redirect(url_for('requests.view_request', rid=rid))

    # Ответственное лицо из модалки
    responsible_id       = request.form.get('responsible_id', '').strip()
    not_in_system        = request.form.get('responsible_not_in_system', '0')
    responsible_external = request.form.get('responsible_name_external', '').strip()
    review_days_raw      = request.form.get('review_days', '7').strip()

    try:
        responsible_id = int(responsible_id) if responsible_id else None
    except ValueError:
        responsible_id = None
    not_in_system = 1 if not_in_system in ('1', 'on', 'true') else 0
    try:
        review_days = max(1, int(review_days_raw))
    except ValueError:
        review_days = 7

    if not_in_system and not responsible_external:
        conn.close()
        flash('Укажите ФИО ответственного лица', 'error')
        return redirect(url_for('requests.view_request', rid=rid))
    if not not_in_system and not responsible_id:
        conn.close()
        flash('Выберите ответственное лицо из списка', 'error')
        return redirect(url_for('requests.view_request', rid=rid))

    # Присвоение номера
    now  = datetime.now()
    year = now.year
    count = conn.execute(
        "SELECT COUNT(*) FROM requests WHERE request_number IS NOT NULL"
    ).fetchone()[0] + 1
    num      = f"ЗУ-{year}-{count:04d}"
    reg_at   = now.strftime('%Y-%m-%d %H:%M:%S')
    deadline = (now + timedelta(days=review_days)).strftime('%Y-%m-%d')

    conn.execute(
        """UPDATE requests SET
            status='in_progress',
            request_number=?,
            registered_at=?,
            review_days=?,
            review_deadline=?,
            responsible_id=?,
            responsible_not_in_system=?,
            responsible_name_external=?,
            updated_at=?
        WHERE id=?""",
        (num, reg_at, review_days, deadline,
         responsible_id, not_in_system, responsible_external or None,
         reg_at, rid)
    )

    # Уведомления
    conn.execute(
        "INSERT INTO notifications (user_id,message,link) VALUES (?,?,?)",
        (req['created_by'],
         f'Обращению присвоен номер {num}', f'/view/{rid}')
    )
    if responsible_id:
        conn.execute(
            "INSERT INTO notifications (user_id,message,link) VALUES (?,?,?)",
            (responsible_id,
             f'Вам назначено обращение {num} для подбора площадок', f'/view/{rid}')
        )

    log_action(conn, session['user_id'], 'register', rid,
               f'Номер: {num}, ответственный: {responsible_external or responsible_id}, срок: {review_days} дн.')
    conn.commit()
    conn.close()
    flash(f'Обращение зарегистрировано. Номер: {num}. Срок: до {deadline}.', 'success')
    return redirect(url_for('requests.view_request', rid=rid))


# ─── СМЕНА СТАТУСА (#53) ────────────────────────────────────────────────
@requests_bp.route('/request/<int:rid>/status', methods=['POST'])
@login_required
def change_status(rid):
    ns   = request.form.get('status')
    conn = get_db()
    req  = conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    role = session.get('role')
    uid  = session.get('user_id')
    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    today = date.today().isoformat()

    if not req:
        conn.close()
        flash('Не найдено', 'error')
        return redirect(url_for('requests.index'))

    cur = req['status']

    # Админ может вернуть любое обращение в черновик
    if ns == 'draft':
        if role != 'admin':
            conn.close()
            abort(403)
        conn.execute(
            "UPDATE requests SET status='draft', updated_at=? WHERE id=?",
            (now, rid)
        )
        log_action(conn, uid, 'admin_return', rid, 'Админ вернул в черновик')
        conn.commit()
        conn.close()
        flash('Обращение возвращено в черновик', 'warning')
        return redirect(url_for('requests.view_request', rid=rid))

    # Проверка допустимости перехода
    if cur not in ALLOWED_TRANSITIONS or ns not in ALLOWED_TRANSITIONS.get(cur, ()):
        conn.close()
        flash(f'Недопустимый переход: {STATUS_LABELS.get(cur,cur)} → {STATUS_LABELS.get(ns,ns)}', 'error')
        return redirect(url_for('requests.view_request', rid=rid))

    extra_fields = {}

    # ─ in_progress → under_review: файл + проверяющий
    if ns == 'under_review':
        if not req['answer_file']:
            conn.close()
            flash('Загрузите файл перед отправкой на проверку', 'error')
            return redirect(url_for('requests.view_request', rid=rid))
        reviewer_id      = request.form.get('reviewer_id', '').strip()
        not_in_sys       = request.form.get('reviewer_not_in_system', '0')
        reviewer_ext     = request.form.get('reviewer_name_external', '').strip()
        not_in_sys       = 1 if not_in_sys in ('1', 'on', 'true') else 0
        try:
            reviewer_id = int(reviewer_id) if reviewer_id else None
        except ValueError:
            reviewer_id = None
        if not_in_sys and not reviewer_ext:
            conn.close()
            flash('Укажите ФИО проверяющего', 'error')
            return redirect(url_for('requests.view_request', rid=rid))
        if not not_in_sys and not reviewer_id:
            conn.close()
            flash('Выберите проверяющего из списка', 'error')
            return redirect(url_for('requests.view_request', rid=rid))
        extra_fields = {
            'reviewer_id': reviewer_id,
            'reviewer_not_in_system': not_in_sys,
            'reviewer_name_external': reviewer_ext or None,
            'reviewer_decision': None,
            'reviewer_comment': None,
            'reviewer_decision_at': None,
        }
        log_action(conn, uid, 'send_reviewer', rid,
                   f'Проверяющий: {reviewer_ext or reviewer_id}')

    # ─ sent_to_applicant: дата + способ отправки
    elif ns == 'sent_to_applicant':
        sent_at     = request.form.get('sent_to_applicant_at', today)
        send_method = request.form.get('send_method', '').strip()
        if not sent_at or not send_method:
            conn.close()
            flash('Укажите дату и способ отправки', 'error')
            return redirect(url_for('requests.view_request', rid=rid))
        extra_fields = {
            'sent_to_applicant_at': sent_at,
            'send_method': send_method,
        }
        log_action(conn, uid, 'docs_sent', rid,
                   f'Способ: {send_method}, дата: {sent_at}')

    # ─ closed: обратная связь
    elif ns == 'closed':
        feedback    = request.form.get('applicant_feedback', '').strip()
        feedback_at = request.form.get('applicant_feedback_at', today)
        if not feedback:
            conn.close()
            flash('Заполните поле «Обратная связь от заявителя»', 'error')
            return redirect(url_for('requests.view_request', rid=rid))
        extra_fields = {
            'applicant_feedback': feedback,
            'applicant_feedback_at': feedback_at,
        }
        log_action(conn, uid, 'close', rid, f'ОБ от заявителя: {feedback[:80]}')

    elif ns == 'in_progress' and cur == 'under_review':
        # Отказ проверяющего через change_status (если не в системе)
        log_action(conn, uid, 'status', rid, 'Отклонено, вернули в работу')

    else:
        log_action(conn, uid, 'status', rid,
                   f'{STATUS_LABELS.get(cur,cur)} → {STATUS_LABELS.get(ns,ns)}')

    # Запись в БД
    set_parts = ['status=?', 'updated_at=?']
    set_vals  = [ns, now]
    for k, v in extra_fields.items():
        set_parts.append(f'{k}=?')
        set_vals.append(v)
    set_vals.append(rid)

    conn.execute(
        f"UPDATE requests SET {', '.join(set_parts)} WHERE id=?",
        set_vals
    )
    conn.commit()
    conn.close()
    flash(f'Статус обновлён: {STATUS_LABELS.get(ns, ns)}', 'success')
    return redirect(url_for('requests.view_request', rid=rid))


# ─── РЕШЕНИЕ ПРОВЕРЯЮЩЕГО (#53) ──────────────────────────────────────
@requests_bp.route('/request/<int:rid>/reviewer_decision', methods=['POST'])
@login_required
def reviewer_decision(rid):
    """
    Проверяющий из системы нажимает «Одобрить» или «Отклонить».
    Доступно только назначенному reviewer_id или admin.
    """
    conn = get_db()
    req  = conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    uid  = session.get('user_id')
    role = session.get('role')

    if not req:
        conn.close()
        flash('Не найдено', 'error')
        return redirect(url_for('requests.index'))

    if req['status'] != 'under_review':
        conn.close()
        flash('Действие недоступно для текущего статуса', 'error')
        return redirect(url_for('requests.view_request', rid=rid))

    # Проверка прав: назначенный reviewer или admin
    if role != 'admin' and req['reviewer_id'] != uid:
        conn.close()
        abort(403)

    decision = request.form.get('decision')  # 'approved' | 'rejected'
    comment  = request.form.get('reviewer_comment', '').strip()
    now      = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if decision not in ('approved', 'rejected'):
        conn.close()
        flash('Неверное решение', 'error')
        return redirect(url_for('requests.view_request', rid=rid))

    new_status = 'ready_to_send' if decision == 'approved' else 'in_progress'
    action_key = 'reviewer_ok' if decision == 'approved' else 'reviewer_rej'

    conn.execute(
        """UPDATE requests SET
            status=?,
            reviewer_decision=?,
            reviewer_comment=?,
            reviewer_decision_at=?,
            updated_at=?
        WHERE id=?""",
        (new_status, decision, comment or None, now, now, rid)
    )

    # Уведомить ответственное лицо
    if req['responsible_id']:
        decision_ru = 'Одобрено' if decision == 'approved' else 'Отклонено'
        msg = f'Обращение {req["request_number"] or rid}: {decision_ru}'
        if comment:
            msg += f'. Комментарий: {comment}'
        conn.execute(
            "INSERT INTO notifications (user_id,message,link) VALUES (?,?,?)",
            (req['responsible_id'], msg, f'/view/{rid}')
        )

    log_action(conn, uid, action_key, rid,
               f'Решение: {decision}, комментарий: {comment[:80] if comment else "-"}')
    conn.commit()
    conn.close()

    decision_ru = 'Одобрено' if decision == 'approved' else 'Отклонено, вернули в работу'
    flash(f'{decision_ru}. Статус: {STATUS_LABELS[new_status]}', 'success')
    return redirect(url_for('requests.view_request', rid=rid))


# ─── ИЗБРАННОЕ ────────────────────────────────────────────────────────────
@requests_bp.route('/request/<int:rid>/favorite', methods=['POST'])
@login_required
def toggle_favorite(rid):
    conn = get_db()
    uid  = session['user_id']
    row  = conn.execute(
        "SELECT id FROM favorites WHERE user_id=? AND request_id=?", (uid, rid)
    ).fetchone()
    if row:
        conn.execute("DELETE FROM favorites WHERE id=?", (row['id'],))
        log_action(conn, uid, 'favorite', rid, 'Убрано из избранного')
    else:
        conn.execute(
            "INSERT OR IGNORE INTO favorites (user_id,request_id) VALUES (?,?)", (uid, rid)
        )
        log_action(conn, uid, 'favorite', rid, 'Добавлено в избранное')
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('requests.index'))


# ─── УДАЛЕНИЕ ────────────────────────────────────────────────────────────
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


# ─── ФАЙЛЫ ──────────────────────────────────────────────────────────────
@requests_bp.route('/uploads/<path:filename>')
@login_required
def uploaded_file(filename):
    return send_file(os.path.join(UPLOADS_DIR, filename), as_attachment=True)


# ─── УСТАРЕВШИЕ МАРШРУТЫ (оставлены для обратной совместимости) ────────────
@requests_bp.route('/request/<int:rid>/confirm', methods=['POST'])
@login_required
@admin_required
def confirm_request(rid):
    """Устаревший маршрут. Оставлен для совместимости. Используйте register_request."""
    flash('Используйте кнопку «Присвоить номер» на странице обращения', 'info')
    return redirect(url_for('requests.view_request', rid=rid))


@requests_bp.route('/request/<int:rid>/answer', methods=['POST'])
@login_required
def answer_request(rid):
    """Устаревший маршрут. Оставлен для совместимости. Используйте change_status."""
    flash('Используйте цепочку статусов на странице обращения', 'info')
    return redirect(url_for('requests.view_request', rid=rid))


@requests_bp.route('/request/<int:rid>/assign_number', methods=['POST'])
@login_required
def assign_number(rid):
    """Устаревший маршрут. Оставлен для совместимости. Используйте register_request."""
    flash('Используйте кнопку «Присвоить номер» на странице обращения', 'info')
    return redirect(url_for('requests.view_request', rid=rid))
