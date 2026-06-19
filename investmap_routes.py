# ╔══════════════════════════════════════════════════════════════╗
# ║                  investmap_routes.py                        ║
# ║  Конвертер + анализатор инвестплощадок                      ║
# ║  Доступ: can_view_investmap — просмотр карты                 ║
# ║           can_investmap_rules — анализ (правила)             ║
# ║  Маршруты: /investmap (плитки),                             ║
# ║             /investmap/v1 (анализ ГИС ЭКОНОМИКА),           ║
# ║             /investmap/v2 (заглушка),                       ║
# ║             /investmap/v2/classifiers (загрузка справочников)║
# ║             /investmap/convert, /investmap/analyze           ║
# ╚══════════════════════════════════════════════════════════════╝

import io

import openpyxl
from flask import (Blueprint, flash, g, redirect, render_template,
                   request, jsonify, url_for)

from activity_log import log_action
from auth_utils import login_required, permission_required
from db import get_db
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
    """Анализ заполняемости v2 — заглушка (логика придёт в Карточке #5)."""
    return render_template('investmap_v2.html')


# ── Справочники классификаторов ────────────────────────────────────────────

@investmap_bp.route('/investmap/v2/classifiers')
@login_required
@permission_required('can_investmap_rules')
def investmap_v2_classifiers():
    """Список загруженных справочников с количеством значений."""
    db = get_db()
    rows = db.execute(
        """
        SELECT classifier_num, COUNT(*) AS cnt
        FROM investmap_classifiers
        GROUP BY classifier_num
        ORDER BY classifier_num
        """
    ).fetchall()
    return render_template('investmap_v2_classifiers.html', classifiers=rows)


@investmap_bp.route('/investmap/v2/classifiers/upload', methods=['POST'])
@login_required
@permission_required('can_investmap_rules')
def investmap_v2_classifiers_upload():
    """Загрузка Excel-файла справочника и сохранение в investmap_classifiers."""
    num = request.form.get('classifier_num', '').strip()
    f = request.files.get('file')

    if not num or not num.isdigit():
        flash('Укажите номер справочника (целое число).', 'danger')
        return redirect(url_for('investmap.investmap_v2_classifiers'))
    if not f or not f.filename.lower().endswith('.xlsx'):
        flash('Загрузите файл в формате .xlsx.', 'danger')
        return redirect(url_for('investmap.investmap_v2_classifiers'))

    classifier_num = int(num)
    db = get_db()

    field_row = db.execute(
        "SELECT field_name FROM investmap_fields WHERE classifier_num = ? LIMIT 1",
        (classifier_num,)
    ).fetchone()
    field_name = field_row['field_name'] if field_row else None

    try:
        wb = openpyxl.load_workbook(
            io.BytesIO(f.read()), read_only=True, data_only=True
        )
        ws = wb.active
        rows_data = list(ws.iter_rows(min_row=2, values_only=True))
    except Exception as e:
        flash(f'Ошибка чтения файла: {e}', 'danger')
        return redirect(url_for('investmap.investmap_v2_classifiers'))

    inserted = 0
    for row in rows_data:
        if len(row) < 2 or row[1] is None:
            continue
        sort_order = row[0] if row[0] is not None else inserted + 1
        value = str(row[1]).strip()
        if not value:
            continue
        db.execute(
            """
            INSERT OR REPLACE INTO investmap_classifiers
                (classifier_num, field_name, sort_order, value)
            VALUES (?, ?, ?, ?)
            """,
            (classifier_num, field_name, sort_order, value)
        )
        inserted += 1
    db.commit()

    log_action(db, g.user['id'], 'investmap_classifier_upload',
               detail=f'Справочник №{classifier_num}: загружено {inserted} значений')
    flash(f'Справочник №{classifier_num}: загружено {inserted} значений.', 'success')
    return redirect(url_for('investmap.investmap_v2_classifiers'))


@investmap_bp.route('/investmap/v2/classifiers/clear/<int:num>', methods=['POST'])
@login_required
@permission_required('can_investmap_rules')
def investmap_v2_classifiers_clear(num):
    """Удалить все записи справочника с указанным номером."""
    db = get_db()
    db.execute(
        "DELETE FROM investmap_classifiers WHERE classifier_num = ?",
        (num,)
    )
    db.commit()
    log_action(db, g.user['id'], 'investmap_classifier_clear',
               detail=f'Справочник №{num}: все записи удалены')
    flash(f'Справочник №{num} очищен.', 'success')
    return redirect(url_for('investmap.investmap_v2_classifiers'))


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
