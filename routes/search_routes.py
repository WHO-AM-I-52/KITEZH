# ╔══════════════════════════════════════════════════════════════╗
# ║ search_routes.py                                             ║
# ║ GlobalSearch: быстрый API + полная страница результатов     ║
# ║ Фильтр по can_view_all: пользователь видит только свои      ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import Blueprint, request, jsonify, render_template, session
from db import get_db
from core.auth_utils import login_required

search_bp = Blueprint('search', __name__)

# ─── ПОЛЯ ПОИСКА ────────────────────────────────────────────────────────────
SEARCH_FIELDS = [
    {'col': 'request_number',      'label': '№ обращения'},
    {'col': 'applicant_full_name', 'label': 'Полн. наим.'},
    {'col': 'applicant_short_name','label': 'Краткое наим.'},
    {'col': 'applicant_inn',       'label': 'ИНН'},
    {'col': 'project_name',        'label': 'Проект'},
    {'col': 'contact_person',      'label': 'Контакт'},
    {'col': 'contact_phone',       'label': 'Телефон'},
    {'col': 'contact_email',       'label': 'E-mail'},
    {'col': 'additional_info',     'label': 'Доп. инфо'},
]

_MAX_DROPDOWN = 7
_MAX_PAGE     = 50

_SELECT_COLS = [f['col'] for f in SEARCH_FIELDS]


def _can_view_all() -> bool:
    """True если пользователь имеет право видеть все обращения."""
    return session.get('role') == 'admin' or bool(session.get('perm_can_view_all', 0))


def _scope():
    """Возвращает (clause, params) для ограничения видимости обращений.

    Если у пользователя нет права can_view_all — добавляет условие
    'только свои (created_by или assigned_to)'.
    """
    if _can_view_all():
        return '', []
    user_id = session.get('user_id')
    return ' AND (r.created_by=? OR r.assigned_to=?)', [user_id, user_id]


def _build_query(q: str, limit: int):
    """Строит SQL и params для поиска по всем SEARCH_FIELDS."""
    pattern = f'%{q}%'
    where_parts = ' OR '.join(f"r.{f['col']} LIKE ?" for f in SEARCH_FIELDS)
    params = [pattern] * len(SEARCH_FIELDS)

    extra_cols = ',\n            '.join(f'r.{c}' for c in _SELECT_COLS)

    scope_clause, scope_params = _scope()

    sql = f"""
        SELECT
            r.id,
            r.status,
            r.request_date,
            u.full_name  AS employee_name,
            {extra_cols}
        FROM requests r
        LEFT JOIN users u ON r.assigned_to = u.id
        WHERE ({where_parts}){scope_clause}
        ORDER BY r.id DESC
        LIMIT ?
    """
    params = params + scope_params + [limit]
    return sql, params


def _build_count_query(q: str):
    """Отдельный COUNT-запрос с тем же фильтром видимости."""
    pattern = f'%{q}%'
    where_parts = ' OR '.join(f"r.{f['col']} LIKE ?" for f in SEARCH_FIELDS)
    params = [pattern] * len(SEARCH_FIELDS)

    scope_clause, scope_params = _scope()

    sql = f"""
        SELECT COUNT(*) AS cnt
        FROM requests r
        WHERE ({where_parts}){scope_clause}
    """
    return sql, params + scope_params


# ─── API: быстрый поиск для дропдауна ────────────────────────────────────────
@search_bp.route('/api/search')
@login_required
def api_search():
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify({'results': []})

    conn = get_db()
    sql, params = _build_query(q, _MAX_DROPDOWN)
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    results = []
    for r in rows:
        row = dict(r)
        match_label = ''
        ql = q.lower()
        for f in SEARCH_FIELDS:
            val = row.get(f['col']) or ''
            if ql in val.lower():
                match_label = f['label']
                break

        results.append({
            'id':          row['id'],
            'number':      row.get('request_number') or f'ID {row["id"]}',
            'name':        row.get('applicant_short_name') or row.get('applicant_full_name') or '—',
            'inn':         row.get('applicant_inn') or '',
            'project':     row.get('project_name') or '',
            'status':      row.get('status') or '',
            'match_label': match_label,
            'url':         f'/request/{row["id"]}/view',
        })
    return jsonify({'results': results, 'q': q})


# ─── Страница полных результатов ───────────────────────────────────────────
@search_bp.route('/search')
@login_required
def search_page():
    q = request.args.get('q', '').strip()
    results = []
    total = 0

    if len(q) >= 2:
        conn = get_db()
        sql, params = _build_query(q, _MAX_PAGE)
        rows = conn.execute(sql, params).fetchall()

        count_sql, count_params = _build_count_query(q)
        total = conn.execute(count_sql, count_params).fetchone()['cnt']
        conn.close()
        results = [dict(r) for r in rows]

    return render_template('search.html', q=q, results=results,
                           total=total, limit=_MAX_PAGE)
