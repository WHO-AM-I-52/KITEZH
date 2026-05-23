# ╔══════════════════════════════════════════════════════════════╗
# ║                      admin_routes.py                         ║
# ║  v2.0: гибкие права, журнал входов                          ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from datetime import datetime
import json

from db import get_db
from auth_utils import login_required, admin_required, hash_pw, ALL_PERMISSIONS

admin_bp = Blueprint('admin', __name__)


# ─── СПРАВОЧНИКИ ──────────────────────────────────────────────────────────────

@admin_bp.route('/admin/classifiers', methods=['GET', 'POST'])
@login_required
@admin_required
def classifiers():
    conn = get_db()

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add':
            cat = request.form.get('category', '')
            val = request.form.get('value', '').strip()
            if cat and val:
                conn.execute(
                    "INSERT INTO classifiers (category,value) VALUES (?,?)",
                    (cat, val)
                )
                conn.commit()
                flash('Значение добавлено', 'success')

        elif action == 'delete':
            cid = request.form.get('cid')
            conn.execute("DELETE FROM classifiers WHERE id=?", (cid,))
            conn.commit()
            flash('Значение удалено', 'success')

        elif action == 'rename':
            cid = request.form.get('cid')
            val = request.form.get('value', '').strip()
            if val:
                conn.execute("UPDATE classifiers SET value=? WHERE id=?", (val, cid))
                conn.commit()
                flash('Значение обновлено', 'success')

    lf  = conn.execute(
        "SELECT * FROM classifiers WHERE category='legal_form'  ORDER BY sort_order,value"
    ).fetchall()
    di  = conn.execute(
        "SELECT * FROM classifiers WHERE category='district'     ORDER BY sort_order,value"
    ).fetchall()
    src = conn.execute(
        "SELECT * FROM classifiers WHERE category='source_type'  ORDER BY sort_order,value"
    ).fetchall()

    okved_total = conn.execute("SELECT COUNT(*) FROM okved").fetchone()[0]
    row = conn.execute("SELECT value FROM settings WHERE key='okved_last_sync'").fetchone()
    okved_last_sync = row['value'] if row else '—'

    conn.close()
    return render_template(
        'classifiers.html',
        legal_forms=lf, districts=di, source_types=src,
        okved_total=okved_total, okved_last_sync=okved_last_sync,
    )


# ─── УПРАВЛЕНИЕ ПОЛЬЗОВАТЕЛЯМИ ────────────────────────────────────────────────

