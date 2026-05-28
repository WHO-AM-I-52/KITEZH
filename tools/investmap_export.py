# ╔══════════════════════════════════════════════════════════════╗
# ║               tools/investmap_export.py                     ║
# ║  Конвертер выгрузки ГИС НСИ (инвестплощадки) → текст + dict ║
# ║  Формат 1: 1 площадка (атрибут|значение по строкам)         ║
# ║  Формат 2: N площадок (строки=площадки, столбцы=атрибуты)   ║
# ║  Формат 3: 1 площадка ГИС НСИ (шапка на стр.2, 3 столбца)  ║
# ╚══════════════════════════════════════════════════════════════╝

import openpyxl
import io
import re
import html

# Поля, которые ПОЛНОСТЬЮ исключаются из data и не передаются анализатору:
# (даты, типы изменений, пользователь — технический мусор СИСТЕМЫ)
SERVICE_FIELDS = {
    'код во внешнем источнике',
    'тип последнего изменения',
    'дата последнего изменения',
    'дата создания',
    'пользователь/система, производивший изменение',
    'пользователь/система, производивший изменения',
    # global_id — НЕ фильтруем: он нужен анализатору для идентификации площадки в SMS
}


def _clean(val):
    """Нормализация значения ячейки: HTML-entities, теги, пробелы."""
    if val is None:
        return ''
    s = str(val).strip()
    s = re.sub(r'<[^>]+>', '', s)
    s = html.unescape(s)
    s = s.replace('\u00a0', ' ').strip()
    return s


def _is_archive(attr_name):
    """Поле с (архив) в названии — полностью игнорировать."""
    return '(архив)' in attr_name.lower()


def _is_service(attr_name):
    """Служебное поле системы — исключить из data."""
    return attr_name.lower().strip() in SERVICE_FIELDS


def _to_empty(val):
    """Пустые/недопустимые значения → ПУСТО."""
    if not val:
        return 'ПУСТО'
    lower = val.lower().strip()
    if lower in ('-', '—', 'нет данных', 'н/д', 'не указано', 'не заполнено'):
        return 'ПУСТО'
    return val


def _detect_format(ws):
    max_col = ws.max_column
    max_row = ws.max_row

    if max_col <= 4 and max_row >= 3:
        row2 = [_clean(c.value).lower() for c in next(ws.iter_rows(min_row=2, max_row=2))]
        row2_text = ' '.join(row2)
        if 'атрибут' in row2_text or 'значени' in row2_text:
            return 'f3'

    if max_col <= 2 and max_row >= 3:
        return 'f1'

    return 'f2'


def _build_output(attr, val):
    """Возвращает (текстовая строка, val_normalized) или None если поле пропустить."""
    if _is_archive(attr) or _is_service(attr):
        return None
    val_norm = _to_empty(_clean(val) if val else '')
    return f"{attr} → {val_norm}", val_norm


def parse_format1(ws):
    lines = []
    data = {}
    for row in ws.iter_rows(min_row=1):
        cells = [_clean(c.value) for c in row]
        attr = cells[0] if len(cells) > 0 else ''
        val  = cells[1] if len(cells) > 1 else ''
        if not attr:
            continue
        result = _build_output(attr, val)
        if result is None:
            continue
        line, val_norm = result
        lines.append(line)
        data[attr] = val_norm
    return lines, data


def parse_format3(ws):
    """
    Формат 3: 1 площадка ГИС НСИ.
    Строка 1 — заголовок каталога (пропускаем).
    Строка 2 — шапка колонок (пропускаем).
    Строки 3+: col A = атрибут, col B = описание, col C = значение.
    """
    lines = []
    data = {}
    for row in ws.iter_rows(min_row=3):
        cells = [_clean(c.value) for c in row]
        attr = cells[0] if len(cells) > 0 else ''
        val  = cells[2] if len(cells) > 2 else ''
        if not attr:
            continue
        result = _build_output(attr, val)
        if result is None:
            continue
        line, val_norm = result
        lines.append(line)
        data[attr] = val_norm
    return lines, data


def parse_format2(ws):
    """
    Формат 2: N площадок.
    Строка 1 = заголовки атрибутов. Строки 2+ = данные.
    """
    headers = []
    for cell in next(ws.iter_rows(min_row=1, max_row=1)):
        headers.append(_clean(cell.value))

    blocks = []
    for row_idx, row in enumerate(ws.iter_rows(min_row=2), start=2):
        cells = [_clean(c.value) for c in row]
        if not any(cells):
            continue
        lines = [f"=== ПЛОЩАДКА {row_idx - 1} ==="]
        data = {}
        for h, v in zip(headers, cells):
            if not h:
                continue
            result = _build_output(h, v)
            if result is None:
                continue
            line, val_norm = result
            lines.append(line)
            data[h] = val_norm
        blocks.append((lines, data))
    return blocks


def convert_excel_to_text(file_bytes):
    """
    Основная функция.
    Принимает bytes файла .xlsx.
    Возвращает dict:
      {
        'format': 1, 2 или 3,
        'count': количество площадок,
        'text': итоговый текст для вставки в чат,
        'data': dict {атрибут: значение} для f1/f3, list[dict] для f2,
        'error': None или строка ошибки
      }
    """
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
        ws = wb.active
        fmt = _detect_format(ws)

        if fmt == 'f3':
            lines, data = parse_format3(ws)
            return {'format': 3, 'count': 1, 'text': '\n'.join(lines), 'data': data, 'error': None}

        elif fmt == 'f1':
            lines, data = parse_format1(ws)
            return {'format': 1, 'count': 1, 'text': '\n'.join(lines), 'data': data, 'error': None}

        else:
            blocks = parse_format2(ws)
            text = '\n\n'.join(['\n'.join(b[0]) for b in blocks])
            data_list = [b[1] for b in blocks]
            return {'format': 2, 'count': len(blocks), 'text': text, 'data': data_list, 'error': None}

    except Exception as e:
        return {'format': None, 'count': 0, 'text': '', 'data': {}, 'error': str(e)}
