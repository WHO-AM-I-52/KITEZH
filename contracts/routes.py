# ╔════════════════════════════════════════════════════════════════════════╗
# ║                          routes.py (contracts)                           ║
# ║  HTTP-маршруты раздела «Договоры»: реестр, карточка, переходы,         ║
# ║  услуги, файлы, оплаты, DOCX и API подсказки.                          ║
# ║  Права: can_view_contracts / can_view_all_contracts /                  ║
# ║  can_edit_contracts / can_manage_contracts / can_contract_payments.    ║
# ╚══════════════════════════════════════════════════════════════════════╝

import os

from datetime import datetime

from flask import (
    Blueprint, render_template, request, redirect, url_for, session,
    jsonify, g, flash, send_file, abort,
)

from db import get_db
from core.activity_log import log_action
from core.auth_utils import get_user_perm, login_required
from services import contact_directory

from contracts import documents, repository, workflow
from contracts import pricing
from contracts.workdays import fmt as wd_fmt
from . import contracts_bp


# ─── СОЕДИНЕНИЕ НА ВРЕМЯ ЗАПРОСА ────────────────────────────────────────────

def _db():
    conn = g.get('_contracts_db')
    if conn is None:
        conn = get_db()
        g._contracts_db = conn
    return conn


@contracts_bp.teardown_request
def _close_db(exc):
    conn = g.pop('_contracts_db', None)
    if conn is None:
        return
    try:
        if conn.in_transaction:
            conn.rollback()
    finally:
        conn.close()


# ─── ПРАВА И ДОСТУП ─────────────────────────────────────────────────────────

def _perm(key):
    return get_user_perm(key)


def _can_view(contract=None):
    """Доступ к договору: раздел + (свой договор или общий доступ)."""
    if not _perm('can_view_contracts'):
        return False
    if contract is None:
        return True
    if _perm('can_view_all_contracts'):
        return True
    uid = session.get('user_id')
    return contract['executor_id'] == uid or contract['created_by'] == uid


def _can_edit(contract):
    """Редактирование: право + (свой договор или управление разделом)."""
    if not _perm('can_edit_contracts'):
        return False
    if _perm('can_manage_contracts'):
        return True
    uid = session.get('user_id')
    return contract['executor_id'] == uid or contract['created_by'] == uid


def _get_or_404(contract_id):
    contract = repository.get_contract(_db(), contract_id)
    if contract is None:
        abort(404)
    if not _can_view(contract):
        abort(403)
    return contract


def _rub_to_kop(value):
    from decimal import Decimal, InvalidOperation
    try:
        return pricing._to_kop(str(value).replace(',', '.'))
    except (TypeError, ValueError, InvalidOperation):
        return None


# ─── РЕЕСТР ────────────────────────────────────────────────────────────────

@contracts_bp.route('/')
@login_required
def list_contracts():
    if not _perm('can_view_contracts'):
        abort(403)
    filters = {k: request.args.get(k, '').strip()
               for k in ('stage', 'state', 'free', 'executor', 'year', 'q')}
    contracts = repository.list_contracts(
        _db(), _perm('can_view_all_contracts'), session['user_id'], filters
    )
    executors = repository.list_executors(_db())
    return render_template(
        'contracts/list.html',
        contracts=contracts, filters=filters, executors=executors,
        stage_labels=workflow.STAGE_LABELS, stages=workflow.STAGES,
        can_edit=_perm('can_edit_contracts'),
        can_manage=_perm('can_manage_contracts'),
        view_all=_perm('can_view_all_contracts'),
        rub=pricing.rub,
    )


