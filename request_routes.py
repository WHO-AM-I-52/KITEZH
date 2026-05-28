# ╔══════════════════════════════════════════════════════════════╗
# ║ request_routes.py                                            ║
# ║ v2.1: поддержка новых полей МинЭК (предмет, итоги)    ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, session, flash, send_file, jsonify, abort
)
from datetime import datetime, date
from werkzeug.utils import secure_filename
import os
import json

from dashboard import build_dash
from db import get_db, UPLOADS_DIR
from auth_utils import login_required, admin_required
from form_utils import build_values, get_classifiers, ALL_FIELDS, REQUIRED_FIELDS
from validators import allowed_file, _int, validate_inn
from request_history import save_history, get_history, rollback_history
from activity_log import log_action
from ocr_utils import extract_anketa_fields

requests_bp = Blueprint('requests', __name__)


# ─── ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ──────────────────────────────────────────────

def _build_filter(sf, df, dt, af, ef, src_f, search, quick, user_id, for_count=False):
    """Собирает WHERE-часть запроса и параметры для фильтрации списка обращений.

    for_count=True — режим для SELECT COUNT(*), где favorite_flag не вычисляется.
    for_count=False — режим для основного SELECT, где favorite_flag = CASE WHEN ...
    fix #34
    """
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
        where += (" AND r.status IN ('draft','review','accepted') "
                  "AND julianday('now')-julianday(r.request_date) > 7")
    elif quick == 'mine':
        where += " AND r.assigned_to=?"
        params.append(user_id)
    elif quick == 'unassigned':
        where += " AND (r.assigned_to IS NULL OR r.assigned_to=0)"
    elif quick == 'favorites':
        # favorite_flag вычисляется только в основном SELECT, не в COUNT(*)
        if for_count:
            where += " AND f.id IS NOT NULL"
        else:
            where += " AND favorite_flag = 1"

    return where, params


# ─── СПИСОК ОБРАЩЕНИЙ + ФИЛЬТРЫ ─────────────────────────────────────────

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

    conn = get_db()
    dash = build_dash(conn, period)

    uid = session['user_id']

    # ─── Основной запрос: список обращений ────────────────────────────
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

    # ─── Запрос подсчёта записей с теми же фильтрами ──────────────────
    count_where, count_params = _build_filter(sf, df, dt, af, ef, src_f, search, quick, uid, for_count=True)
    count_q = (
        "SELECT COUNT(*) FROM requests r "
        "LEFT JOIN users u   ON r.created_by  = u.id "
        "LEFT JOIN users ass ON r.assigned_to = ass.id "
        "LEFT JOIN favorites f ON f.request_id = r.id AND f.user_id = ? "
        f"{count_where}"
    )

    total_filtered = conn.execute(count_q, count_params).fetchone()[0]
    reqs = conn.execute(q, params).fetchall()

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
        try:    sp = json.loads(sfr['params'])
        except: sp = {}
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
    )


# ─── ДАШБОРД ────────────────────────────────────────────────────────────────────────

@requests_bp.route('/dashboard')
@login_required
def dashboard():
    period = request.args.get('period', 'month')
    conn   = get_db()
    dash   = build_dash(conn, period)
    conn.close()
    return render_template('dashboard.html', dash=dash)


# ─── СОЗДАНИЕ ОБРАЩЕНИЯ ────────────────────────────────────────────────

