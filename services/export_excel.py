# ╔═════════════════════════════════════════════════════════════════════════════╗
# ║                       export_excel.py                                        ║
# ║  Построители Excel-выгрузок (стандартная, МинЭК, полная база).               ║
# ║  Выделено из export_routes.py (декомпозиция, refactor/structure).            ║
# ║  Каждая функция строит файл в REPORTS_DIR и возвращает                        ║
# ║  (filepath, filename, log_detail). Роутинг и логирование — в export_routes.  ║
# ╚═════════════════════════════════════════════════════════════════════════════╝

from datetime import datetime, date, timedelta
import os
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from db import get_db, REPORTS_DIR
from services.export_helpers import (
    _std_border, _hex_to_argb, _fmt_date, _mln_to_mld,
    _short_fio, _contact_cell,
)


def build_report_wb(date_from: str, date_to: str, status: str) -> tuple:
    """Стандартная выгрузка обращений. → (filepath, filename, log_detail)."""
    df, dt, sf = date_from, date_to, status

    conn = get_db()
    try:
        q = ("SELECT r.*,u.full_name as employee_name,ass.full_name as assigned_name "
             "FROM requests r "
             "LEFT JOIN users u   ON r.created_by=u.id "
             "LEFT JOIN users ass ON r.assigned_to=ass.id "
             "WHERE 1=1")
        p = []
        if df:
            q += ' AND r.request_date>=?'; p.append(df)
        if dt:
            q += ' AND r.request_date<=?'; p.append(dt)
        if sf:
            q += ' AND r.status=?'; p.append(sf)

        rows = conn.execute(q + ' ORDER BY r.request_date', p).fetchall()
    finally:
        conn.close()

    sm = {
        'draft':    'Черновик',
        'review':   'На проверке',
        'accepted': 'Принято в работу',
        'answered': 'Ответ направлен',
    }

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Отчёт'

    hfill = PatternFill("solid", fgColor="1B5E7B")
    hfont = Font(bold=True, color="FFFFFF", size=10)
    alt   = PatternFill("solid", fgColor="EAF4FB")
    br    = _std_border()

    ws.merge_cells('A1:R1')
    per = f" за период {_fmt_date(df)}–{_fmt_date(dt)}" if (df or dt) else ""
    ws['A1'].value     = f"Обращения на подбор земельных участков{per}"
    ws['A1'].font      = Font(bold=True, size=13, color="1B5E7B")
    ws['A1'].alignment = Alignment(horizontal='center')
    ws.row_dimensions[1].height = 26

    ws.merge_cells('A2:R2')
    ws['A2'].value = (
        f"Дата формирования: {datetime.now().strftime('%d.%m.%Y %H:%M')}  "
        f"Всего: {len(rows)}"
    )
    ws['A2'].font      = Font(italic=True, size=9, color="888888")
    ws['A2'].alignment = Alignment(horizontal='center')

    # v3.8: площадь разбита на min/max — добавлен столбец
    hdrs = [
        '№ обращения', 'Дата', 'Статус', 'Источник', 'Заявитель', 'Название проекта',
        'Контактное лицо', 'Телефон', 'E-mail', 'Инвестиции (млн)',
        'Рабочих мест', 'Площадь от (га)', 'Площадь до (га)',
        'Застройка от (м²)', 'Застройка до (м²)',
        'Право пользования', 'Районы', 'Дата ответа',
    ]
    for ci, h in enumerate(hdrs, 1):
        c = ws.cell(row=3, column=ci, value=h)
        c.fill = hfill; c.font = hfont; c.border = br
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[3].height = 38

    for ri, r in enumerate(rows, 4):
        fill = alt if ri % 2 == 0 else PatternFill("solid", fgColor="FFFFFF")
        vals = [
            r['request_number'] or '—',
            _fmt_date(r['request_date']),
            sm.get(r['status'], r['status']),
            r['source_type'] or '—',
            r['applicant_short_name'] or r['applicant_full_name'] or '—',
            r['project_name'] or '—',
            r['contact_person'] or '—',
            r['contact_phone'] or '—',
            r['contact_email'] or '—',
            r['investment_total'],
            r['jobs_total'],
            r['site_area_ha_min'],        # v3.8
            r['site_area_ha_max'],        # v3.8
            r['site_build_area_m2_min'],  # v3.8
            r['site_build_area_m2_max'],  # v3.8
            r['site_right'] or '—',
            r['preferred_districts'] or '—',
            _fmt_date(r['answer_date']),
        ]
        for ci, val in enumerate(vals, 1):
            c = ws.cell(row=ri, column=ci, value=val)
            c.fill = fill; c.border = br
            c.alignment = Alignment(vertical='center', wrap_text=True)
        ws.row_dimensions[ri].height = 16

    for ci, w in enumerate(
        [16, 12, 20, 16, 28, 30, 20, 15, 24, 12, 10, 10, 10, 12, 12, 16, 24, 12], 1
    ):
        ws.column_dimensions[get_column_letter(ci)].width = w
    ws.freeze_panes = 'A4'

    fn = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    fp = os.path.join(REPORTS_DIR, fn)
    wb.save(fp)

    log_parts = []
    if df or dt:
        log_parts.append(f"период: {_fmt_date(df)} – {_fmt_date(dt)}")
    if sf:
        log_parts.append(f"статус: {sm.get(sf, sf)}")
    log_parts.append(f"всего {len(rows)} обращ.")
    return fp, fn, '; '.join(log_parts)


