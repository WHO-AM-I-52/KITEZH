# ╔══════════════════════════════════════════════════════════════╗
# ║                     admin_filters.py                          ║
# ║  Сохранённые фильтры реестра обращений и их применение.       ║
# ║  Выделено из admin_routes.py (декомпозиция, refactor/structure).║
# ║  register(admin_bp) навешивает роуты на общий Blueprint admin, ║
# ║  поэтому endpoint-имена (admin.*) и url_for сохраняются.       ║
# ╚══════════════════════════════════════════════════════════════╝

import json

from flask import render_template, request, redirect, url_for, session, flash

from db import get_db
from core.auth_utils import login_required


def _build_filter_query(p):
    """Построить параметризованный COUNT-запрос по сохранённому фильтру.
    Возвращает (sql, params, has_like)."""
    q = "SELECT COUNT(*) FROM requests r WHERE 1=1"
    params = []
    has_like = False

    if p.get('status'):
        q += " AND r.status=?"
        params.append(p['status'])
    if p.get('date_from'):
        q += " AND r.request_date>=?"
        params.append(p['date_from'])
    if p.get('date_to'):
        q += " AND r.request_date<=?"
        params.append(p['date_to'])
    if p.get('employee'):
        q += " AND r.assigned_to=?"
        params.append(p['employee'])
    if p.get('site_type_free') == '1':
        q += " AND r.site_type_free=1"
    if p.get('site_type_existing') == '1':
        q += " AND r.site_type_existing=1"
    if p.get('area_min'):
        q += " AND r.site_area_ha>=?"
        params.append(float(p['area_min']))
    if p.get('area_max'):
        q += " AND r.site_area_ha<=?"
        params.append(float(p['area_max']))
    if p.get('build_min'):
        q += " AND r.site_build_area_m2>=?"
        params.append(float(p['build_min']))
    if p.get('build_max'):
        q += " AND r.site_build_area_m2<=?"
        params.append(float(p['build_max']))
    if p.get('inv_min'):
        q += " AND r.investment_total>=?"
        params.append(float(p['inv_min']))
    if p.get('inv_max'):
        q += " AND r.investment_total<=?"
        params.append(float(p['inv_max']))

    if p.get('applicant'):
        q += " AND (r.applicant_full_name LIKE ? OR r.applicant_short_name LIKE ?)"
        params += [f"%{p['applicant']}%"] * 2
        has_like = True
    if p.get('district'):
        q += " AND r.preferred_districts LIKE ?"
        params.append(f"%{p['district']}%")
        has_like = True

    return q, params, has_like