@requests_bp.route('/request/new', methods=['GET', 'POST'])
@login_required
def new_request():
    conn = get_db()

    if request.method == 'POST':
        now    = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        action = request.form.get('action', 'save')

        # ── Ветка OCR ─────────────────────────────────────────────────────
        if action == 'ocr':
            ocr_file = request.files.get('ocr_form')
            if not ocr_file or not ocr_file.filename:
                flash('\u041d\u0435 \u0432\u044b\u0431\u0440\u0430\u043d \u0444\u0430\u0439\u043b \u0430\u043d\u043a\u0435\u0442\u044b \u0434\u043b\u044f OCR.', 'warning')
                conn.close()
                conn2 = get_db()
                lf2, di2, src2, emp2, subjects2, results2 = get_classifiers(conn2)
                conn2.close()
                return render_template(
                    'form.html', req=None, today=date.today().isoformat(),
                    legal_forms=lf2, districts=di2, source_types=src2,
                    employees=emp2, required_fields=REQUIRED_FIELDS,
                    subjects=subjects2, results=results2
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
            lf2, di2, src2, emp2, subjects2, results2 = get_classifiers(conn2)
            conn2.close()

            if fields:
                fake_req = {f: '' for f in ALL_FIELDS}
                for k, v in fields.items():
                    if k in fake_req:
                        fake_req[k] = v
                flash(
                    '\u0410\u043d\u043a\u0435\u0442\u0430 \u0440\u0430\u0441\u043f\u043e\u0437\u043d\u0430\u043d\u0430: \u0447\u0430\u0441\u0442\u044c \u043f\u043e\u043b\u0435\u0439 \u0437\u0430\u043f\u043e\u043b\u043d\u0435\u043d\u0430 \u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0438. '
                    '\u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u043f\u0435\u0440\u0435\u0434 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u0435\u043c.', 'success'
                )
                return render_template(
                    'form.html', req=fake_req, today=date.today().isoformat(),
                    legal_forms=lf2, districts=di2, source_types=src2,
                    employees=emp2, required_fields=REQUIRED_FIELDS,
                    subjects=subjects2, results=results2, ocr_message=msg
                )
            else:
                flash(
                    '\u042f \u0435\u0449\u0451 \u043d\u0435 \u0441\u043b\u0438\u0448\u043a\u043e\u043c \u0443\u043c\u043d\u044b\u0439 \u0438 \u043d\u0435 \u0441\u043c\u043e\u0433 \u0441\u043e\u043f\u043e\u0441\u0442\u0430\u0432\u0438\u0442\u044c \u0434\u0430\u043d\u043d\u044b\u0435 \u0430\u043d\u043a\u0435\u0442\u044b. '
                    '\u0417\u0430\u043f\u043e\u043b\u043d\u0438\u0442\u0435 \u043f\u043e\u043b\u044f \u0432\u0440\u0443\u0447\u043d\u0443\u044e.', 'warning'
                )
                return render_template(
                    'form.html', req=None, today=date.today().isoformat(),
                    legal_forms=lf2, districts=di2, source_types=src2,
                    employees=emp2, required_fields=REQUIRED_FIELDS,
                    subjects=subjects2, results=results2,
                    ocr_message=msg if 'msg' in locals() else ''
                )

        # ── Обычная ветка сохранения ───────────────────────────────
        inn = request.form.get('applicant_inn', '').strip()
        ok_inn, inn_reason = validate_inn(inn)
        if inn_reason == 'format':
            flash('\u0418\u041d\u041d \u0434\u043e\u043b\u0436\u0435\u043d \u0441\u043e\u0434\u0435\u0440\u0436\u0430\u0442\u044c \u0442\u043e\u043b\u044c\u043a\u043e \u0446\u0438\u0444\u0440\u044b.', 'warning')
        elif inn_reason == 'length':
            flash('\u0414\u043b\u0438\u043d\u0430 \u0418\u041d\u041d \u0434\u043e\u043b\u0436\u043d\u0430 \u0431\u044b\u0442\u044c 10 \u0446\u0438\u0444\u0440 (\u044e\u0440\u043b\u0438\u0446\u0430) \u0438\u043b\u0438 12 \u0446\u0438\u0444\u0440 (\u0418\u041f).', 'warning')
        elif inn_reason == 'checksum':
            flash('\u0418\u041d\u041d \u0443\u043a\u0430\u0437\u0430\u043d \u0441 \u043e\u0448\u0438\u0431\u043a\u043e\u0439. \u041a\u043e\u043d\u0442\u0440\u043e\u043b\u044c\u043d\u0430\u044f \u0441\u0443\u043c\u043c\u0430 \u043d\u0435 \u0441\u043e\u0432\u043f\u0430\u0434\u0430\u0435\u0442.', 'warning')

        vals = build_values(request.form)

        uploaded_files = request.files.getlist('request_files')
        saved_names = []
        for uf in uploaded_files:
            if uf and uf.filename and allowed_file(uf.filename):
                fn2 = secure_filename(uf.filename)
                uf.save(os.path.join(UPLOADS_DIR, fn2))
                saved_names.append(fn2)
        vals[ALL_FIELDS.index('request_files')] = ','.join(saved_names) if saved_names else None

        cols = ', '.join(ALL_FIELDS) + ', created_by, created_at, updated_at'
        ph   = ','.join(['?'] * len(ALL_FIELDS)) + ',?,?,?'
        cursor = conn.execute(
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
                   f'\u0421\u043e\u0437\u0434\u0430\u043d\u043e \u043e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u0435: {applicant}')
        conn.commit()
        conn.close()
        flash('\u041e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u0435 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u043e', 'success')
        return redirect(url_for('requests.index'))

    # ── GET: чистая форма ─────────────────────────────────────────────────────
    lf, di, src, emp, subjects, results = get_classifiers(conn)
    conn.close()
    return render_template(
        'form.html', req=None, today=date.today().isoformat(),
        legal_forms=lf, districts=di, source_types=src,
        employees=emp, required_fields=REQUIRED_FIELDS,
        subjects=subjects, results=results
    )


# ─── РЕДАКТИРОВАНИЕ ОБРАЩЕНИЯ ──────────────────────────────────────────

@requests_bp.route('/request/<int:rid>', methods=['GET', 'POST'])
@login_required
def edit_request(rid):
    conn = get_db()
    req  = conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not req:
        conn.close()
        flash('\u041d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e', 'error')
        return redirect(url_for('requests.index'))

    old_req = dict(req)

    if request.method == 'POST':
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        inn = request.form.get('applicant_inn', '').strip()
        ok_inn, inn_reason = validate_inn(inn)
        if inn_reason == 'format':
            flash('\u0418\u041d\u041d \u0434\u043e\u043b\u0436\u0435\u043d \u0441\u043e\u0434\u0435\u0440\u0436\u0430\u0442\u044c \u0442\u043e\u043b\u044c\u043a\u043e \u0446\u0438\u0444\u0440\u044b.', 'warning')
        elif inn_reason == 'length':
            flash('\u0414\u043b\u0438\u043d\u0430 \u0418\u041d\u041d \u0434\u043e\u043b\u0436\u043d\u0430 \u0431\u044b\u0442\u044c 10 \u0446\u0438\u0444\u0440 (\u044e\u0440\u043b\u0438\u0446\u0430) \u0438\u043b\u0438 12 \u0446\u0438\u0444\u0440 (\u0418\u041f).', 'warning')
        elif inn_reason == 'checksum':
            flash('\u0418\u041d\u041d \u0443\u043a\u0430\u0437\u0430\u043d \u0441 \u043e\u0448\u0438\u0431\u043a\u043e\u0439. \u041a\u043e\u043d\u0442\u0440\u043e\u043b\u044c\u043d\u0430\u044f \u0441\u0443\u043c\u043c\u0430 \u043d\u0435 \u0441\u043e\u0432\u043f\u0430\u0434\u0430\u0435\u0442.', 'warning')

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
        reason_str = f' | \u041f\u0440\u0438\u0447\u0438\u043d\u0430: {edit_reason}' if edit_reason else ''
        log_action(conn, session['user_id'], 'edit', rid,
                   f'\u041e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u0435 {num}{reason_str}')
        conn.commit()
        conn.close()
        flash('\u041e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u0435 \u043e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u043e', 'success')
        return redirect(url_for('requests.index'))

    lf, di, src, emp, subjects, results = get_classifiers(conn)
    conn.close()
    return render_template(
        'form.html', req=req, today=date.today().isoformat(),
        legal_forms=lf, districts=di, source_types=src,
        employees=emp, required_fields=REQUIRED_FIELDS,
        subjects=subjects, results=results
    )


# ─── ПРОСМОТР ОБРАЩЕНИЯ ────────────────────────────────────────────

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
        flash('\u041d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e', 'error')
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


# ─── ИСТОРИЯ ИЗМЕНЕНИЙ ───────────────────────────────────────────────

@requests_bp.route('/view/<int:rid>/history')
@login_required
@admin_required
def request_history_view(rid):
    conn = get_db()
    req  = conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    conn.close()
    if not req:
        flash('\u041d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e', 'error')
        return redirect(url_for('requests.index'))
    history = get_history(rid)
    return render_template('history.html', history=history, req=req, rid=rid)


# ─── ОТКАТ ──────────────────────────────────────────────────────────────────────

@requests_bp.route('/view/<int:rid>/rollback/<int:hid>', methods=['POST'])
@login_required
@admin_required
def rollback_request(rid, hid):
    conn = get_db()
    ok   = rollback_history(hid, rid)
    if ok:
        log_action(conn, session['user_id'], 'rollback', rid,
                   f'\u041e\u0442\u043a\u0430\u0442 \u043a \u0432\u0435\u0440\u0441\u0438\u0438 history_id={hid}')
        conn.commit()
        flash('\u041e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u0435 \u043e\u0442\u043a\u0430\u0447\u0435\u043d\u043e \u043a \u0432\u044b\u0431\u0440\u0430\u043d\u043d\u043e\u0439 \u0432\u0435\u0440\u0441\u0438\u0438', 'success')
    else:
        flash('\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0432\u044b\u043f\u043e\u043b\u043d\u0438\u0442\u044c \u043e\u0442\u043a\u0430\u0442 — \u0437\u0430\u043f\u0438\u0441\u044c \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u0430', 'error')
    conn.close()
    return redirect(url_for('requests.view_request', rid=rid))


# ─── ПОДТВЕРЖДЕНИЕ / ВОЗВРАТ ───────────────────────────────────

@requests_bp.route('/request/<int:rid>/confirm', methods=['POST'])
@login_required
@admin_required
def confirm_request(rid):
    conn    = get_db()
    req     = conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not req:
        conn.close()
        flash('\u041d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e', 'error')
        return redirect(url_for('requests.index'))

    action  = request.form.get('action')
    comment = request.form.get('admin_comment', '').strip()
    now     = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if action == 'accept':
        year     = datetime.now().year
        count    = conn.execute(
            "SELECT COUNT(*) FROM requests WHERE status!='draft'"
        ).fetchone()[0] + 1
        num      = f"\u0417\u0423-{year}-{count:04d}"
        assigned = _int(request.form.get('assigned_to')) or req['assigned_to']

        conn.execute(
            "UPDATE requests SET status='accepted', request_number=?, "
            "confirmed_by=?, confirmed_at=?, admin_comment=?, assigned_to=? WHERE id=?",
            (num, session['user_id'], now, comment, assigned, rid)
        )
        conn.execute(
            "INSERT INTO notifications (user_id,message,link) VALUES (?,?,?)",
            (req['created_by'],
             f'\u041e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u0435 \u043f\u0440\u0438\u043d\u044f\u0442\u043e \u0432 \u0440\u0430\u0431\u043e\u0442\u0443. \u041d\u043e\u043c\u0435\u0440: {num}', f'/view/{rid}')
        )
        if assigned:
            conn.execute(
                "INSERT INTO notifications (user_id,message,link) VALUES (?,?,?)",
                (assigned,
                 f'\u0412\u0430\u043c \u043d\u0430\u0437\u043d\u0430\u0447\u0435\u043d\u043e \u043d\u043e\u0432\u043e\u0435 \u043e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u0435. \u041d\u043e\u043c\u0435\u0440: {num}', f'/view/{rid}')
            )
        log_action(conn, session['user_id'], 'accept', rid,
                   f'\u041f\u0440\u0438\u043d\u044f\u0442\u043e \u0432 \u0440\u0430\u0431\u043e\u0442\u0443, \u043d\u043e\u043c\u0435\u0440: {num}, \u043e\u0442\u0432\u0435\u0442\u0441\u0442\u0432\u0435\u043d\u043d\u044b\u0439 ID={assigned}')
        conn.commit()
        flash(f'\u041f\u0440\u0438\u043d\u044f\u0442\u043e \u0432 \u0440\u0430\u0431\u043e\u0442\u0443, \u043d\u043e\u043c\u0435\u0440: {num}', 'success')

    elif action == 'reject':
        conn.execute(
            "UPDATE requests SET status='draft', admin_comment=? WHERE id=?",
            (comment, rid)
        )
        conn.execute(
            "INSERT INTO notifications (user_id,message,link) VALUES (?,?,?)",
            (req['created_by'],
             f'\u041e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u0435 \u0432\u043e\u0437\u0432\u0440\u0430\u0449\u0435\u043d\u043e \u043d\u0430 \u0434\u043e\u0440\u0430\u0431\u043e\u0442\u043a\u0443. \u041a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0439: {comment}',
             f'/view/{rid}')
        )
        log_action(conn, session['user_id'], 'reject', rid,
                   f'\u0412\u043e\u0437\u0432\u0440\u0430\u0442 \u043d\u0430 \u0434\u043e\u0440\u0430\u0431\u043e\u0442\u043a\u0443. \u041a\u043e\u043c\u043c\u0435\u043d\u0442\u0430\u0440\u0438\u0439: {comment}')
        conn.commit()
        flash('\u0412\u043e\u0437\u0432\u0440\u0430\u0449\u0435\u043d\u043e \u043d\u0430 \u0434\u043e\u0440\u0430\u0431\u043e\u0442\u043a\u0443', 'warning')

    conn.close()
    return redirect(url_for('requests.view_request', rid=rid))


# ─── ФИКСАЦИЯ ОТВЕТА ────────────────────────────────────────────

@requests_bp.route('/request/<int:rid>/answer', methods=['POST'])
@login_required
def answer_request(rid):
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
               f'\u0421\u043f\u043e\u0441\u043e\u0431: {method}' + (f' ({answer_sys_num})' if answer_sys_num else ''))
    conn.commit()
    conn.close()
    flash('\u041e\u0442\u0432\u0435\u0442 \u0437\u0430\u0444\u0438\u043a\u0441\u0438\u0440\u043e\u0432\u0430\u043d', 'success')
    return redirect(url_for('requests.view_request', rid=rid))


