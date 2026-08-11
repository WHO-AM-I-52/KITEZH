from flask import Blueprint, render_template, request, jsonify, flash, g, redirect, url_for

from core.activity_log import log_action
from core.auth_utils import login_required, permission_required
from db import get_db
from core.kitezh_logger import err_logger
from portal_analysis.portal_checker import calc_portal_score_v2
from tools.investmap_export import convert_excel_to_text
from tools.investmap_analyzer import (
    analyze,
    build_summary_sms,
    build_v2_summary_sms,
)

investmap_bp = Blueprint('investmap', __name__)


@investmap_bp.route('/investmap')
@login_required
@permission_required('can_view_investmap')
def investmap():
    """Главная страница — плитки навигации."""
    return render_template('investmap.html')


@investmap_bp.route('/investmap/v1')
@login_required
@permission_required('can_view_investmap')
def investmap_v1():
    """Анализ заполняемости (ГИС ЭКОНОМИКА) — перенесено с /investmap."""
    return render_template('investmap_v1.html')


@investmap_bp.route('/investmap/v2')
@login_required
@permission_required('can_view_investmap')
def investmap_v2():
    """Анализ заполняемости v2 — страница с кнопкой «Правила» и счётчиком правил."""
    user = getattr(g, 'user', {}).get('login', 'unknown')
    db = None
    try:
        db = get_db()
        rules_count = db.execute(
            'SELECT COUNT(*) FROM investmap_rules'
        ).fetchone()[0]
        return render_template('investmap_v2.html', rules_count=rules_count)
    except Exception as exc:
        err_logger.exception(
            'investmap_v2 error | user=%s | %s', user, exc
        )
        flash('Ошибка при загрузке страницы анализа v2.', 'error')
        return render_template('investmap_v2.html', rules_count=0), 500
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                err_logger.exception('investmap_v2 close error | user=%s', user)


@investmap_bp.route('/investmap/v2', methods=['POST'])
@login_required
@permission_required('can_investmap_rules')
def investmap_v2_post():
    """
    Batch-оценка площадок через calc_portal_score_v2.

    POST /investmap/v2
    Content-Type: multipart/form-data
    file: .xlsx
    """
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'Файл не передан', 'results': [], 'count': 0}), 400
    if not f.filename.lower().endswith('.xlsx'):
        return jsonify({'error': 'Поддерживается только формат .xlsx', 'results': [], 'count': 0}), 400

    user = getattr(g, 'user', {}).get('login', 'unknown')
    db = None
    try:
        file_bytes = f.read()
        export = convert_excel_to_text(file_bytes)

        if export.get('error'):
            return jsonify({'results': [], 'count': 0, 'error': export['error']}), 400

        data = export.get('data', {})
        fmt = export.get('format')
        db = get_db()

        if fmt == 2 and isinstance(data, list):
            results = [calc_portal_score_v2(r, db) for r in data]
        else:
            results = [calc_portal_score_v2(data, db)]

        summary_sms = (
            build_v2_summary_sms(results, data)
            if fmt == 2 and isinstance(data, list) and len(results) > 1
            else None
        )

        log_action(db, getattr(g, 'user', {}).get('id'), 'investmap_v2_score',
                   detail=f'count={len(results)}')

        export_payload = {
            'format': fmt,
            'count': export.get('count', len(results)),
        }

        if fmt == 2:
            export_payload['texts'] = export.get('texts', [])
        else:
            text = export.get('text', '')
            export_payload['text'] = text
            export_payload['texts'] = [text]

        return jsonify({
            'results': results,
            'count': len(results),
            'summary_sms': summary_sms,
            'export': export_payload,
            'error': None,
        })

    except Exception as exc:
        err_logger.exception('investmap_v2 POST error | user=%s | %s', user, exc)
        return jsonify({'results': [], 'count': 0, 'error': 'Внутренняя ошибка сервера'}), 500
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                err_logger.exception(
                    'investmap_v2 POST close error | user=%s', user
                )


@investmap_bp.route('/investmap/v2/rules')
@login_required
@permission_required('can_investmap_rules')
def investmap_v2_rules():
    """Список правил рекомендаций."""
    db = None
    try:
        db = get_db()
        rules = db.execute("""
            SELECT r.id, r.source_field, r.source_value,
                   r.target_field, r.recommended_text,
                   sf.display_name AS source_display,
                   tf.display_name AS target_display
            FROM investmap_rules r
            LEFT JOIN investmap_fields sf ON sf.tech_name = r.source_field
            LEFT JOIN investmap_fields tf ON tf.tech_name = r.target_field
            ORDER BY r.id
        """).fetchall()
        fields = db.execute(
            "SELECT tech_name, display_name FROM investmap_fields ORDER BY display_name"
        ).fetchall()
        return render_template('investmap_v2_rules.html', rules=rules, fields=fields)
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                err_logger.exception('investmap_v2_rules close error')


