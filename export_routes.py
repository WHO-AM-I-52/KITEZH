# ╔═════════════════════════════════════════════════════════════════════════════╗
# ║                       export_routes.py                                       ║
# ║  v3.7: алиасы /export/excel → report, /export/excel/upload → import_full    ║
# ║  v3.8: site_area_ha/site_build_area_m2 → _min/_max (bagfix v2.8)            ║
# ║  v3.9: декомпозиция — helpers/excel/import вынесены в отдельные модули        ║
# ║        (export_helpers.py, export_excel.py, export_import.py).               ║
# ║        Здесь остаётся Blueprint report_bp и тонкие роуты-обёртки.            ║
# ╚═════════════════════════════════════════════════════════════════════════════╝

from flask import Blueprint, request, send_file, jsonify, session

from db import get_db
from auth_utils import login_required, get_user_perm
from activity_log import log_action

from export_excel import build_report_wb, build_minek_wb, build_full_wb
from export_import import process_import_full, process_import_sites

# Реэкспорт для обратной совместимости: внешний код, импортировавший эти имена
# из export_routes, продолжит работать (validate_site_record и helpers).
from export_import import validate_site_record, _parse_coords_point  # noqa: F401
from export_helpers import (  # noqa: F401
    _short_fio, _std_border, _hex_to_argb, _fmt_date,
    _parse_date_for_db, _is_empty_cell, _parse_numeric_for_db,
    _mln_to_mld, _contact_cell, _apply_cell_value, _gen_request_number,
    DATE_FIELDS, NUMERIC_FIELDS, REQUIRED_FOR_CREATE, STATUS_IMPORT_MAP,
)

_XLSX_MIME = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

report_bp = Blueprint('report', __name__)


def _log_export(action: str, detail: str) -> None:
    """Записать действие экспорта в журнал (отдельным соединением)."""
    try:
        conn = get_db()
        log_action(conn, session['user_id'], action, detail=detail)
        conn.commit()
        conn.close()
    except Exception:
        pass


# ─── СТАНДАРТНАЯ ВЫГРУЗКА ─────────────────────────────────────────────────────────────────────

@report_bp.route('/report')
@report_bp.route('/export/excel')  # алиас для фронта (фикс 404)
@login_required
def report():
    df = request.args.get('date_from', '')
    dt = request.args.get('date_to', '')
    sf = request.args.get('status', '')

    fp, fn, detail = build_report_wb(df, dt, sf)
    _log_export('export_report', detail)

    return send_file(fp, as_attachment=True, download_name=fn, mimetype=_XLSX_MIME)


# ─── ЕЖЕНЕДЕЛЬНАЯ ВЫГРУЗКА ДЛЯ МИНЭК ───────────────────────────────────────────────────────────────────

@report_bp.route('/report/minek')
@login_required
def report_minek():
    from datetime import date, timedelta
    today      = date.today()
    week_start = today - timedelta(days=today.weekday())
    week_end   = week_start + timedelta(days=6)

    df = request.args.get('date_from', week_start.isoformat())
    dt = request.args.get('date_to',   week_end.isoformat())
    sf = request.args.get('status', '')

    fp, fn, detail = build_minek_wb(df, dt, sf)
    _log_export('export_minek', detail)

    return send_file(fp, as_attachment=True, download_name=fn, mimetype=_XLSX_MIME)


# ─── ПОЛНАЯ ВЫГРУЗКА БАЗЫ ─────────────────────────────────────────────────────────────────────────

@report_bp.route('/export/full')
@report_bp.route('/export/excel/base')  # алиас для обратной совместимости
@login_required
def export_full():
    if not get_user_perm('can_export_full'):
        return jsonify({'error': 'Недостаточно прав: Скачать полную базу (Excel)'}), 403

    fp, fn, detail = build_full_wb()
    _log_export('export_full', detail)

    return send_file(fp, as_attachment=True, download_name=fn, mimetype=_XLSX_MIME)


# ─── ИМПОРТ ОБНОВЛЁННОГО EXCEL ──────────────────────────────────────────────────────────────────────────────────

@report_bp.route('/import/full', methods=['POST'])
@report_bp.route('/export/excel/upload', methods=['POST'])  # алиас для фронта (фикс 404)
@login_required
def import_full():
    if not get_user_perm('can_import_full'):
        return jsonify({'error': 'Недостаточно прав: Загрузить обновлённый Excel (импорт)'}), 403

    file = request.files.get('import_file')
    if not file or not file.filename.endswith('.xlsx'):
        return jsonify({'error': 'Загрузите файл .xlsx'}), 400

    overwrite = request.form.get('overwrite') == '1'

    payload, status = process_import_full(file, overwrite, session['user_id'])
    return jsonify(payload), status


# ─── AUTOSAVE / WAL CHECKPOINT ─────────────────────────────────────────────────────────────────────────────────

@report_bp.route('/autosave', methods=['POST'])
@login_required
def autosave():
    try:
        conn = get_db()
        conn.execute('PRAGMA wal_checkpoint(PASSIVE)')
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


# ─── ВАЛИДАЦИЯ ПЛОЩАДОК ГИС НСИ ─────────────────────────────────────────────

@report_bp.route('/import/sites', methods=['POST'])
@login_required
def import_sites():
    """Импорт выгрузки площадок ГИС НСИ с валидацией качества данных.

    POST multipart/form-data:
        import_file — .xlsx файл выгрузки ГИС НСИ
        dry_run     — '1' = только проверить, не сохранять

    Response JSON:
        total       — всего строк
        passed      — прошли без критических ошибок
        blocked     — заблокированы критическими ошибками
        fixes_count — кол-во автоисправлений
        report      — список {row, name, errors, warnings, fixes} для каждой строки
    """
    if not get_user_perm('can_import_full'):
        return jsonify({'error': 'Недостаточно прав'}), 403

    file = request.files.get('import_file')
    if not file or not file.filename.endswith('.xlsx'):
        return jsonify({'error': 'Загрузите файл .xlsx'}), 400

    dry_run = request.form.get('dry_run') == '1'

    payload, status = process_import_sites(file, dry_run, session['user_id'])
    return jsonify(payload), status
