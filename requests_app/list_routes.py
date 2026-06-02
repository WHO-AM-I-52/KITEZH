import json
import math
from datetime import date

from flask import render_template, request, session

from dashboard import build_dash
from db import get_db
from auth_utils import login_required
from . import requests_bp

VALID_PAGE_SIZES = (10, 25, 50, 100)
DEFAULT_PAGE_SIZE = 50

# Все допустимые статусы issue #53
ALL_STATUSES = (
    'draft', 'registered', 'in_progress',
    'under_review', 'ready_to_send', 'sent_to_applicant', 'closed'
)


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
        where += (" AND r.status IN ('draft','registered','in_progress') "
                  "AND julianday('now')-julianday(r.request_date) > 7")
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
    try:
        per_page = int(request.args.get('per_page', DEFAULT_PAGE_SIZE))
        if per_page not in VALID_PAGE_SIZES:
            per_page = DEFAULT_PAGE_SIZE
    except (ValueError, TypeError):
        per_page = DEFAULT_PAGE_SIZE

    conn = get_db()
    dash = build_dash(conn, period)

    uid = session['user_id']

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

    total_pages = max(1, math.ceil(total_filtered / per_page))
    page        = min(page, total_pages)
    offset      = (page - 1) * per_page

    reqs = conn.execute(q + f" LIMIT {per_page} OFFSET {offset}", params).fetchall()

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
        page=page, total_pages=total_pages, per_page=per_page,
        valid_page_sizes=(10, 25, 50, 100),
        all_statuses=ALL_STATUSES,
    )


@requests_bp.route('/dashboard')
@login_required
def dashboard():
    period = request.args.get('period', 'month')
    conn   = get_db()
    dash   = build_dash(conn, period)
    conn.close()
    return render_template('dashboard.html', dash=dash)
