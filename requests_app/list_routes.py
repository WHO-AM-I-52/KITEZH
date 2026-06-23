# ╔══════════════════════════════════════════════════════════════╗
# ║ requests_app/list_routes.py                                   ║
# ║ GET /requests       — классический список (старый index.html)  ║
# ║ GET /requests/table — Tabulator-версия (новая)              ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import render_template, redirect, url_for, session, request
from . import requests_bp
from db import get_db
from core.auth_utils import login_required


# ─── /requests — классический список (остаётся для обратной совместимости) ───────────
@requests_bp.route('/requests')
@login_required
def requests_list():
    db   = get_db()
    role = session.get('role', '')
    uid  = session.get('user_id')

    # Список ответственных для чипов (передаётся в шаблон)
    employees = db.execute(
        'SELECT id, full_name FROM users WHERE is_active = 1 ORDER BY full_name'
    ).fetchall()

    # Члены для чипов short_name: если есть фамилия_ии — используем фамилию + инициалы
    def short(row):
        parts = row['full_name'].split()
        if len(parts) >= 2:
            return parts[0] + ' ' + ' '.join(p[0] + '.' for p in parts[1:])
        return row['full_name']

    emp_list = [{'id': e['id'], 'short_name': short(e), 'full_name': e['full_name']}
                for e in employees]

    db.close()
    return render_template(
        'requests_tabulator.html',
        employees=emp_list,
    )


# ─── /requests/table — тот же Tabulator-вид (альтернативный URL) ───────────────
@requests_bp.route('/requests/table')
@login_required
def requests_table():
    return redirect(url_for('requests.requests_list'), 301)
