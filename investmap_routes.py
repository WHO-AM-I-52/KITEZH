# ╔══════════════════════════════════════════════════════════════╗
# ║                  investmap_routes.py                        ║
# ║  Конвертер + анализатор инвестплощадок              ║
# ║  Доступ: админ или can_view_investmap                 ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import Blueprint, render_template, request, jsonify
from auth_utils import login_required, permission_required
from tools.investmap_export import convert_excel_to_text
from tools.investmap_analyzer import analyze

investmap_bp = Blueprint('investmap', __name__)


@investmap_bp.route('/admin/investmap')
@login_required
@permission_required('can_view_investmap')
def investmap():
    return render_template('investmap.html')


@investmap_bp.route('/admin/investmap/convert', methods=['POST'])
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


@investmap_bp.route('/admin/investmap/analyze', methods=['POST'])
@login_required
@permission_required('can_view_investmap')
def investmap_analyze():
    """
    Полный анализ карточки инвестплощадки.

    POST /admin/investmap/analyze
    Content-Type: multipart/form-data
    file: .xlsx

    Возвращает JSON:
    {
        'export': {
            'format': int,
            'count': int,
            'text': str
        },
        'analysis': {
            'id':          str,
            'format':      str,
            'status':      str,
            'blocks': {
                '1': {'score': int, 'weight': float, 'contribution': float, 'missing': [str]},
                ...
            },
            'total':       int,
            'category':    str,
            'ready':       str,
            'all_missing': [str],
            'sms':         str | null,
            'signed':      str
        },
        'error': null или строка
    }

    Для формата 2 (N площадок) 'analysis' является списком.
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
            'export':   export,
            'analysis': None,
            'error':    export['error']
        }), 400

    data = export.get('data', {})
    fmt  = export.get('format')

    if fmt == 2 and isinstance(data, list):
        analysis = [analyze(d) for d in data]
    else:
        analysis = analyze(data)

    return jsonify({
        'export': {
            'format': fmt,
            'count':  export.get('count', 1),
            'text':   export.get('text', '')
        },
        'analysis': analysis,
        'error':    None
    })
