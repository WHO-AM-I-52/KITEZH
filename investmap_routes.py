# ╔══════════════════════════════════════════════════════════════╗
# ║                  investmap_routes.py                        ║
# ║  Конвертер + анализатор инвестплощадок                      ║
# ║  Доступ: can_view_investmap — просмотр карты                 ║
# ║           can_investmap_rules — анализ (правила)             ║
# ║  Маршруты: /investmap (плитки),                             ║
# ║             /investmap/v1 (анализ ГИС ЭКОНОМИКА),           ║
# ║             /investmap/v2 GET (заглушка),                   ║
# ║             /investmap/v2 POST (batch-оценка v2),           ║
# ║             /investmap/convert, /investmap/analyze           ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import Blueprint, render_template, request, jsonify, flash, g

from auth_utils import login_required, permission_required
from db import get_db
from kitezh_logger import err_logger
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

        return jsonify({'results': results, 'count': len(results), 'error': None})

    except Exception as exc:
        err_logger.exception('investmap_v2 POST error | user=%s | %s', user, exc)
        return jsonify({'results': [], 'count': 0, 'error': 'Внутренняя ошибка сервера'}), 500


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
