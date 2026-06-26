# ╔══════════════════════════════════════════════════════════════╗
# ║ api/requests_api.py                                           ║
# ║ GET  /api/requests          — JSON для Tabulator.js           ║
# ║ GET  /api/requests/distinct — уникальные значения по полю    ║
# ║ POST /api/request/<id>/favorite — тоггл избранного         ║
# ║ POST /api/check-duplicate   — проверка дублей (difflib)      ║
# ║                                                               ║
# ║ filter[status]      — один статус (обратная совместимость) ║
# ║ filter[status][]    — несколько статусов, IN (?,?,?)     ║
# ║ filter[overdue]=1   — просроченные по этапному review_deadline ║
# ║   Статусы-участники: все кроме draft и closed               ║
# ║   Условие: review_deadline < date('now')                    ║
# ║ filter[district]=Борский — фильтр по preferred_districts     ║
# ║                                                               ║
# ║ Ответ GET: { data:[], total, page, pages, stats:{} }    ║
# ╚══════════════════════════════════════════════════════════════╝

from datetime import date, timedelta
from difflib import SequenceMatcher
from flask import Blueprint, jsonify, request, session
from functools import wraps
from db import get_db

api_bp = Blueprint('api', __name__, url_prefix='/api')


def login_required_api(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            return jsonify({'error': 'unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


_ALLOWED_SORT = {
    'id':             'r.id',
    'number':         'r.request_number',
    'created_at':     'r.request_date',
    'status':         'r.status',
    'applicant':      'r.applicant_full_name',
    'project':        'r.project_name',
    'investment_mln': 'r.investment_total',
    'area_ha':        'r.site_area_ha_min',
    'area_m2':        'r.site_build_area_m2_min',
    'workplaces':     'r.jobs_total',
    'employee_name':  'u.full_name',
    'source':         'r.source_type',
    'review_deadline':'r.review_deadline',
}

# Поля, по которым разрешено получать уникальные значения
_DISTINCT_FIELDS = {
    'created_at':     'r.request_date',
    'status':         'r.status',
    'source':         'r.source_type',
    'applicant':      "COALESCE(r.applicant_short_name, r.applicant_full_name)",
    'project':        'r.project_name',
    'investment_mln': 'r.investment_total',
    'area_ha':        'r.site_area_ha_min',
    'area_m2':        'r.site_build_area_m2_min',
    'workplaces':     'r.jobs_total',
    'employee_name':  'u.full_name',
    'number':         'r.request_number',
}


def _apply_filter(where, params, col, value, ftype):
    if ftype == 'empty':
        where.append(f"({col} IS NULL OR {col} = '')")
    elif ftype == '=':
        where.append(f"{col} = ?")
        params.append(value)
    elif ftype == 'starts':
        where.append(f"{col} LIKE ?")
        params.append(value + '%')
    elif ftype == 'ends':
        where.append(f"{col} LIKE ?")
        params.append('%' + value)
    else:
        where.append(f"{col} LIKE ?")
        params.append('%' + value + '%')


def _date_range(chip):
    today = date.today()
    if chip == 'today':
        return str(today), str(today)
    if chip == 'week':
        monday = today - timedelta(days=today.weekday())
        return str(monday), str(today)
    if chip == 'month':
        return str(today.replace(day=1)), str(today)
    return None, None


# ─── УСЛОВИЕ ПРОСРОЧКИ ──────────────────────────────────────────────────────
_OVERDUE_SQL = (
    "r.status NOT IN ('closed','draft') "
    "AND r.review_deadline IS NOT NULL AND r.review_deadline != '' "
    "AND r.review_deadline < date('now')"
)


@api_bp.route('/requests/distinct')
@login_required_api
def get_distinct_values():
    """Возвращает уникальные непустые значения по полю.
    GET /api/requests/distinct?field=status
    Ответ: { field: 'status', values: ['draft', 'registered', ...] }
    """
    field = request.args.get('field', '').strip()
    if field not in _DISTINCT_FIELDS:
        return jsonify({'error': 'unknown field'}), 400

    db   = get_db()
    uid  = session['user_id']
    role = session.get('role', '')

    col = _DISTINCT_FIELDS[field]

    where  = [f"{col} IS NOT NULL", f"{col} != ''"]
    params = []

    if role != 'admin' and not session.get('perm_can_view_all'):
        where.append('r.created_by = ?')
        params.append(uid)

    where_sql = 'WHERE ' + ' AND '.join(where)

    rows = db.execute(
        f"""
        SELECT DISTINCT {col} AS val
        FROM requests r
        LEFT JOIN users u ON u.id = r.assigned_to
        {where_sql}
        ORDER BY {col} ASC
        LIMIT 300
        """,
        params
    ).fetchall()
    db.close()

    values = [r['val'] for r in rows if r['val']]
    return jsonify({'field': field, 'values': values})


@api_bp.route('/requests')
@login_required_api
def get_requests():
    db   = get_db()
    uid  = session['user_id']
    role = session.get('role', '')

    try:
        page = max(1, int(request.args.get('page', 1)))
        size = min(200, max(1, int(request.args.get('size', 50))))
    except (ValueError, TypeError):
        page, size = 1, 50
    offset = (page - 1) * size

    raw_sort = request.args.get('sort', 'created_at')
    sort_col = _ALLOWED_SORT.get(raw_sort, 'r.request_date')
    sort_dir = 'asc' if request.args.get('dir', 'desc').lower() == 'asc' else 'desc'

    where  = []
    params = []

    if role != 'admin' and not session.get('perm_can_view_all'):
        where.append('r.created_by = ?')
        params.append(uid)

    # ── МУЛЬТИ-ФИЛЬТР ПО СТАТУСУ ──────────────────────────────────────
    statuses = [s.strip() for s in request.args.getlist('filter[status][]') if s.strip()]
    if not statuses:
        single = request.args.get('filter[status]', '').strip()
        if single:
            statuses = [single]

    if len(statuses) == 1:
        where.append('r.status = ?')
        params.append(statuses[0])
    elif len(statuses) > 1:
        placeholders = ','.join('?' * len(statuses))
        where.append(f'r.status IN ({placeholders})')
        params.extend(statuses)

    applicant = request.args.get('filter[applicant]', '').strip()
    if applicant:
        ftype = request.args.get('filter_type[applicant]', 'like')
        _apply_filter(where, params, 'r.applicant_full_name', applicant, ftype)

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

    district = request.args.get('filter[district]', '').strip()
    if district:
        where.append('r.preferred_districts LIKE ?')
        params.append('%' + district + '%')

    employee = request.args.get('filter[employee]', '').strip()
    if employee:
        if employee.isdigit():
            where.append('r.assigned_to = ?')
            params.append(int(employee))
        else:
            where.append('u.full_name LIKE ?')
            params.append('%' + employee + '%')

    if request.args.get('filter[favorite]', '').strip() == '1':
        where.append(
            'EXISTS (SELECT 1 FROM favorites fv '
            'WHERE fv.request_id = r.id AND fv.user_id = ?)'
        )
        params.append(uid)

    date_chip = request.args.get('filter[date_chip]', '').strip()
    date_from = request.args.get('filter[date_from]', '').strip()
    date_to   = request.args.get('filter[date_to]',   '').strip()
    if date_chip:
        date_from, date_to = _date_range(date_chip)
    if date_from:
        where.append('r.request_date >= ?')
        params.append(date_from)
    if date_to:
        where.append('r.request_date <= ?')
        params.append(date_to)

    if request.args.get('filter[overdue]', '').strip() == '1':
        where.append(_OVERDUE_SQL)

    # ── ФИЛЬТРЫ ПО СТОЛБЦАМ (воронка) ─────────────────────────────────
    # Передаются как filter[col][]=val1&filter[col][]=val2 (мульти)
    for field, col_expr in {
        'col_created_at':     'r.request_date',
        'col_source':         'r.source_type',
        'col_applicant':      "COALESCE(r.applicant_short_name, r.applicant_full_name)",
        'col_project':        'r.project_name',
        'col_investment_mln': 'r.investment_total',
        'col_area_ha':        'r.site_area_ha_min',
        'col_area_m2':        'r.site_build_area_m2_min',
        'col_workplaces':     'r.jobs_total',
        'col_employee_name':  'u.full_name',
        'col_number':         'r.request_number',
    }.items():
        vals = [v.strip() for v in request.args.getlist(f'filter[{field}][]') if v.strip()]
        if vals:
            placeholders = ','.join('?' * len(vals))
            where.append(f"{col_expr} IN ({placeholders})")
            params.extend(vals)

    # col_status — отдельно, чтобы не конфликтовать с основным фильтром статуса
    col_statuses = [v.strip() for v in request.args.getlist('filter[col_status][]') if v.strip()]
    if col_statuses and not statuses:  # применяем только если нет глобального фильтра
        if len(col_statuses) == 1:
            where.append('r.status = ?')
            params.append(col_statuses[0])
        else:
            placeholders = ','.join('?' * len(col_statuses))
            where.append(f'r.status IN ({placeholders})')
            params.extend(col_statuses)

    where_sql = ('WHERE ' + ' AND '.join(where)) if where else ''

    base_query = f"""
        SELECT
            r.id,
            r.request_number          AS number,
            r.request_date            AS created_at,
            r.status,
            r.source_type             AS source,
            COALESCE(r.applicant_short_name,
                     r.applicant_full_name)  AS applicant,
            r.project_name            AS project,
            r.investment_total        AS investment_mln,
            r.site_area_ha_min        AS area_ha,
            r.site_build_area_m2_min  AS area_m2,
            r.jobs_total              AS workplaces,
            u.full_name               AS employee_name,
            r.assigned_to             AS employee_id,
            r.review_deadline,
            r.created_by,
            CASE WHEN fav.id IS NOT NULL THEN 1 ELSE 0 END  AS favorite,
            CASE WHEN {_OVERDUE_SQL} THEN 1 ELSE 0 END      AS overdue
        FROM requests r
        LEFT JOIN users     u   ON u.id  = r.assigned_to
        LEFT JOIN favorites fav ON fav.request_id = r.id
                                AND fav.user_id   = ?
        {where_sql}
    """

    count_query = f"""
        SELECT COUNT(*)
        FROM requests r
        LEFT JOIN users u ON u.id = r.assigned_to
        {where_sql}
    """

    total = db.execute(count_query, params).fetchone()[0]
    pages = max(1, -(-total // size))

    rows = db.execute(
        base_query + f' ORDER BY {sort_col} {sort_dir} NULLS LAST LIMIT ? OFFSET ?',
        [uid] + params + [size, offset]
    ).fetchall()

    stats_where  = ''
    stats_params = []
    if role != 'admin' and not session.get('perm_can_view_all'):
        stats_where  = 'WHERE r.created_by = ?'
        stats_params = [uid]

    stats_rows = db.execute(f"""
        SELECT
            COUNT(*)                                                                  AS total,
            SUM(CASE WHEN r.status='draft'             THEN 1 ELSE 0 END)            AS draft,
            SUM(CASE WHEN r.status='registered'        THEN 1 ELSE 0 END)            AS registered,
            SUM(CASE WHEN r.status='in_progress'       THEN 1 ELSE 0 END)            AS in_progress,
            SUM(CASE WHEN r.status='under_review'      THEN 1 ELSE 0 END)            AS under_review,
            SUM(CASE WHEN r.status='ready_to_send'     THEN 1 ELSE 0 END)            AS ready_to_send,
            SUM(CASE WHEN r.status='sent_to_applicant' THEN 1 ELSE 0 END)            AS sent_to_applicant,
            SUM(CASE WHEN r.status='closed'            THEN 1 ELSE 0 END)            AS closed,
            SUM(CASE WHEN {_OVERDUE_SQL} THEN 1 ELSE 0 END)                          AS overdue
        FROM requests r
        {stats_where}
    """, stats_params).fetchone()

    stats = {
        'all':               stats_rows['total']             or 0,
        'draft':             stats_rows['draft']             or 0,
        'registered':        stats_rows['registered']        or 0,
        'in_progress':       stats_rows['in_progress']       or 0,
        'under_review':      stats_rows['under_review']      or 0,
        'ready_to_send':     stats_rows['ready_to_send']     or 0,
        'sent_to_applicant': stats_rows['sent_to_applicant'] or 0,
        'closed':            stats_rows['closed']            or 0,
        'overdue':           stats_rows['overdue']           or 0,
    }

    db.close()

    data = [
        {
            'id':             r['id'],
            'number':         r['number']         or '',
            'created_at':     r['created_at']     or '',
            'status':         r['status']         or '',
            'source':         r['source']         or '',
            'applicant':      r['applicant']      or '',
            'project':        r['project']        or '',
            'investment_mln': r['investment_mln'],
            'area_ha':        r['area_ha'],
            'area_m2':        r['area_m2'],
            'workplaces':     r['workplaces'],
            'employee_name':  r['employee_name']  or '',
            'employee_id':    r['employee_id'],
            'review_deadline':r['review_deadline'] or '',
            'favorite':       bool(r['favorite']),
            'overdue':        bool(r['overdue']),
        }
        for r in rows
    ]

    return jsonify({
        'data':  data,
        'total': total,
        'page':  page,
        'size':  size,
        'pages': pages,
        'stats': stats,
    })


@api_bp.route('/request/<int:request_id>/favorite', methods=['POST'])
@login_required_api
def toggle_favorite(request_id):
    uid = session['user_id']
    db  = get_db()

    existing = db.execute(
        'SELECT id FROM favorites WHERE request_id = ? AND user_id = ?',
        (request_id, uid)
    ).fetchone()

    if existing:
        db.execute(
            'DELETE FROM favorites WHERE request_id = ? AND user_id = ?',
            (request_id, uid)
        )
        is_fav = False
    else:
        db.execute(
            'INSERT INTO favorites (request_id, user_id) VALUES (?, ?)',
            (request_id, uid)
        )
        is_fav = True

    db.commit()
    db.close()
    return jsonify({'favorite': is_fav})


@api_bp.route('/check-duplicate', methods=['POST'])
@login_required_api
def check_duplicate():
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip().lower()
    inn  = (data.get('inn')  or '').strip()

    if not name and not inn:
        return jsonify({'duplicates': []})

    db = get_db()

    if inn:
        rows = db.execute(
            "SELECT id, applicant_short_name, applicant_inn "
            "FROM requests WHERE applicant_inn = ? LIMIT 5",
            (inn,)
        ).fetchall()
        if rows:
            db.close()
            return jsonify({'duplicates': [dict(r) for r in rows], 'method': 'inn'})

    if not name:
        db.close()
        return jsonify({'duplicates': []})

    prefix = name[:3]
    candidates = db.execute(
        "SELECT id, applicant_short_name, applicant_inn "
        "FROM requests WHERE lower(applicant_short_name) LIKE ? LIMIT 200",
        (prefix + '%',)
    ).fetchall()
    db.close()

    hits = []
    for row in candidates:
        candidate = (row['applicant_short_name'] or '').lower()
        if not candidate:
            continue
        score = SequenceMatcher(None, name, candidate).ratio()
        if score >= 0.75:
            hits.append({
                'id':    row['id'],
                'name':  row['applicant_short_name'],
                'inn':   row['applicant_inn'],
                'score': round(score, 2),
            })

    hits.sort(key=lambda x: x['score'], reverse=True)
    return jsonify({'duplicates': hits[:5], 'method': 'fuzzy'})