@investmap_bp.route('/investmap/v2/rules/add', methods=['POST'])
@login_required
@permission_required('can_investmap_rules')
def investmap_v2_rules_add():
    """Добавить новое правило рекомендации."""
    source_field = request.form.get('source_field', '').strip()
    source_value = request.form.get('source_value', '').strip()
    target_field = request.form.get('target_field', '').strip()
    recommended_text = request.form.get('recommended_text', '').strip()

    if not all([source_field, source_value, target_field, recommended_text]):
        flash('Все поля обязательны.', 'error')
        return redirect(url_for('investmap.investmap_v2_rules'))

    user = getattr(g, 'user', {}).get('login', 'unknown')
    db = None
    try:
        db = get_db()
        db.execute(
            """INSERT INTO investmap_rules
               (source_field, source_value, target_field, recommended_text)
               VALUES (?, ?, ?, ?)""",
            (source_field, source_value, target_field, recommended_text)
        )
        db.commit()
        log_action(db, getattr(g, 'user', {}).get('id'), 'investmap_rules_add',
                   detail=f'source={source_field}:{source_value} → target={target_field}')
        flash('Правило добавлено.', 'success')
        return redirect(url_for('investmap.investmap_v2_rules'))
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                err_logger.exception('investmap_v2_rules_add close error | user=%s', user)


@investmap_bp.route('/investmap/v2/rules/delete/<int:rule_id>', methods=['POST'])
@login_required
@permission_required('can_investmap_rules')
def investmap_v2_rules_delete(rule_id):
    """Удалить правило рекомендации."""
    user = getattr(g, 'user', {}).get('login', 'unknown')
    db = None
    try:
        db = get_db()
        db.execute("DELETE FROM investmap_rules WHERE id = ?", (rule_id,))
        db.commit()
        log_action(db, getattr(g, 'user', {}).get('id'), 'investmap_rules_delete',
                   detail=f'rule_id={rule_id}')
        flash('Правило удалено.', 'success')
        return redirect(url_for('investmap.investmap_v2_rules'))
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                err_logger.exception('investmap_v2_rules_delete close error | user=%s', user)


@investmap_bp.route('/investmap/v2/rules/values')
@login_required
@permission_required('can_investmap_rules')
def investmap_v2_rules_values():
    """
    AJAX: вернуть список значений классификатора для выбранного source_field.

    GET /investmap/v2/rules/values?field=<tech_name>
    Возвращает JSON: list[str]
    """
    tech_name = request.args.get('field', '').strip()
    if not tech_name:
        return jsonify([])
    db = None
    try:
        db = get_db()
        row = db.execute(
            "SELECT classifier_num FROM investmap_fields WHERE tech_name = ?",
            (tech_name,)
        ).fetchone()
        if not row or not row['classifier_num']:
            return jsonify([])
        values = db.execute(
            "SELECT value FROM investmap_classifiers WHERE classifier_num = ? ORDER BY value",
            (row['classifier_num'],)
        ).fetchall()
        return jsonify([v['value'] for v in values])
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                err_logger.exception('investmap_v2_rules_values close error')


@investmap_bp.route('/investmap/convert', methods=['POST'])
@login_required
@permission_required('can_view_investmap')
def investmap_convert():
    """Только конвертация в текст — без анализа. Используется для отправки в AI-чат."""
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'Файл не передан'}), 400
    if not f.filename.lower().endswith('.xlsx'):
        return jsonify({'error': 'Поддерживается только формат .xlsx'}), 400

    result = convert_excel_to_text(f.read())
    if not result.get('error'):
        db = None
        try:
            db = get_db()
            log_action(db, getattr(g, 'user', {}).get('id'), 'investmap_convert',
                       detail=f'file={f.filename}')
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    err_logger.exception('investmap_convert close error')
    return jsonify(result)


@investmap_bp.route('/investmap/analyze', methods=['POST'])
@login_required
@permission_required('can_investmap_rules')
@permission_required('can_view_investmap')
def investmap_analyze():
    """
    Полный анализ карточки инвестплощадки.

    POST /investmap/analyze
    Content-Type: multipart/form-data
    file: .xlsx
    """
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'Файл не передан'}), 400
    if not f.filename.lower().endswith('.xlsx'):
        return jsonify({'error': 'Поддерживается только формат .xlsx'}), 400

    file_bytes = f.read()
    export = convert_excel_to_text(file_bytes)

    if export.get('error'):
        return jsonify({
            'export': export,
            'analysis': None,
            'summary_sms': None,
            'error': export['error']
        }), 400

    data = export.get('data', {})
    fmt = export.get('format')

    if fmt == 2 and isinstance(data, list):
        analysis = [analyze(d) for d in data]
        summary_sms = build_summary_sms(analysis)
    else:
        analysis = analyze(data)
        summary_sms = None

    return jsonify({
        'export': {
            'format': fmt,
            'count': export.get('count', 1),
            'text': export.get('text', '')
        },
        'analysis': analysis,
        'summary_sms': summary_sms,
        'error': None
    })
