import io

from flask import Blueprint, jsonify, render_template, request, session

try:
    import pandas as pd
except ImportError:
    pd = None

import db as _db
from .batch_analysis import analyze_and_save_rows
from .export_normalizer import normalize_export_rows
from .portal_checker import calc_portal_score
from .portal_message_builder import build_messages, FIELD_HINTS, _get_site_name, _get_site_id, _get_contact
from .presentation import build_site_results

portal_analysis_bp = Blueprint('portal_analysis', __name__)


def _require_login():
    if not session.get('user_id'):
        return jsonify({'error': 'Требуется авторизация'}), 401
    return None


def _excel_to_rows(file_bytes: bytes) -> list:
    if pd is None:
        raise RuntimeError('pandas не установлен')
    return pd.read_excel(io.BytesIO(file_bytes), dtype=str).fillna('').to_dict(orient='records')


def _scores_for_message(rows: list) -> list:
    output = []
    for row in rows:
        result = calc_portal_score(row)
        output.append({'name': _get_site_name(row), 'id': _get_site_id(row), 'score': result['score'], 'missing': [{'field': field, 'hint': FIELD_HINTS.get(field.strip().lower(), 'Заполните поле на портале invest.gov.ru.')} for field in result['missing']]})
    return output


@portal_analysis_bp.route('/portal-analysis-v2')
def page():
    if _require_login():
        from flask import redirect, url_for
        return redirect(url_for('auth.login'))
    return render_template('portal_analysis_v2.html')


@portal_analysis_bp.route('/api/portal-analysis-v2', methods=['POST'])
def api_analyze():
    err = _require_login()
    if err:
        return err
    upload = request.files.get('file')
    if not upload:
        return jsonify({'error': 'Файл не передан'}), 400
    if not upload.filename.lower().endswith('.xlsx'):
        return jsonify({'error': 'Поддерживается только формат .xlsx'}), 400
    try:
        rows = normalize_export_rows(_excel_to_rows(upload.read()))
    except Exception as exc:
        return jsonify({'error': 'Ошибка чтения файла: ' + str(exc)}), 400
    if not rows:
        return jsonify({'error': 'Файл пустой или не содержит строк'}), 400
    conn = _db.get_db()
    try:
        history = analyze_and_save_rows(conn, rows, initiated_by=session.get('user_id'), source_label=upload.filename)
        conn.commit()
    except Exception as exc:
        conn.rollback()
        return jsonify({'error': 'Ошибка сохранения истории анализа: ' + str(exc)}), 500
    finally:
        conn.close()

    site_results = build_site_results(rows)
    included_rows = [row for row, site in zip(rows, site_results) if site['included']]
    messages = build_messages(included_rows)
    groups, no_contact_rows = {}, []
    for row in included_rows:
        contact = _get_contact(row)
        (groups.setdefault(contact, []).append(row) if contact else no_contact_rows.append(row))
    scores_map = {contact: _scores_for_message(contact_rows) for contact, contact_rows in groups.items()}
    if no_contact_rows:
        scores_map['__no_contact__'] = _scores_for_message(no_contact_rows)
    for message in messages:
        message['scores'] = scores_map.get(message['contact'], [])
    all_scores = [site['score'] for site in site_results if site['included']]
    return jsonify({'messages': messages, 'site_results': site_results, 'total_sites': history['total_sites'], 'active_sites': history['active_sites'], 'excluded_sites': history['excluded_sites'], 'total_contacts': sum(1 for message in messages if message['contact'] != '__no_contact__'), 'avg_score': round(sum(all_scores) / len(all_scores)) if all_scores else 0, 'low_score_count': sum(1 for score in all_scores if score < 60), 'history': history})