# ─── СМЕНА СТАТУСА ────────────────────────────────────────────────

@requests_bp.route('/request/<int:rid>/status', methods=['POST'])
@login_required
def change_status(rid):
    ns = request.form.get('status')
    if ns not in ('draft', 'review', 'accepted', 'answered'):
        flash('\u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u0441\u0442\u0430\u0442\u0443\u0441', 'error')
        return redirect(url_for('requests.view_request', rid=rid))
    conn = get_db()
    conn.execute(
        "UPDATE requests SET status=?, updated_at=? WHERE id=?",
        (ns, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), rid)
    )
    log_action(conn, session['user_id'], 'status', rid, f'\u041d\u043e\u0432\u044b\u0439 \u0441\u0442\u0430\u0442\u0443\u0441: {ns}')
    conn.commit()
    conn.close()
    return redirect(url_for('requests.view_request', rid=rid))


# ─── ИЗБРАННОЕ ──────────────────────────────────────────────────────────────────

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
        log_action(conn, uid, 'favorite', rid, '\u0423\u0431\u0440\u0430\u043d\u043e \u0438\u0437 \u0438\u0437\u0431\u0440\u0430\u043d\u043d\u043e\u0433\u043e')
    else:
        conn.execute(
            "INSERT OR IGNORE INTO favorites (user_id,request_id) VALUES (?,?)", (uid, rid)
        )
        log_action(conn, uid, 'favorite', rid, '\u0414\u043e\u0431\u0430\u0432\u043b\u0435\u043d\u043e \u0432 \u0438\u0437\u0431\u0440\u0430\u043d\u043d\u043e\u0435')
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('requests.index'))