def register(admin_bp):
    """Навесить роуты сохранённых фильтров на admin_bp."""

    @admin_bp.route('/saved-filters', methods=['GET', 'POST'])
    @login_required
    def saved_filters():
        conn = get_db()
        try:
            if request.method == 'POST':
                action = request.form.get('action')

                def get_params():
                    return {
                        'status':             request.form.get('f_status', ''),
                        'date_from':          request.form.get('f_date_from', ''),
                        'date_to':            request.form.get('f_date_to', ''),
                        'applicant':          request.form.get('f_applicant', ''),
                        'employee':           request.form.get('f_employee', ''),
                        'period':             request.form.get('f_period', 'all'),
                        'site_type_free':     request.form.get('f_site_type_free', ''),
                        'site_type_existing': request.form.get('f_site_type_existing', ''),
                        'area_min':           request.form.get('f_area_min', ''),
                        'area_max':           request.form.get('f_area_max', ''),
                        'build_min':          request.form.get('f_build_min', ''),
                        'build_max':          request.form.get('f_build_max', ''),
                        'inv_min':            request.form.get('f_inv_min', ''),
                        'inv_max':            request.form.get('f_inv_max', ''),
                        'district':           request.form.get('f_district', ''),
                    }

                if action == 'add':
                    name = request.form.get('name', '').strip()
                    desc = request.form.get('description', '').strip()
                    if name:
                        conn.execute(
                            "INSERT INTO saved_filters (name,description,params,created_by) "
                            "VALUES (?,?,?,?)",
                            (name, desc, json.dumps(get_params(), ensure_ascii=False), session['user_id'])
                        )
                        conn.commit()
                        flash(f'Фильтр «{name}» сохранён', 'success')

                elif action == 'delete':
                    conn.execute(
                        "DELETE FROM saved_filters WHERE id=?",
                        (request.form.get('fid'),)
                    )
                    conn.commit()
                    flash('Фильтр удалён', 'success')

                elif action == 'edit':
                    fid  = request.form.get('fid')
                    name = request.form.get('name', '').strip()
                    desc = request.form.get('description', '').strip()
                    conn.execute(
                        "UPDATE saved_filters SET name=?,description=?,params=? WHERE id=?",
                        (name, desc, json.dumps(get_params(), ensure_ascii=False), fid)
                    )
                    conn.commit()
                    flash('Фильтр обновлён', 'success')

                return redirect(url_for('admin.saved_filters'))

            rows = conn.execute(
                "SELECT sf.*,u.full_name FROM saved_filters sf "
                "LEFT JOIN users u ON sf.created_by=u.id "
                "ORDER BY sf.sort_order,sf.id"
            ).fetchall()
            employees = conn.execute(
                    "SELECT id,full_name FROM users WHERE role IN ('employee','admin') "
                "ORDER BY full_name"
            ).fetchall()
            districts = [
                r['value'] for r in conn.execute(
                    "SELECT value FROM classifiers WHERE category='district' ORDER BY value"
                ).fetchall()
            ]

            parsed = {}
            for row in rows:
                try:
                    parsed[row['id']] = json.loads(row['params'])
                except Exception:
                    parsed[row['id']] = {}

            batch_ids  = []
            single_ids = []
            for row in rows:
                _, _, has_like = _build_filter_query(parsed[row['id']])
                if has_like:
                    single_ids.append(row['id'])
                else:
                    batch_ids.append(row['id'])

            counts = {}

            if batch_ids:
                union_parts  = []
                union_params = []
                for fid in batch_ids:
                    q, params, _ = _build_filter_query(parsed[fid])
                    q_with_fid = q.replace(
                        "SELECT COUNT(*) FROM requests r WHERE 1=1",
                        f"SELECT {fid} AS fid, COUNT(*) AS cnt FROM requests r WHERE 1=1",
                        1
                    )
                    union_parts.append(q_with_fid)
                    union_params.extend(params)
                union_sql = " UNION ALL ".join(union_parts)
                for batch_row in conn.execute(union_sql, union_params).fetchall():
                    counts[batch_row[0]] = batch_row[1]

            for fid in single_ids:
                q, params, _ = _build_filter_query(parsed[fid])
                try:
                    counts[fid] = conn.execute(q, params).fetchone()[0]
                except Exception:
                    counts[fid] = 0

            fwc = []
            for row in rows:
                fwc.append({
                    'row':    row,
                    'params': parsed[row['id']],
                    'count':  counts.get(row['id'], 0),
                    'qs':     '',
                })

            return render_template(
                'saved_filters.html',
                items=fwc, employees=employees, districts=districts
            )
        finally:
            conn.close()

    @admin_bp.route('/saved-filters/<int:fid>/apply')
    @login_required
    def apply_saved_filter(fid):
        from urllib.parse import urlencode
        conn = get_db()
        try:
            row = conn.execute("SELECT * FROM saved_filters WHERE id=?", (fid,)).fetchone()
        finally:
            conn.close()
        if not row:
            flash('Фильтр не найден', 'error')
            return redirect(url_for('requests.index'))
        try:
            p = json.loads(row['params'])
        except Exception:
            p = {}
        qs = {k: v for k, v in p.items() if v}
        qs['active_filter'] = fid
        return redirect(url_for('requests.index') + '?' + urlencode(qs))
