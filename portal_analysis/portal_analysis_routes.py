# ═══════════════════════════════════════════════════════════════════════
# portal_analysis/portal_analysis_routes.py
# Маршруты для вкладки «Анализ заполняемости площадок v2.0»
# v1.0.0 (17.06.2026)
# ═══════════════════════════════════════════════════════════════════════

import io
import re

from flask import Blueprint, render_template, request, jsonify, session

try:
    import pandas as pd
except ImportError:
    pd = None

import db as _db

from .export_normalizer import normalize_export_rows
from .portal_checker import calc_portal_score, _strip_html
from .portal_message_builder import build_messages, FIELD_HINTS, _get_site_name, _get_site_id, _get_contact

portal_analysis_bp = Blueprint('portal_analysis', __name__)


def _require_login():
    """Возвращает None если авторизован, иначе jsonify с ошибкой."""
    if not session.get('user_id'):
        return jsonify({'error': 'Требуется авторизация'}), 401
    return None


def _excel_to_rows(file_bytes: bytes) -> list:
    """Читает xlsx-файл и возвращает список dict (строка = площадка)."""
    if pd is None:
        raise RuntimeError('pandas не установлен')
    df = pd.read_excel(io.BytesIO(file_bytes), dtype=str)
    df = df.fillna('')
    return df.to_dict(orient='records')


def _scores_for_message(rows: list) -> list:
    """Строит данные оценки для карточек, выводимых в сообщениях."""
    out = []
    for row in rows:
        result = calc_portal_score(row)
        missing = [
            {'field': f, 'hint': FIELD_HINTS.get(f.strip().lower(), 'Заполните поле на портале invest.gov.ru.')}
            for f in result['missing']
        ]
        out.append({
            'name': _get_site_name(row),
            'id': _get_site_id(row),
            'score': result['score'],
            'missing': missing,
        })
    return out


@portal_analysis_bp.route('/portal-analysis-v2')
def page():
    err = _require_login()
    if err:
        from flask import redirect, url_for
        return redirect(url_for('auth.login'))
    return render_template('portal_analysis_v2.html')


@portal_analysis_bp.route('/api/portal-analysis-v2', methods=['POST'])
def api_analyze():
    err = _require_login()
    if err:
        return err

    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'Файл не передан'}), 400
    if not f.filename.lower().endswith('.xlsx'):
        return jsonify({'error': 'Поддерживается только формат .xlsx'}), 400

    try:
        rows = _excel_to_rows(f.read())
    except Exception as e:
        return jsonify({'error': 'Ошибка чтения файла: ' + str(e)}), 400

    if not rows:
        return jsonify({'error': 'Файл пустой или не содержит строк'}), 400

    rows = normalize_export_rows(rows)
    messages = build_messages(rows)

    groups: dict[str, list] = {}
    no_contact_rows: list = []
    for row in rows:
        contact = _get_contact(row)
        if contact:
            groups.setdefault(contact, []).append(row)
        else:
            no_contact_rows.append(row)

    scores_map: dict[str, list] = {}
    for contact, contact_rows in groups.items():
        scores_map[contact] = _scores_for_message(contact_rows)
    if no_contact_rows:
        scores_map['__no_contact__'] = _scores_for_message(no_contact_rows)

    for message in messages:
        message['scores'] = scores_map.get(message['contact'], [])

    all_scores = [score['score'] for message in messages for score in message.get('scores', [])]
    avg_score = round(sum(all_scores) / len(all_scores)) if all_scores else 0
    low_count = sum(1 for score in all_scores if score < 60)
    contacts = sum(1 for message in messages if message['contact'] != '__no_contact__')

    return jsonify({
        'messages': messages,
        'total_sites': len(rows),
        'total_contacts': contacts,
        'avg_score': avg_score,
        'low_score_count': low_count,
    })