# ─── УДАЛЕНИЕ ────────────────────────────────────────────────────────────────

@requests_bp.route('/request/<int:rid>/delete', methods=['POST'])
@login_required
@admin_required
def delete_request(rid):
    conn = get_db()
    req  = conn.execute(
        "SELECT request_number, applicant_short_name FROM requests WHERE id=?", (rid,)
    ).fetchone()
    num  = req['request_number'] or f'ID:{rid}' if req else f'ID:{rid}'
    name = req['applicant_short_name'] or '\u2014' if req else '\u2014'

    log_action(conn, session['user_id'], 'delete', rid,
               f'\u0423\u0434\u0430\u043b\u0435\u043d\u043e \u043e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u0435 {num} ({name})')
    conn.execute("DELETE FROM requests WHERE id=?", (rid,))
    conn.commit()
    conn.close()
    flash('\u041e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u0435 \u0443\u0434\u0430\u043b\u0435\u043d\u043e', 'success')
    return redirect(url_for('requests.index'))


# ─── ФАЙЛЫ ───────────────────────────────────────────────────────────────────────

@requests_bp.route('/uploads/<path:filename>')
@login_required
def uploaded_file(filename):
    return send_file(os.path.join(UPLOADS_DIR, filename), as_attachment=True)


# ─── ПРИСВОЕНИЕ НОМЕРА ──────────────────────────────────────────────

