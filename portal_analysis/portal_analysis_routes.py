import io

from flask import Blueprint, jsonify, render_template, request, session

try:
    import pandas as pd
except ImportError:
    pd = None

import db as _db
from .analysis_history import create_analysis_history_tables
from .batch_analysis import analyze_and_save_rows
from .export_normalizer import normalize_export_rows
from .history_summary import get_history_summary
from .presentation import build_contact_messages, build_site_results

portal_analysis_bp = Blueprint('portal_analysis', __name__)


def _require_login():
    if not session.get('user_id'):
        return jsonify({'error': 'Требуется авторизация'}), 401
    return None


def _excel_to_rows(file_bytes: bytes) -> list:
    if pd is None:
        raise RuntimeError('pandas не установлен')
    return pd.read_excel(io.BytesIO(file_bytes), dtype=str).fillna('').to_dict(orient='records')


@portal_analysis_bp.route('/portal-analysis-v2')
def page():
    if _require_login():
        from flask import redirect, url_for
        return redirect(url_for('auth.login'))
    return render_template('portal_analysis_v2.html')


@portal_analysis_bp.route('/api/portal-analysis-v2/history')
def api_history():
    err = _require_login()
    if err:
        return err
    conn = _db.get_db()
    try:
        create_analysis_history_tables(conn)
        return jsonify(get_history_summary(conn))
    finally:
        conn.close()


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
        history_overview = get_history_summary(conn)
    except Exception as exc:
        conn.rollback()
        return jsonify({'error': 'Ошибка сохранения истории анализа: ' + str(exc)}), 500
    finally:
        conn.close()
    site_results = build_site_results(rows)
    messages = build_contact_messages(site_results)
    all_scores = [site['score'] for site in site_results if site['included']]
    return jsonify({'messages': messages, 'site_results': site_results, 'total_sites': history['total_sites'], 'active_sites': history['active_sites'], 'excluded_sites': history['excluded_sites'], 'total_contacts': sum(1 for message in messages if message['contact'] != '__no_contact__'), 'avg_score': round(sum(all_scores) / len(all_scores)) if all_scores else 0, 'low_score_count': sum(1 for score in all_scores if score < 60), 'history': history, 'history_overview': history_overview})
