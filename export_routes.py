# ╔══════════════════════════════════════════════════════════════╗
# ║                       export_routes.py                       ║
# ║  Отчёты и сервисные операции:                               ║
# ║  - выгрузка обращений в Excel                               ║
# ║  - autosave-хук для принудительного checkpoint WAL-журнала  ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import Blueprint, request, redirect, url_for, send_file, jsonify, session
from datetime import datetime, date
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import os

from db import get_db, REPORTS_DIR
from auth_utils import login_required


# ─── НАСТРОЙКИ BLUEPRINT ─────────────────────────────────────────────────────

report_bp = Blueprint('report', __name__)


# ─── ВЫГРУЗКА ОБРАЩЕНИЙ В EXCEL ─────────────────────────────────────────────

@report_bp.route('/report')
@login_required
def report():
    """
    Формирует Excel-отчёт по обращениям.

    Фильтры принимаются из query-параметров:
      - date_from, date_to — диапазон дат обращения,
      - status           — статус обращения.

    Файл сохраняется во временную папку REPORTS_DIR
    и отдается пользователю как attachment.
    """
    # Читаем фильтры из URL
    df = request.args.get('date_from', '')
    dt = request.args.get('date_to', '')
    sf = request.args.get('status', '')

    # Формируем SQL с учётом фильтров
    conn = get_db()
    q = ("SELECT r.*,u.full_name as employee_name,ass.full_name as assigned_name "
         "FROM requests r "
         "LEFT JOIN users u   ON r.created_by=u.id "
         "LEFT JOIN users ass ON r.assigned_to=ass.id "
         "WHERE 1=1")
    p = []
    if df:
        q += ' AND r.request_date>=?'
        p.append(df)
    if dt:
        q += ' AND r.request_date<=?'
        p.append(dt)
    if sf:
        q += ' AND r.status=?'
        p.append(sf)

    rows = conn.execute(q + ' ORDER BY r.request_date', p).fetchall()
    conn.close()

    # Человеческие подписи статусов
    sm = {
        'draft':    'Черновик',
        'review':   'На проверке',
        'accepted': 'Принято в работу',
        'answered': 'Ответ направлен',
    }

    # ── Создаём книгу и лист ─────────────────────────────────────────────────
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Отчёт'

    # Стили шапки и строк
    hfill = PatternFill("solid", fgColor="1B5E7B")
    hfont = Font(bold=True, color="FFFFFF", size=10)
    alt   = PatternFill("solid", fgColor="EAF4FB")
    br    = Border(
        left=Side(style='thin', color='CCCCCC'),
        right=Side(style='thin', color='CCCCCC'),
        top=Side(style='thin', color='CCCCCC'),
        bottom=Side(style='thin', color='CCCCCC'),
    )

    # ── Заголовок отчёта (1–2 строки) ────────────────────────────────────────
    ws.merge_cells('A1:Q1')
    per = f" за период {df}–{dt}" if (df or dt) else ""
    ws['A1'].value     = f"Обращения на подбор земельных участков{per}"
    ws['A1'].font      = Font(bold=True, size=13, color="1B5E7B")
    ws['A1'].alignment = Alignment(horizontal='center')
    ws.row_dimensions[1].height = 26

    ws.merge_cells('A2:Q2')
    ws['A2'].value     = (
        f"Дата формирования: {datetime.now().strftime('%d.%m.%Y %H:%M')}  "
        f"Всего: {len(rows)}"
    )
    ws['A2'].font      = Font(italic=True, size=9, color="888888")
    ws['A2'].alignment = Alignment(horizontal='center')

    # ── Шапка таблицы (3-я строка) ───────────────────────────────────────────
    hdrs = [
        '№ обращения', 'Дата', 'Статус', 'Источник', 'Заявитель', 'Название проекта',
        'Контактное лицо', 'Телефон', 'E-mail', 'Инвестиции (млн)',
        'Рабочих мест', 'Площадь (га)', 'Застройка (м²)', 'Право пользования',
        'Районы', 'Ответственный', 'Дата ответа',
    ]
    for ci, h in enumerate(hdrs, 1):
        c = ws.cell(row=3, column=ci, value=h)
        c.fill = hfill
        c.font = hfont
        c.border = br
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[3].height = 38

    # ── Данные по обращениям (строки 4+) ─────────────────────────────────────
    for ri, r in enumerate(rows, 4):
        fill = alt if ri % 2 == 0 else PatternFill("solid", fgColor="FFFFFF")
        vals = [
            r['request_number'] or '—',
            r['request_date'] or '—',
            sm.get(r['status'], r['status']),
            r['source_type'] or '—',
            r['applicant_short_name'] or r['applicant_full_name'] or '—',
            r['project_name'] or '—',
            r['contact_person'] or '—',
            r['contact_phone'] or '—',
            r['contact_email'] or '—',
            r['investment_total'],
            r['jobs_total'],
            r['site_area_ha'],
            r['site_build_area_m2'],
            r['site_right'] or '—',
            r['preferred_districts'] or '—',
            r['assigned_name'] or r['employee_name'] or '—',
            r['answer_date'] or '—',
        ]
        for ci, val in enumerate(vals, 1):
            c = ws.cell(row=ri, column=ci, value=val)
            c.fill = fill
            c.border = br
            c.alignment = Alignment(vertical='center', wrap_text=True)
        ws.row_dimensions[ri].height = 16

    # ── Ширина колонок и заморозка шапки ─────────────────────────────────────
    for ci, w in enumerate(
        [16, 12, 20, 16, 28, 30, 20, 15, 24, 12, 10, 10, 12, 16, 24, 20, 12],
        1
    ):
        ws.column_dimensions[get_column_letter(ci)].width = w
    ws.freeze_panes = 'A4'

    # ── Сохранение файла и отдача пользователю ──────────────────────────────
    fn = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    fp = os.path.join(REPORTS_DIR, fn)
    wb.save(fp)

    return send_file(
        fp,
        as_attachment=True,
        download_name=fn,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


# ─── AUTOSAVE / WAL CHECKPOINT ──────────────────────────────────────────────

@report_bp.route('/autosave', methods=['POST'])
@login_required
def autosave():
    """
    Сервисный endpoint для клиентского autosave.

    Делает WAL-checkpoint в SQLite (PRAGMA wal_checkpoint(PASSIVE)),
    чтобы сбросить журнал на диск и уменьшить риск потерь данных.

    Возвращает JSON:
      {status: 'ok'}       — если всё прошло успешно
      {status: 'error', message: '...'} — если произошла ошибка
    """
    try:
        conn = get_db()
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        conn.close()
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500