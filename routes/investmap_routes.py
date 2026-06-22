# ╔══════════════════════════════════════════════════════════════╗
# ║                  investmap_routes.py                        ║
# ║  Конвертер + анализатор инвестплощадок                      ║
# ║  Доступ: can_view_investmap — просмотр карты                 ║
# ║           can_investmap_rules — анализ (правила)             ║
# ║  Маршруты: /investmap (плитки),                             ║
# ║             /investmap/v1 (анализ ГИС ЭКОНОМИКА),           ║
# ║             /investmap/v2 GET (заглушка),                   ║
# ║             /investmap/v2 POST (batch-оценка v2),           ║
# ║             /investmap/v2/rules (CRUD правил),              ║
# ║             /investmap/convert, /investmap/analyze           ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import Blueprint, render_template, request, jsonify, flash, g, redirect, url_for

from core.activity_log import log_action
from core.auth_utils import login_required, permission_required
from db import get_db
from core.kitezh_logger import err_logger
from portal_analysis.portal_checker import calc_portal_score_v2
from tools.investmap_export import convert_excel_to_text
from tools.investmap_analyzer import analyze, build_summary_sms

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
    """Анализ заполняемости v2 — заглушка (шаблон обновится в Карточке #5в)."""
    user = getattr(g, 'user', {}).get('login', 'unknown')
    try:
        return render_template('investmap_v2.html')
    except Exception as exc:
        err_logger.exception(
            'investmap_v2 error | user=%s | %s', user, exc
        )
        flash('Ошибка при загрузке страницы анализа v2.', 'error')
        return render_template('investmap_v2.html'), 500


@investmap_bp.route('/investmap/v2', methods=['POST'])
@login_required
@permission_required('can_investmap_rules')
def investmap_v2_post():
    """
    Batch-оценка площадок через calc_portal_score_v2.

    POST /investmap/v2
    Content-Type: multipart/form-data
    file: .xlsx

    Возвращает JSON:
    {
        'results': list[dict],  # score/filled/total/missing/skipped
        'count': int,
        'export': {
            'format': int,
            'count': int,
            'text': str         # текст площадки для передачи в ИИ
        },
        'error': null
    }

    Для формата 2 (список площадок) итерирует каждую строку.
    Для форматов 1/3 (одна площадка) оборачивает результат в список.
    """
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'Файл не передан', 'results': [], 'count': 0}), 400
    if not f.filename.lower().endswith('.xlsx'):
        return jsonify({'error': 'Поддерживается только формат .xlsx', 'results': [], 'count': 0}), 400

    user = getattr(g, 'user', {}).get('login', 'unknown')
    try:
        file_bytes = f.read()
        export = convert_excel_to_text(file_bytes)

        if export.get('error'):
            return jsonify({'results': [], 'count': 0, 'error': export['error']}), 400

        data = export.get('data', {})
        fmt  = export.get('format')
        db   = get_db()

        if fmt == 2 and isinstance(data, list):
            results = [calc_portal_score_v2(r, db) for r in data]
        else:
            results = [calc_portal_score_v2(data, db)]

        return jsonify({
            'results': results,
            'count':   len(results),
            'export':  {
                'format': fmt,
                'count':  export.get('count', len(results)),
                'text':   export.get('text', ''),
            },
            'error': None,
        })

    except Exception as exc:
        err_logger.exception('investmap_v2 POST error | user=%s | %s', user, exc)
        return jsonify({'results': [], 'count': 0, 'error': 'Внутренняя ошибка сервера'}), 500


# ── CRUD-редактор правил investmap_rules ──────────────────────────────────

@investmap_bp.route('/investmap/v2/rules')
@login_required
@permission_required('can_investmap_rules')
def investmap_v2_rules():
    """Список правил рекомендаций."""
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


@investmap_bp.route('/investmap/v2/rules/add', methods=['POST'])
@login_required
@permission_required('can_investmap_rules')
def investmap_v2_rules_add():
    """Добавить новое правило рекомендации."""
    source_field     = request.form.get('source_field', '').strip()
    source_value     = request.form.get('source_value', '').strip()
    target_field     = request.form.get('target_field', '').strip()
    recommended_text = request.form.get('recommended_text', '').strip()

    if not all([source_field, source_value, target_field, recommended_text]):
        flash('Все поля обязательны.', 'error')
        return redirect(url_for('investmap.investmap_v2_rules'))

    user = getattr(g, 'user', {}).get('login', 'unknown')
    db = get_db()
    db.execute(
        """INSERT INTO investmap_rules
           (source_field, source_value, target_field, recommended_text)
           VALUES (?, ?, ?, ?)""",
        (source_field, source_value, target_field, recommended_text)
    )
    db.commit()
    log_action(user, 'investmap_rules_add',
               f'source={source_field}:{source_value} → target={target_field}')
    flash('Правило добавлено.', 'success')
    return redirect(url_for('investmap.investmap_v2_rules'))


@investmap_bp.route('/investmap/v2/rules/delete/<int:rule_id>', methods=['POST'])
@login_required
@permission_required('can_investmap_rules')
def investmap_v2_rules_delete(rule_id):
    """Удалить правило рекомендации."""
    user = getattr(g, 'user', {}).get('login', 'unknown')
    db = get_db()
    db.execute("DELETE FROM investmap_rules WHERE id = ?", (rule_id,))
    db.commit()
    log_action(user, 'investmap_rules_delete', f'rule_id={rule_id}')
    flash('Правило удалено.', 'success')
    return redirect(url_for('investmap.investmap_v2_rules'))


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


# ── Конвертация и анализ ───────────────────────────────────────────────────

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

    Возвращает JSON:
    {
        'export': {
            'format': int,
            'count': int,
            'text': str
        },
        'analysis': dict | list[dict],
        'summary_sms': str | null,  # Только для формата 2 (N площадок)
        'error': null или строка
    }

    Для формата 2 (N площадок) 'analysis' является списком.
    summary_sms — сводный текст по всем площадкам с проблемами (null если все заполнены).
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
            'export':      export,
            'analysis':    None,
            'summary_sms': None,
            'error':       export['error']
        }), 400

    data = export.get('data', {})
    fmt  = export.get('format')

    if fmt == 2 and isinstance(data, list):
        analysis    = [analyze(d) for d in data]
        summary_sms = build_summary_sms(analysis)
    else:
        analysis    = analyze(data)
        summary_sms = None

    return jsonify({
        'export': {
            'format': fmt,
            'count':  export.get('count', 1),
            'text':   export.get('text', '')
        },
        'analysis':    analysis,
        'summary_sms': summary_sms,
        'error':       None
    })
