# ╔══════════════════════════════════════════════════════════════╗
# ║ api/requests_api.py                                          ║
# ║ GET /api/requests — JSON-эндпоинт для Tabulator.js             ║
# ║                                                               ║
# ║ Параметры:                                                  ║
# ║   page, size          — пагинация                             ║
# ║   sort, dir           — сортировка (field, asc|desc)        ║
# ║   filter[field]       — значение фильтра                    ║
# ║   filter_type[field]  — тип: like|starts|ends|=|empty|regex  ║
# ╚══════════════════════════════════════════════════════════════╝

import re
from datetime import date, timedelta
from flask import Blueprint, jsonify, request, session
from functools import wraps
from db import get_db

api_bp = Blueprint('api', __name__, url_prefix='/api')


# ─── Декоратор: требует авторизации ──────────────────────────────────
def login_required_api(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


# ─── Белые поля для сортировки (защита от SQL-инъекции) ─────────────
_ALLOWED_SORT = {
    'id', 'request_number', 'request_date', 'status',
    'applicant_full_name', 'applicant_short_name',
    'project_name', 'investment_total',
    'site_area_ha_min', 'site_build_area_m2_min',
    'jobs_total', 'assigned_name', 'source_type',
}


def _apply_filter(where, params, col, value, ftype):
    """SQL-фрагмент по типу Tabulator header filter."""
    if ftype == 'empty':
        where.append(f"(r.{col} IS NULL OR r.{col} = '')")
    elif ftype == '=':
        where.append(f"r.{col} = ?")
        params.append(value)
    elif ftype == 'starts':
        where.append(f"r.{col} LIKE ?")
        params.append(value + '%')
    elif ftype == 'ends':
        where.append(f"r.{col} LIKE ?")
        params.append('%' + value)
    elif ftype == 'regex':
        # SQLite не поддерживает REGEXP нативно — фаллбэк на LIKE
        where.append(f"r.{col} LIKE ?")
        params.append('%' + value + '%')
    else:  # like (по умолчанию)
        where.append(f"r.{col} LIKE ?")
        params.append('%' + value + '%')


def _date_range(chip):
    """Tabulator chip-фильтр дат: today/week/month → (date_from, date_to)."""
    today = date.today()
    if chip == 'today':
        return str(today), str(today)
    if chip == 'week':
        monday = today - timedelta(days=today.weekday())
        return str(monday), str(today)
    if chip == 'month':
        return str(today.replace(day=1)), str(today)
    return None, None


@api_bp.route('/requests')
@login_required_api
def get_requests():
    db = get_db()

    # ── Пагинация ─────────────────────────────────────────────────────
    try:
        page = max(1, int(request.args.get('page', 1)))
        size = min(200, max(1, int(request.args.get('size', 50))))
    except (ValueError, TypeError):
        page = 1
        size = 50
    offset = (page - 1) * size

    # ── Сортировка ─────────────────────────────────────────────────────
    sort_field = request.args.get('sort', 'request_date')
    sort_dir   = request.args.get('dir', 'desc').lower()
    if sort_field not in _ALLOWED_SORT:
        sort_field = 'request_date'
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'desc'

    # ── Фильтры ────────────────────────────────────────────────────────
    where  = []
    params = []

    # Права: не-админы видят только свои
    if session.get('role') != 'admin' and not session.get('perm_can_view_all'):
        where.append('r.created_by = ?')
        params.append(session['user_id'])

    # ─ Статус ─────────────────────────────────────────────────────────────
    status = request.args.get('filter[status]', '').strip()
    if status:
        where.append('r.status = ?')
        params.append(status)

    # ─ Заявитель ──────────────────────────────────────────────────────────
    applicant = request.args.get('filter[applicant]', '').strip()
    if applicant:
        ftype = request.args.get('filter_type[applicant]', 'like')
        _apply_filter(where, params,
                      'applicant_full_name', applicant, ftype)

    # ─ Поиск по тексту (project_name + все контакты) ────────────────────
    search = request.args.get('filter[search]', '').strip()
    if search:
        s = '%' + search + '%'
        where.append("""
            (r.project_name          LIKE ?
          OR r.applicant_full_name   LIKE ?
          OR r.applicant_short_name  LIKE ?
          OR r.contact_person        LIKE ?
          OR r.contact_phone         LIKE ?
          OR r.request_number        LIKE ?)
        """)
        params.extend([s, s, s, s, s, s])

    # ─ Ответственный ───────────────────────────────────────────────────────
    employee = request.args.get('filter[employee]', '').strip()
    if employee:
        # можно передать как id или как full_name
        if employee.isdigit():
            where.append('r.assigned_to = ?')
            params.append(int(employee))
        else:
            where.append('u.full_name LIKE ?')
            params.append('%' + employee + '%')

    # ─ Избранные ────────────────────────────────────────────────────────────
    favorite = request.args.get('filter[favorite]', '').strip()
    if favorite == '1':
        where.append(
            'EXISTS (SELECT 1 FROM favorites f '
            'WHERE f.request_id = r.id AND f.user_id = ?)'
        )
        params.append(session['user_id'])

    # ─ Дата ───────────────────────────────────────────────────────────────
    date_chip = request.args.get('filter[date_chip]', '').strip()
    date_from = request.args.get('filter[date_from]', '').strip()
    date_to   = request.args.get('filter[date_to]', '').strip()

    if date_chip:
        date_from, date_to = _date_range(date_chip)

    if date_from:
        where.append('r.request_date >= ?')
        params.append(date_from)
    if date_to:
        where.append('r.request_date <= ?')
        params.append(date_to)

    # ── Сборка SQL ─────────────────────────────────────────────────────
    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    base_query = f"""
        SELECT
            r.id,
            r.request_number,
            r.request_date,
            r.status,
            r.source_type,
            COALESCE(r.applicant_short_name, r.applicant_full_name) AS applicant,
            r.project_name,
            r.investment_total,
            r.site_area_ha_min,
            r.site_build_area_m2_min,
            r.jobs_total,
            u.full_name  AS assigned_name,
            r.assigned_to,
            r.review_deadline,
            r.created_by,
            CASE WHEN fav.id IS NOT NULL THEN 1 ELSE 0 END AS favorite_flag,
            CASE
                WHEN r.status NOT IN ('closed','draft','sent_to_applicant')
                 AND r.review_deadline IS NOT NULL
                 AND r.review_deadline < date('now')
                THEN 1 ELSE 0
            END AS overdue
        FROM requests r
        LEFT JOIN users   u   ON u.id = r.assigned_to
        LEFT JOIN favorites fav ON fav.request_id = r.id
                               AND fav.user_id = ?
        {where_sql}
    """
    count_query = f"""
        SELECT COUNT(*) FROM requests r
        LEFT JOIN users u ON u.id = r.assigned_to
        {where_sql}
    """

    count_params = [session['user_id']] + params
    total = db.execute(count_query, params).fetchone()[0]

    order_sql = f'ORDER BY r.{sort_field} {sort_dir} NULLS LAST'
    rows = db.execute(
        base_query + f' {order_sql} LIMIT ? OFFSET ?',
        [session['user_id']] + params + [size, offset]
    ).fetchall()

    db.close()

    data = []
    for r in rows:
        data.append({
            'id':               r['id'],
            'request_number':   r['request_number'] or '',
            'request_date':     r['request_date']   or '',
            'status':           r['status']         or '',
            'source_type':      r['source_type']    or '',
            'applicant':        r['applicant']      or '',
            'project_name':     r['project_name']   or '',
            'investment_total': r['investment_total'],
            'site_area_ha':     r['site_area_ha_min'],
            'site_area_m2':     r['site_build_area_m2_min'],
            'jobs_total':       r['jobs_total'],
            'assigned_name':    r['assigned_name']  or '',
            'assigned_to':      r['assigned_to'],
            'review_deadline':  r['review_deadline'] or '',
            'favorite':         bool(r['favorite_flag']),
            'overdue':          bool(r['overdue']),
        })

    return jsonify({
        'data':       data,
        'total':      total,
        'page':       page,
        'size':       size,
        'last_page':  max(1, -(-total // size)),  # целочисльное деление вверх
    })