def build_minek_wb(date_from: str, date_to: str, status: str) -> tuple:
    """Еженедельная выгрузка для МинЭК. → (filepath, filename, log_detail)."""
    df, dt, sf = date_from, date_to, status

    conn = get_db()
    try:
        q = """
            SELECT
                r.*,
                u.full_name   AS employee_name,
                ass.full_name AS assigned_name,
                st.name       AS subject_type_name,
                rt.name       AS result_type_name,
                rt.color_hex  AS result_color
            FROM requests r
            LEFT JOIN users         u   ON r.created_by      = u.id
            LEFT JOIN users         ass ON r.assigned_to     = ass.id
            LEFT JOIN subject_types st  ON r.subject_type_id = st.id
            LEFT JOIN result_types  rt  ON r.result_type_id  = rt.id
            WHERE r.request_date >= ? AND r.request_date <= ?
        """
        p = [df, dt]
        if sf:
            q += ' AND r.status = ?'; p.append(sf)
        q += ' ORDER BY r.request_date, r.id'

        rows = conn.execute(q, p).fetchall()
        result_types = conn.execute(
            'SELECT id, name, color_hex FROM result_types ORDER BY id'
        ).fetchall()
    finally:
        conn.close()

    HEADER_COLOR = '1B5E7B'
    hfill = PatternFill('solid', fgColor=HEADER_COLOR)
    hfont = Font(bold=True, color='FFFFFF', size=10)
    alt   = PatternFill('solid', fgColor='EAF4FB')
    br    = _std_border()

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Заявки'

    NCOLS = 12

    ws.merge_cells(f'A1:{get_column_letter(NCOLS)}1')
    ws['A1'].value = (
        f"Еженедельный доклад МинЭК: обращения за период "
        f"{_fmt_date(df)} – {_fmt_date(dt)}"
    )
    ws['A1'].font      = Font(bold=True, size=13, color=HEADER_COLOR)
    ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
    ws.row_dimensions[1].height = 28

    ws.merge_cells(f'A2:{get_column_letter(NCOLS)}2')
    ws['A2'].value = (
        f"Дата формирования: {datetime.now().strftime('%d.%m.%Y %H:%M')}   "
        f"Обращений в выборке: {len(rows)}"
    )
    ws['A2'].font      = Font(italic=True, size=9, color='888888')
    ws['A2'].alignment = Alignment(horizontal='center')

    HEADERS = [
        '',
        'Дата обращения',
        'Наименование компании',
        'Наименование проекта',
        'Объем инвестиций,\nмлрд рублей',
        'Рабочие места',
        'Предмет обращения',
        'Дата направления презентации',
        'Дата получения обратной связи',
        'Итоги работы по обращению',
        'Менеджер',
        'Телефон, контактное лицо',
    ]

    for ci, h in enumerate(HEADERS, 1):
        c = ws.cell(row=3, column=ci, value=h)
        c.fill = hfill; c.font = hfont; c.border = br
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[3].height = 40

    for ri, r in enumerate(rows, 4):
        argb = _hex_to_argb(r['result_color']) if r['result_color'] else ''
        if argb:
            rfill = PatternFill('solid', fgColor=argb)
        else:
            rfill = alt if ri % 2 == 0 else PatternFill('solid', fgColor='FFFFFFFF')

        result_val = r['additional_info'] or r['result_type_name'] or '—'

        vals = [
            ri - 3,
            _fmt_date(r['request_date']),
            r['applicant_short_name'] or r['applicant_full_name'] or '—',
            r['project_name'] or '—',
            _mln_to_mld(r['investment_total']),
            r['jobs_total'] or '—',
            r['subject_type_name'] or '—',
            _fmt_date(r['answer_date']),
            _fmt_date(r['feedback_date']),
            result_val,
            _short_fio(r['assigned_name'] or r['employee_name']),
            _contact_cell(
                r['contact_person'],
                r['contact_phone'],
                r['contact_email'],
            ),
        ]

        for ci, val in enumerate(vals, 1):
            c = ws.cell(row=ri, column=ci, value=val)
            c.fill   = rfill
            c.border = br
            c.alignment = Alignment(
                vertical='center',
                wrap_text=True,
                horizontal='center' if ci == 1 else 'left',
            )
        ws.row_dimensions[ri].height = 30

    col_widths = [5, 13, 28, 35, 12, 12, 22, 16, 16, 30, 16, 32]
    for ci, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(ci)].width = w

    ws.freeze_panes = 'B4'

    wl = wb.create_sheet(title='Справочник')
    wl.merge_cells('A1:C1')
    wl['A1'].value     = 'Легенда цветов (итоги работы по обращению)'
    wl['A1'].font      = Font(bold=True, size=12, color=HEADER_COLOR)
    wl['A1'].alignment = Alignment(horizontal='center')
    wl.row_dimensions[1].height = 22

    for ci, h in enumerate(['Цвет', 'Итог', 'Обозначение'], 1):
        c = wl.cell(row=2, column=ci, value=h)
        c.fill = PatternFill('solid', fgColor=HEADER_COLOR)
        c.font = Font(bold=True, color='FFFFFF', size=10)
        c.border = _std_border()
        c.alignment = Alignment(horizontal='center', vertical='center')
    wl.row_dimensions[2].height = 20

    if result_types:
        for li, rt in enumerate(result_types, 3):
            argb = _hex_to_argb(rt['color_hex'] or '')
            fill = PatternFill('solid', fgColor=argb) if argb else PatternFill('solid', fgColor='FFFFFFFF')
            ca = wl.cell(row=li, column=1, value='')
            ca.fill = fill; ca.border = _std_border()
            cb = wl.cell(row=li, column=2, value=rt['name'])
            cb.fill = fill
            cb.font = Font(bold=True, size=10); cb.border = _std_border()
            cb.alignment = Alignment(vertical='center')
            cc = wl.cell(row=li, column=3, value=rt['color_hex'])
            cc.font = Font(italic=True, size=9, color='888888'); cc.border = _std_border()
            cc.alignment = Alignment(vertical='center')
            wl.row_dimensions[li].height = 18
    else:
        wl.cell(row=3, column=1,
                value='Справочник итогов пуст. Добавьте значения в разделе «Справочники».')

    wl.column_dimensions['A'].width = 8
    wl.column_dimensions['B'].width = 36
    wl.column_dimensions['C'].width = 12

    fn = f"minek_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    fp = os.path.join(REPORTS_DIR, fn)
    wb.save(fp)

    detail = f"период: {_fmt_date(df)} – {_fmt_date(dt)}; всего {len(rows)} обращ."
    return fp, fn, detail