@contracts_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_contract():
    if not _perm('can_edit_contracts'):
        abort(403)
    if request.method == 'POST':
        form = {f: request.form.get(f) for f in repository.CONTRACT_FIELDS}
        conn = _db()
        try:
            contract_id = repository.insert_contract(
                conn, form, session['user_id']
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        log_action(conn, session['user_id'], 'contract_create',
                  detail=f'Создан договор #{contract_id}')
        flash('Договор создан', 'success')
        return redirect(url_for('contracts.detail', contract_id=contract_id))
    return _render_form(None)


@contracts_bp.route('/<int:contract_id>')
@login_required
def detail(contract_id):
    contract = _get_or_404(contract_id)
    conn = _db()
    services = repository.get_services(conn, contract_id)
    files = repository.get_files(conn, contract_id)
    payments = repository.get_payments(conn, contract_id)
    reviews = repository.get_reviews(conn, contract_id)
    stage_log = repository.get_stage_log(conn, contract_id)
    activities = repository.get_activities(conn, contract_id)
    paid = sum(p['amount_kop'] for p in payments if p['kind'] == 'income')
    return render_template(
        'contracts/detail.html',
        c=contract, services=services, files=files, payments=payments,
        reviews=reviews, stage_log=stage_log, activities=activities,
        paid_kop=paid,
        tariffs=repository.get_tariff_items(conn),
        actions=workflow.next_actions(contract),
        stages=workflow.route_of(contract),
        stage_labels=workflow.STAGE_LABELS,
        today=datetime.utcnow().date().isoformat(),
        category_labels=documents.CATEGORY_LABELS,
        vat=pricing.vat_from_total(
            contract['total_kop'] or 0, contract['vat_percent'] or '22'
        ) if not contract['is_free'] else 0,
        can_edit=_can_edit(contract),
        can_manage=_perm('can_manage_contracts'),
        can_payments=_perm('can_contract_payments'),
        rub=pricing.rub, rub_words=pricing.rub_in_words,
        wd_fmt=wd_fmt, state_labels={
            'active': 'Действует', 'paused': 'Приостановлен',
            'cancelled': 'Отменён', 'terminated': 'Расторгнут',
            'completed': 'Завершён',
        },
    )


@contracts_bp.route('/<int:contract_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_contract(contract_id):
    contract = _get_or_404(contract_id)
    if not _can_edit(contract):
        abort(403)
    if request.method == 'POST':
        form = {f: request.form.get(f) for f in repository.CONTRACT_FIELDS}
        conn = _db()
        try:
            repository.update_contract(conn, contract_id, form)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        log_action(conn, session['user_id'], 'contract_edit',
                   detail=f'Изменён договор #{contract_id}')
        flash('Изменения сохранены', 'success')
        return redirect(url_for('contracts.detail', contract_id=contract_id))
    return _render_form(contract)


def _render_form(contract):
    conn = _db()
    tariffs = repository.get_tariff_items(conn)
    return render_template(
        'contracts/form.html',
        c=contract,
        tariffs=tariffs,
        object_types=repository.get_object_types(conn),
        executors=repository.list_executors(conn),
        districts=repository.list_districts(conn),
        rub=pricing.rub,
        is_new=contract is None,
    )


# ─── ПЕРЕХОДЫ И СОБЫТИЯ ────────────────────────────────────────────────────

@contracts_bp.route('/<int:contract_id>/transition', methods=['POST'])
@login_required
def transition(contract_id):
    contract = _get_or_404(contract_id)
    if not _can_edit(contract):
        abort(403)
    target = request.form.get('target', '')
    event_date = request.form.get('event_date') or None
    comment = request.form.get('comment', '').strip() or None
    conn = _db()
    backward = (target in workflow.route_order(contract)
                and workflow.route_order(contract).get(target, 99)
                < workflow.route_order(contract)[contract['stage']])
    if backward and not comment:
        flash('Укажите причину возврата на предыдущую стадию', 'danger')
        return redirect(url_for('contracts.detail', contract_id=contract_id))
    try:
        ok, err = workflow.apply_transition(
            conn, contract, target, session['user_id'], event_date, comment
        )
        if ok:
            conn.commit()
            log_action(conn, session['user_id'], 'contract_transition',
                       detail=f'Договор #{contract_id}: стадия {target}')
            flash('Стадия изменена', 'success')
        else:
            conn.rollback()
            flash(err or 'Переход не выполнен', 'danger')
    except Exception:
        conn.rollback()
        raise
    return redirect(url_for('contracts.detail', contract_id=contract_id))


@contracts_bp.route('/<int:contract_id>/acts-sent', methods=['POST'])
@login_required
def acts_sent(contract_id):
    contract = _get_or_404(contract_id)
    if not _can_edit(contract):
        abort(403)
    send_date = request.form.get('send_date') or None
    conn = _db()
    try:
        ok, err = workflow.acts_sent(
            conn, contract, session['user_id'], send_date
        )
        if ok:
            conn.commit()
            log_action(conn, session['user_id'], 'contract_acts_sent',
                       detail=f'Договор #{contract_id}: акты направлены')
            flash('Отмечено: акты направлены заказчику', 'success')
        else:
            conn.rollback()
            flash(err, 'danger')
    except Exception:
        conn.rollback()
        raise
    return redirect(url_for('contracts.detail', contract_id=contract_id))


# ─── УСЛУГИ ПО ПРИКАЗУ ─────────────────────────────────────────────────────

@contracts_bp.route('/<int:contract_id>/services/add', methods=['POST'])
@login_required
def add_service(contract_id):
    contract = _get_or_404(contract_id)
    if not _can_edit(contract):
        abort(403)
    conn = _db()
    item = conn.execute(
        "SELECT * FROM contract_tariff_items WHERE id=?",
        (request.form.get('tariff_item_id'),)
    ).fetchone()
    if item is None:
        flash('Не выбрана услуга', 'danger')
        return redirect(url_for('contracts.detail', contract_id=contract_id))
    qty = request.form.get('qty') or None
    area_ha = request.form.get('area_ha') or None
    manual_reason = request.form.get('manual_reason', '').strip() or None
    amount = _rub_to_kop(request.form.get('amount', ''))
    if amount is None:
        amount = pricing.service_amount(
            item['price_kop'], qty=qty or 1, area_ha=area_ha
        )
    try:
        repository.add_service(
            conn, contract_id, item, amount, qty=qty, area_ha=area_ha,
            manual_reason=manual_reason,
        )
        conn.commit()
        flash('Услуга добавлена', 'success')
    except Exception:
        conn.rollback()
        raise
    return redirect(url_for('contracts.detail', contract_id=contract_id))


@contracts_bp.route('/<int:contract_id>/services/<int:service_id>/delete',
                    methods=['POST'])
@login_required
def delete_service(contract_id, service_id):
    contract = _get_or_404(contract_id)
    if not _can_edit(contract):
        abort(403)
    conn = _db()
    try:
        repository.delete_service(conn, contract_id, service_id)
        conn.commit()
        flash('Услуга удалена', 'success')
    except Exception:
        conn.rollback()
        raise
    return redirect(url_for('contracts.detail', contract_id=contract_id))


# ─── ФАЙЛЫ ─────────────────────────────────────────────────────────────────

@contracts_bp.route('/<int:contract_id>/files/upload', methods=['POST'])
@login_required
def upload_file(contract_id):
    contract = _get_or_404(contract_id)
    if not _can_edit(contract):
        abort(403)
    category = request.form.get('category', '')
    if category not in documents.CATEGORY_LABELS:
        flash('Неизвестная категория файла', 'danger')
        return redirect(url_for('contracts.detail', contract_id=contract_id))
    f = request.files.get('file')
    if not f or not f.filename:
        flash('Файл не выбран', 'danger')
        return redirect(url_for('contracts.detail', contract_id=contract_id))
    conn = _db()
    try:
        file_id, err = documents.save_version(
            conn, contract_id, category, f, session['user_id'],
            comment=request.form.get('comment', '').strip() or None
        )
        if err:
            conn.rollback()
            flash(err, 'danger')
        else:
            conn.commit()
            flash('Файл загружен', 'success')
    except Exception:
        conn.rollback()
        raise
    return redirect(url_for('contracts.detail', contract_id=contract_id))


@contracts_bp.route('/<int:contract_id>/files/<int:file_id>/download')
@login_required
def download_file(contract_id, file_id):
    contract = _get_or_404(contract_id)
    row = _db().execute(
        "SELECT * FROM contract_files WHERE id=? AND contract_id=?",
        (file_id, contract_id)
    ).fetchone()
    if row is None:
        abort(404)
    path = documents.file_abs_path(row)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, as_attachment=True,
                     download_name=row['orig_name'])


# ─── ОПЛАТЫ И РАССМОТРЕНИЯ ─────────────────────────────────────────────────

@contracts_bp.route('/<int:contract_id>/payments/add', methods=['POST'])
@login_required
def add_payment(contract_id):
    contract = _get_or_404(contract_id)
    if not (_perm('can_contract_payments') or _can_edit(contract)):
        abort(403)
    amount = _rub_to_kop(request.form.get('amount', ''))
    if not amount:
        flash('Укажите сумму платежа', 'danger')
        return redirect(url_for('contracts.detail', contract_id=contract_id))
    conn = _db()
    try:
        repository.add_payment(
            conn, contract_id,
            kind=request.form.get('kind', 'income'),
            amount_kop=amount,
            number=request.form.get('number', '').strip() or None,
            pay_date=request.form.get('pay_date') or None,
            note=request.form.get('note', '').strip() or None,
            user_id=session['user_id'],
        )
        conn.commit()
        log_action(conn, session['user_id'], 'contract_payment',
                   detail=f'Договор #{contract_id}: платёж')
        flash('Платёж добавлен', 'success')
    except Exception:
        conn.rollback()
        raise
    return redirect(url_for('contracts.detail', contract_id=contract_id))


@contracts_bp.route('/<int:contract_id>/reviews/add', methods=['POST'])
@login_required
def add_review(contract_id):
    contract = _get_or_404(contract_id)
    if not _can_edit(contract):
        abort(403)
    body = request.form.get('body', '').strip()
    if not body:
        flash('Укажите орган рассмотрения', 'danger')
        return redirect(url_for('contracts.detail', contract_id=contract_id))
    conn = _db()
    try:
        repository.add_review(
            conn, contract_id, body,
            request_no=request.form.get('request_no', '').strip() or None,
            sent_date=request.form.get('sent_date') or None,
            accepted_date=request.form.get('accepted_date') or None,
            decision_date=request.form.get('decision_date') or None,
            result=request.form.get('result', '').strip() or None,
            comment=request.form.get('comment', '').strip() or None,
        )
        conn.commit()
        flash('Рассмотрение добавлено', 'success')
    except Exception:
        conn.rollback()
        raise
    return redirect(url_for('contracts.detail', contract_id=contract_id))


# ─── ГЕНЕРАЦИЯ ДОГОВОРА ─────────────────────────────────────────────────────

@contracts_bp.route('/<int:contract_id>/docx')
@login_required
def generate_docx(contract_id):
    contract = _get_or_404(contract_id)
    if not _can_edit(contract):
        abort(403)
    conn = _db()
    services = repository.get_services(conn, contract_id)
    try:
        path = documents.generate_contract_docx(conn, contract, services)
    except FileNotFoundError as e:
        flash(str(e), 'danger')
        return redirect(url_for('contracts.detail', contract_id=contract_id))
    name = f"Проект договора {contract['inner_number']}.docx"
    return send_file(path, as_attachment=True, download_name=name)


# ─── API: СПРАВОЧНИК И ТАРИФЫ ───────────────────────────────────────────────

@contracts_bp.route('/api/directory/search')
@login_required
def api_directory_search():
    if not _perm('can_view_contracts'):
        return jsonify([]), 403
    q = request.args.get('q', '').strip()
    if len(q) < 2:
        return jsonify([])
    return jsonify(contact_directory.search_directory(_db(), q, limit=10))


@contracts_bp.route('/api/tariffs')
@login_required
def api_tariffs():
    if not _perm('can_view_contracts'):
        return jsonify([]), 403
    rows = repository.get_tariff_items(_db())
    return jsonify([
        {
            'id': r['id'], 'code': r['code'], 'name': r['name'],
            'unit': r['unit'], 'price_kop': r['price_kop'],
            'per_area': r['per_area'],
            'price_rub': float(pricing.rub(r['price_kop'])),
        } for r in rows
    ])