@requests_bp.route('/request/<int:rid>/assign_number', methods=['POST'])
@login_required
def assign_number(rid):
    if session.get('role') != 'admin':
        abort(403)
    conn = get_db()
    req  = conn.execute("SELECT * FROM requests WHERE id=?", (rid,)).fetchone()
    if not req or req['request_number']:
        conn.close()
        flash('\u041d\u043e\u043c\u0435\u0440 \u0443\u0436\u0435 \u043f\u0440\u0438\u0441\u0432\u043e\u0435\u043d \u0438\u043b\u0438 \u043e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u0435 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d\u043e', 'warning')
        return redirect(url_for('requests.view_request', rid=rid))

    year  = datetime.now().year
    count = conn.execute(
        "SELECT COUNT(*) FROM requests WHERE request_number IS NOT NULL"
    ).fetchone()[0] + 1
    num   = f"\u0417\u0423-{year}-{count:04d}"

    conn.execute("UPDATE requests SET request_number=? WHERE id=?", (num, rid))
    log_action(conn, session['user_id'], 'status', rid,
               f'\u041f\u0440\u0438\u0441\u0432\u043e\u0435\u043d \u043d\u043e\u043c\u0435\u0440: {num}')
    conn.commit()
    conn.close()
    flash(f'\u041f\u0440\u0438\u0441\u0432\u043e\u0435\u043d \u043d\u043e\u043c\u0435\u0440: {num}', 'success')
    return redirect(url_for('requests.view_request', rid=rid))