def build_full_wb() -> tuple:
    """Полная выгрузка базы. → (filepath, filename, log_detail)."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT r.*,
                   ass.full_name AS assigned_name,
                   st.name       AS subject_type_name,
                   rt.name       AS result_type_name
            FROM requests r
            LEFT JOIN users         ass ON r.assigned_to     = ass.id
            LEFT JOIN subject_types st  ON r.subject_type_id = st.id
            LEFT JOIN result_types  rt  ON r.result_type_id  = rt.id
            ORDER BY r.id
        """).fetchall()
    finally:
        conn.close()

    STATUS_EXPORT_MAP = {
        'draft':             'Черновик',
        'registered':        'Зарегистрировано',
        'in_progress':       'В работе',
        'under_review':      'На проверке',
        'ready_to_send':     'Готово к отправке',
        'sent_to_applicant': 'Документы отправлены',
        'closed':            'Закрыто',
    }

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'База обращений'

    hfill   = PatternFill("solid", fgColor="1B5E7B")
    id_fill = PatternFill("solid", fgColor="2E4057")
    hfont   = Font(bold=True, color="FFFFFF", size=10)
    br      = _std_border()

    # v3.8: site_area_ha → site_area_ha_min + site_area_ha_max
    #        site_build_area_m2 → site_build_area_m2_min + site_build_area_m2_max
    COLS = [
        ('id',                      'ID (не менять)'),
        ('request_number',          '№ обращения'),
        ('request_date',            'Дата обращения'),
        ('status',                  'Статус'),
        ('applicant_full_name',     'Полное наименование'),
        ('applicant_short_name',    'Краткое наименование'),
        ('applicant_inn',           'ИНН'),
        ('project_name',            'Название проекта'),
        ('contact_person',          'Контактное лицо'),
        ('contact_phone',           'Телефон'),
        ('contact_email',           'E-mail'),
        ('investment_total',        'Инвестиции (млн руб.)'),
        ('jobs_total',              'Рабочих мест'),
        ('site_area_ha_min',        'Площадь от (га)'),
        ('site_area_ha_max',        'Площадь до (га)'),
        ('site_build_area_m2_min',  'Застройка от (м²)'),
        ('site_build_area_m2_max',  'Застройка до (м²)'),
        ('preferred_districts',     'Районы'),
        ('source_type',             'Источник'),
        ('assigned_name',           'Ответственный'),
        ('subject_type_name',       'Предмет обращения'),
        ('feedback_date',           'Дата обратной связи'),
        ('result_type_name',        'Итоги работы'),
        ('incoming_number',         'Входящий номер'),
        ('answer_date',             'Дата ответа'),
        ('answer_method',           'Способ ответа'),
        ('answer_notes',            'Примечания к ответу'),
        ('additional_info',         'Доп. информация'),
    ]

    for ci, (field, header) in enumerate(COLS, 1):
        c = ws.cell(row=1, column=ci, value=header)
        c.fill = id_fill if field == 'id' else hfill
        c.font = hfont
        c.border = br
        c.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 36
    ws.freeze_panes = 'A2'

    for ri, r in enumerate(rows, 2):
        row_keys = list(r.keys())
        for ci, (field, _) in enumerate(COLS, 1):
            val = r[field] if field in row_keys else None
            if field == 'status' and val:
                val = STATUS_EXPORT_MAP.get(val, val)
            c = ws.cell(row=ri, column=ci, value=val)
            c.border = br
            c.alignment = Alignment(vertical='center', wrap_text=(ci == len(COLS)))

    col_widths = [8, 16, 14, 16, 35, 25, 14, 30, 22, 16, 24, 14, 12, 10, 10, 12, 12, 24, 16, 20, 22, 14, 28, 18, 14, 18, 28, 30]
    for ci, w in enumerate(col_widths[:len(COLS)], 1):
        ws.column_dimensions[get_column_letter(ci)].width = w

    fn = f"sonar_full_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    fp = os.path.join(REPORTS_DIR, fn)
    wb.save(fp)

    return fp, fn, f'Полная выгрузка базы: {len(rows)} обращений'