@admin_bp.route('/admin/users', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_users():
    conn = get_db()

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add':
            un  = request.form.get('username', '').strip()
            pw2 = request.form.get('password', '').strip()
            fn  = request.form.get('full_name', '').strip()
            ro  = request.form.get('role', 'employee')
            mcp = 1 if request.form.get('must_change_password') else 0

            if un and pw2 and fn:
                perms = {k: (1 if request.form.get(k) else 0) for k in ALL_PERMISSIONS}
                # admin всегда получает все права
                if ro == 'admin':
                    perms = {k: 1 for k in ALL_PERMISSIONS}
                try:
                    conn.execute(
                        f"INSERT INTO users "
                        f"(username,password,full_name,role,must_change_password,"
                        f"{','.join(ALL_PERMISSIONS)}) "
                        f"VALUES (?,?,?,?,?,{','.join(['?']*len(ALL_PERMISSIONS))})",
                        [un, hash_pw(pw2), fn, ro, mcp] + [perms[k] for k in ALL_PERMISSIONS]
                    )
                    conn.commit()
                    flash(f'Пользователь {un} добавлен', 'success')
                except Exception:
                    flash('Логин уже занят', 'error')

        elif action == 'edit_permissions':
            uid = request.form.get('user_id')
            ro  = request.form.get('role', 'employee')
            perms = {k: (1 if request.form.get(k) else 0) for k in ALL_PERMISSIONS}
            if ro == 'admin':
                perms = {k: 1 for k in ALL_PERMISSIONS}
            sets = ', '.join([f"{k}=?" for k in ALL_PERMISSIONS])
            conn.execute(
                f"UPDATE users SET role=?, {sets} WHERE id=?",
                [ro] + [perms[k] for k in ALL_PERMISSIONS] + [uid]
            )
            conn.commit()
            flash('Права обновлены', 'success')

        elif action == 'delete':
            uid = request.form.get('user_id')
            if str(uid) != str(session['user_id']):
                conn.execute("DELETE FROM users WHERE id=?", (uid,))
                conn.commit()
                flash('Пользователь удалён', 'success')
            else:
                flash('Нельзя удалить себя', 'error')

        elif action == 'change_password':
            uid = request.form.get('user_id')
            np2 = request.form.get('new_password', '').strip()
            mcp = 1 if request.form.get('must_change_password') else 0
            if np2:
                conn.execute(
                    "UPDATE users SET password=?, must_change_password=? WHERE id=?",
                    (hash_pw(np2), mcp, uid)
                )
                conn.commit()
                flash('Пароль изменён', 'success')

    users = conn.execute(
        "SELECT * FROM users ORDER BY role, full_name"
    ).fetchall()

    login_log = conn.execute(
        "SELECT * FROM login_log ORDER BY id DESC LIMIT 50"
    ).fetchall()

    # ─── Фильтры журнала действий (v2.0.1) ───────────────────────
    af_user   = request.args.get('af_user', '')
    af_action = request.args.get('af_action', '')
    af_date   = request.args.get('af_date', '')

    from activity_log import get_activity_log, ACTION_LABELS
    activity = get_activity_log(
        limit=200,
        user_id=int(af_user) if af_user else None,
        action=af_action or None,
        date_from=af_date or None,
    )

    conn.close()
    return render_template(
        'users.html',
        users=users,
        login_log=login_log,
        activity=activity,
        action_labels=ACTION_LABELS,
        af_user=af_user,
        af_action=af_action,
        af_date=af_date,
    )


# ─── СОХРАНЁННЫЕ ФИЛЬТРЫ ──────────────────────────────────────────────────────

@admin_bp.route('/saved-filters', methods=['GET', 'POST'])
@login_required
def saved_filters():
    conn = get_db()

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

        conn.close()
        return redirect(url_for('admin.saved_filters'))

    rows = conn.execute(
        "SELECT sf.*,u.full_name FROM saved_filters sf "
        "LEFT JOIN users u ON sf.created_by=u.id "
        "ORDER BY sf.sort_order,sf.id"
    ).fetchall()
    employees = conn.execute(
        "SELECT id,full_name FROM users WHERE role IN ('employee','admin','manager') "
        "ORDER BY full_name"
    ).fetchall()
    districts = [
        r['value'] for r in conn.execute(
            "SELECT value FROM classifiers WHERE category='district' ORDER BY value"
        ).fetchall()
    ]

    fwc = []
    for row in rows:
        try:
            p = json.loads(row['params'])
        except Exception:
            p = {}

        q2 = "SELECT COUNT(*) FROM requests r WHERE 1=1"
        p2 = []
        if p.get('status'):
            q2 += " AND r.status=?"; p2.append(p['status'])
        if p.get('date_from'):
            q2 += " AND r.request_date>=?"; p2.append(p['date_from'])
        if p.get('date_to'):
            q2 += " AND r.request_date<=?"; p2.append(p['date_to'])
        if p.get('applicant'):
            q2 += " AND (r.applicant_full_name LIKE ? OR r.applicant_short_name LIKE ?)"
            p2 += [f"%{p['applicant']}%"] * 2
        if p.get('employee'):
            q2 += " AND r.assigned_to=?"; p2.append(p['employee'])
        if p.get('site_type_free') == '1':
            q2 += " AND r.site_type_free=1"
        if p.get('site_type_existing') == '1':
            q2 += " AND r.site_type_existing=1"
        if p.get('area_min'):
            q2 += " AND r.site_area_ha_min>=?"; p2.append(float(p['area_min']))
        if p.get('area_max'):
            q2 += " AND r.site_area_ha_min<=?"; p2.append(float(p['area_max']))
        if p.get('build_min'):
            q2 += " AND r.site_build_area_m2_min>=?"; p2.append(float(p['build_min']))
        if p.get('build_max'):
            q2 += " AND r.site_build_area_m2_min<=?"; p2.append(float(p['build_max']))
        if p.get('inv_min'):
            q2 += " AND r.investment_total>=?"; p2.append(float(p['inv_min']))
        if p.get('inv_max'):
            q2 += " AND r.investment_total<=?"; p2.append(float(p['inv_max']))
        if p.get('district'):
            q2 += " AND r.preferred_districts LIKE ?"; p2.append(f"%{p['district']}%")

        cnt = conn.execute(q2, p2).fetchone()[0]
        fwc.append({'row': row, 'params': p, 'count': cnt, 'qs': ''})

    conn.close()
    return render_template(
        'saved_filters.html',
        items=fwc, employees=employees, districts=districts
    )


@admin_bp.route('/saved-filters/<int:fid>/apply')
@login_required
def apply_saved_filter(fid):
    from urllib.parse import urlencode
    conn = get_db()
    row  = conn.execute("SELECT * FROM saved_filters WHERE id=?", (fid,)).fetchone()
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