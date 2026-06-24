from datetime import date
from flask import render_template, redirect, url_for, session, flash

from db import get_db
from core.auth_utils import login_required, admin_required
from core.request_history import get_history, rollback_history
from core.activity_log import log_action
from utils.form_utils import denormalize_from_base, FIELD_UNIT_KEY
from . import requests_bp

# ─── Issue #48: поля, которые денормализуются в карточке ──────────────────
# Формат: (поле_значения, поле_unit, ключ_в_UNIT_FACTORS)
_INFRA_DISPLAY_FIELDS = [
    ('water_household',   'water_unit',  'water_unit'),
    ('water_production',  'water_unit',  'water_unit'),
    ('sewage',            'water_unit',  'water_unit'),
    ('firefighting',      'water_unit',  'water_unit'),
    ('electricity_total', 'elec_unit',   'elec_unit'),
    ('electricity_cat1',  'elec_unit',   'elec_unit'),
    ('electricity_cat2',  'elec_unit',   'elec_unit'),
    ('electricity_cat3',  'elec_unit',   'elec_unit'),
    ('heat_gcal',         'heat_unit',   'heat_unit'),
    ('gas_m3h',           'gas_unit_h',  'gas_unit_h'),
    ('gas_m3y',           'gas_unit_y',  'gas_unit_y'),
]

_UNIT_DEFAULTS = {
    'water_unit':  'м³/сут',
    'elec_unit':   'кВт',
    'heat_unit':   'Гкал/ч',
    'gas_unit_h':  'м³/ч',
    'gas_unit_y':  'м³/год',
}


def _build_display_vals(req):
    """
    Возвращает dict с денормализованными значениями инфра-полей
    в тех единицах, которые выбрал пользователь при вводе.
    Используется только для отображения в view.html.
    """
    dv = {}
    for field, unit_field, unit_key in _INFRA_DISPLAY_FIELDS:
        raw  = req[field]
        unit = req[unit_field] if req[unit_field] else _UNIT_DEFAULTS[unit_field]
        dv[field] = denormalize_from_base(raw, unit_key, unit)
    return dv


@requests_bp.route('/view/<int:rid>')
@login_required
def view_request(rid):
    conn = get_db()
    req  = conn.execute(
        "SELECT r.*, u.full_name AS employee_name, ass.full_name AS assigned_name, "
        "adm.full_name AS admin_name, upd.full_name AS updated_by_name, "
        "resp.full_name AS responsible_name, rev.full_name AS reviewer_name, "
        "st.name AS subject_type_name, rt.name AS result_type_name, rt.color_hex AS result_color "
        "FROM requests r "
        "LEFT JOIN users u    ON r.created_by      = u.id "
        "LEFT JOIN users ass  ON r.assigned_to     = ass.id "
        "LEFT JOIN users adm  ON r.confirmed_by    = adm.id "
        "LEFT JOIN users upd  ON r.updated_by      = upd.id "
        "LEFT JOIN users resp ON r.responsible_id  = resp.id "
        "LEFT JOIN users rev  ON r.reviewer_id     = rev.id "
        "LEFT JOIN subject_types st ON r.subject_type_id = st.id "
        "LEFT JOIN result_types  rt ON r.result_type_id  = rt.id "
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

    employees = conn.execute(
        "SELECT id,full_name FROM users WHERE role IN ('employee','admin','manager') "
        "ORDER BY full_name"
    ).fetchall()

    result_types = conn.execute(
        "SELECT id, name, color_hex FROM result_types ORDER BY id"
    ).fetchall()

    all_users = conn.execute(
        "SELECT id, full_name, role FROM users WHERE is_active=1 ORDER BY full_name"
    ).fetchall()

    # ─ Все районы для блока «Предпочтительные районы»
    all_districts = conn.execute(
        "SELECT id, name FROM districts WHERE is_active=1 ORDER BY name"
    ).fetchall()

    # ─ Цепочка согласования для текущего обращения
    review_chain = conn.execute(
        "SELECT rc.id, rc.step_order, rc.decision, rc.comment, rc.decided_at, "
        "rc.user_id, rc.external_name, u.full_name AS reviewer_full_name "
        "FROM review_chain rc "
        "LEFT JOIN users u ON rc.user_id = u.id "
        "WHERE rc.request_id = ? "
        "ORDER BY rc.step_order",
        (rid,)
    ).fetchall()

    # ─ Соисполнители (#77)
    coexecutors = conn.execute(
        "SELECT ce.user_id, u.full_name, ce.assigned_at, "
        "ab.full_name AS assigned_by_name "
        "FROM request_coexecutors ce "
        "JOIN users u  ON u.id  = ce.user_id "
        "JOIN users ab ON ab.id = ce.assigned_by "
        "WHERE ce.request_id = ? "
        "ORDER BY ce.assigned_at",
        (rid,)
    ).fetchall()

    conn.close()

    display_vals = _build_display_vals(req)

    return render_template(
        'view.html',
        req=req,
        employees=employees,
        okved_name=okved_name,
        display_vals=display_vals,
        result_types=result_types,
        all_users=all_users,
        all_districts=all_districts,
        review_chain=review_chain,
        coexecutors=coexecutors,
        today_str=str(date.today()),
    )


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
    try:
        ok = rollback_history(hid, rid)
        if ok:
            log_action(conn, session['user_id'], 'rollback', rid,
                       f'Откат к версии history_id={hid}')
            conn.commit()
            flash('Обращение откачено к выбранной версии', 'success')
        else:
            flash('Не удалось выполнить откат — запись не найдена', 'error')
    finally:
        conn.close()
    return redirect(url_for('requests.view_request', rid=rid))
