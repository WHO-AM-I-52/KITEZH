"""Чтение двухлистовой книги справочника без обращения к БД."""
from decimal import Decimal, InvalidOperation

from openpyxl import load_workbook

ORG_HEADERS = (
    'Действие', 'ID организации', 'Название', 'Адрес', 'ИНН организации',
)
CONTACT_HEADERS = (
    'Действие', 'ID контакта', 'ID организации', 'Организация', 'ФИО',
    'Должность', 'Кабинет', 'Рабочий тел.', 'Доб.', 'Личный тел.',
    'Email', 'ИНН контакта', 'Примечания',
)
SHEETS = {
    'Организации': (ORG_HEADERS, (
        'action', 'id', 'name', 'address', 'inn',
    )),
    'Контакты': (CONTACT_HEADERS, (
        'action', 'id', 'org_id', 'org_name', 'full_name', 'position',
        'room', 'phone_work', 'phone_ext', 'phone_personal',
        'email', 'inn', 'notes',
    )),
}


def _text(value):
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _id(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError('ID должен быть положительным целым числом')
    try:
        number = Decimal(str(value).strip())
        if not number.is_finite() or number != number.to_integral_value():
            raise ValueError
        if number <= 0 or number > 9223372036854775807:
            raise ValueError
        return int(number)
    except (InvalidOperation, ValueError):
        raise ValueError('ID должен быть положительным целым числом') from None


def parse_phonebook_workbook(source):
    """Принимает путь/бинарный поток; возвращает rows/errors/warnings.

    Заголовки находятся в строке 1. Невалидные строки исключаются.
    При непустом errors весь пакет должен быть заблокирован вызывающим кодом.
    """
    result = {'organizations': [], 'contacts': [], 'errors': [], 'warnings': []}

    def message(bucket, sheet, row, field, text):
        result[bucket].append({
            'sheet': sheet, 'row': row, 'field': field, 'message': text,
        })

    try:
        wb = load_workbook(source, read_only=True, data_only=False)
    except Exception as exc:
        message('errors', '', None, '', f'Не удалось открыть Excel: {exc}')
        return result
    try:
        for sheet, (headers, keys) in SHEETS.items():
            if sheet not in wb.sheetnames:
                message('errors', sheet, None, '', 'Отсутствует обязательный лист')
                continue
            ws = wb[sheet]
            iterator = ws.iter_rows()
            first = next(iterator, ())
            actual = tuple(_text(c.value) for c in first)
            if actual[:len(headers)] != headers or any(actual[len(headers):]):
                message('errors', sheet, 1, '', 'Заголовки не совпадают с форматом')
                continue
            seen = set()
            bucket = 'organizations' if sheet == 'Организации' else 'contacts'
            for row_number, cells in enumerate(iterator, 2):
                if all(c.value is None or c.value == '' for c in cells):
                    continue
                before = len(result['errors'])
                if any(c.value is not None and c.value != '' for c in cells[len(keys):]):
                    message('errors', sheet, row_number, '', 'Данные за пределами колонок')
                values = {}
                for index, key in enumerate(keys):
                    cell = cells[index] if index < len(cells) else None
                    value = cell.value if cell else None
                    field = headers[index]
                    if cell and cell.data_type in ('f', 'e'):
                        message('errors', sheet, row_number, field, 'Формулы и ошибки Excel запрещены')
                    if key in ('id', 'org_id'):
                        try:
                            values[key] = _id(value)
                        except ValueError as exc:
                            values[key] = None
                            message('errors', sheet, row_number, field, str(exc))
                    else:
                        values[key] = _text(value)
                        if cell and cell.data_type == 'n' and value is not None and key in (
                            'inn', 'phone_work', 'phone_ext', 'phone_personal',
                        ):
                            message('warnings', sheet, row_number, field,
                                    'Числовая ячейка: проверьте ведущие нули и исходное значение')
                action = values['action'].casefold()
                if action not in ('', 'удалить'):
                    message('errors', sheet, row_number, headers[0],
                            'Допустимо пустое действие или «Удалить»')
                values['action'] = 'delete' if action == 'удалить' else 'auto'
                record_id = values['id']
                if record_id is not None:
                    if record_id in seen:
                        message('errors', sheet, row_number, headers[1], 'Повторяющийся ID')
                    seen.add(record_id)
                if values['action'] == 'delete':
                    if record_id is None:
                        message('errors', sheet, row_number, headers[1], 'Для удаления нужен ID')
                else:
                    required = 'name' if bucket == 'organizations' else 'full_name'
                    if not values[required]:
                        message('errors', sheet, row_number, headers[keys.index(required)],
                                'Обязательное поле не заполнено')
                if bucket == 'organizations':
                    values['inn'] = values['inn'] or None
                values['row'] = row_number
                if len(result['errors']) == before:
                    result[bucket].append(values)
    finally:
        wb.close()
    return result
