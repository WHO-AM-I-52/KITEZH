# ╔═════════════════════════════════════════════════════════════════════════════╗
# ║                       export_helpers.py                                      ║
# ║  Константы и чистые вспомогательные функции для экспорта/импорта Excel.       ║
# ║  Выделено из export_routes.py (декомпозиция, refactor/structure).            ║
# ╚═════════════════════════════════════════════════════════════════════════════╝

from datetime import datetime, date, timedelta
import re

from openpyxl.styles import Border, Side


# Датовые поля — нормализуем через _parse_date_for_db
DATE_FIELDS = {'request_date', 'answer_date', 'feedback_date'}

# Числовые поля — нормализуем через _parse_numeric_for_db
# v3.8: site_area_ha/site_build_area_m2 заменены на _min/_max
NUMERIC_FIELDS = {
    'investment_total', 'jobs_total',
    'site_area_ha_min', 'site_area_ha_max',
    'site_build_area_m2_min', 'site_build_area_m2_max',
}

# Обязательные поля для создания новой записи при импорте (3В-2)
REQUIRED_FOR_CREATE = ('applicant_full_name', 'request_date')

# Маппинг человекочитаемых статусов из Excel → внутренние коды (импорт)
STATUS_IMPORT_MAP = {
    'Черновик':             'draft',
    'Зарегистрировано':      'registered',
    'В работе':              'in_progress',
    'На проверке':           'under_review',
    'Готово к отправке':     'ready_to_send',
    'Документы отправлены':  'sent_to_applicant',
    'Закрыто':               'closed',
}

# ─── КОНСТАНТЫ ВАЛИДАЦИИ ПЛОЩАДОК ГИС НСИ ────────────────────────────────────

# БАГ-6: плата ниже порога считается заглушкой
STUB_PAYMENT_MAX = 100

# БАГ-8: ВРИ несовместимые с категорией «земли сельскохозяйственного назначения»
VRI_INCOMPATIBLE_WITH_AGRI = {
    'Коммунальное обслуживание',
    'Производственная деятельность',
    'Склады',
    'Тяжелая промышленность',
}

# БАГ-13: виды деятельности, несовместимые с ВРИ «Коммунальное обслуживание»
PRODUCTION_ACTIVITY_KEYWORDS = (
    'производств', 'завод', 'фабрик', 'переработк', 'промышленн',
    'склад', 'логистик', 'добыч',
)

# БАГ-10: ключевые слова для детектирования текста про дорогу в поле ТКО
ROAD_KEYWORDS_IN_TKO = (
    'дорог', 'асфальт', 'подъезд', 'автодорог', 'проезд',
)


# ─── ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ────────────────────────────────────────────────────────

def _short_fio(full_name: str) -> str:
    if not full_name:
        return '—'
    parts = full_name.strip().split()
    if len(parts) >= 3:
        return f"{parts[0]} {parts[1][0].upper()}.{parts[2][0].upper()}."
    if len(parts) == 2:
        return f"{parts[0]} {parts[1][0].upper()}."
    return full_name


def _std_border(color='CCCCCC'):
    s = Side(style='thin', color=color)
    return Border(left=s, right=s, top=s, bottom=s)


def _hex_to_argb(hex_color: str) -> str:
    c = hex_color.lstrip('#').upper() if hex_color else ''
    if len(c) == 6:
        return 'FF' + c
    if len(c) == 8:
        return c
    return ''


def _fmt_date(iso) -> str:
    """ISO-дата или datetime → DD.MM.YYYY для вывода."""
    if not iso:
        return '—'
    if isinstance(iso, (date, datetime)):
        return iso.strftime('%d.%m.%Y')
    s = str(iso).strip()[:10]
    try:
        return datetime.strptime(s, '%Y-%m-%d').strftime('%d.%m.%Y')
    except Exception:
        return s or '—'


def _parse_date_for_db(val) -> str | None:
    """Любое представление даты из Excel → YYYY-MM-DD или None."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime('%Y-%m-%d')
    if isinstance(val, date):
        return val.strftime('%Y-%m-%d')
    if isinstance(val, (int, float)):
        serial = int(val)
        if 1 <= serial <= 2958465:
            try:
                return (datetime(1899, 12, 30) + timedelta(days=serial)).strftime('%Y-%m-%d')
            except Exception:
                return None
        return None
    s = str(val).strip()
    if not s or s.lower() in ('none', 'null', '—', '-'):
        return None
    FMTS = [
        '%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M', '%-d.%-m.%Y %H:%M:%S',
        '%d.%m.%Y', '%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y', '%Y.%m.%d',
    ]
    for fmt in FMTS:
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    if len(s) >= 10 and s[4] == '-':
        try:
            return datetime.strptime(s[:10], '%Y-%m-%d').strftime('%Y-%m-%d')
        except ValueError:
            pass
    return None


def _is_empty_cell(val) -> bool:
    """True если ячейка пустая / заполнитель — не нужно писать ошибку."""
    if val is None:
        return True
    s = str(val).strip().lower()
    return s in ('', 'none', 'null', '—', '-')


def _parse_numeric_for_db(val, field: str) -> tuple:
    """Значение из Excel → число для БД или (None, сообщение об ошибке)."""
    if val is None:
        return None, None
    if isinstance(val, bool):
        return None, f'поле «{field}»: булево значение не является числом'
    if isinstance(val, (int, float)):
        if field == 'jobs_total':
            return int(round(val)), None
        return val, None
    s = str(val).strip()
    if not s or s.lower() in ('—', '-', 'none', 'null', 'н/д', 'нет'):
        return None, None
    s = re.sub(r'[^\d\s,.].*$', '', s).strip()
    s = re.sub(r'[\s\xa0]+', '', s)
    s = s.replace(',', '.')
    parts = s.split('.')
    if len(parts) > 2:
        s = ''.join(parts[:-1]) + '.' + parts[-1]
    if not s:
        return None, None
    try:
        num = float(s)
        if field == 'jobs_total':
            return int(round(num)), None
        return num, None
    except ValueError:
        return None, f'поле «{field}»: не удалось распознать число из {val!r}'


def _mln_to_mld(val) -> str:
    if val is None or val == '':
        return '—'
    try:
        mld = float(str(val).replace(',', '.')) / 1000
        return f"{mld:.3f}".rstrip('0').rstrip('.')
    except (ValueError, TypeError):
        return str(val)


def _contact_cell(person: str, phone: str, email: str) -> str:
    parts = []
    if person and person.strip():
        parts.append(f"Ф.И.О. {person.strip()}")
    if phone and phone.strip():
        parts.append(f"Тел: {phone.strip()}")
    if email and email.strip():
        parts.append(email.strip())
    return '\n'.join(parts) if parts else '—'


def _apply_cell_value(field: str, cell_val, row_label: str, errors: list) -> tuple:
    """Универсальный конвертер значения ячейки → (значение_для_бд, успех)."""
    if field in DATE_FIELDS:
        # Пустая ячейка — не ошибка, просто пропускаем
        if _is_empty_cell(cell_val):
            return None, True
        parsed = _parse_date_for_db(cell_val)
        if parsed is None:
            errors.append(f'{row_label}: не удалось распознать дату в поле «{field}»: {cell_val!r}')
            return None, False
        return parsed, True
    if field in NUMERIC_FIELDS:
        num, err_msg = _parse_numeric_for_db(cell_val, field)
        if err_msg:
            errors.append(f'{row_label}: {err_msg}')
            return None, False
        return num, True
    return str(cell_val).strip(), True


def _gen_request_number(new_id: int) -> str:
    """3В-3: авто-генерация номера обращения ЗУ-{ID}-{ГГ}."""
    yy = datetime.now().strftime('%y')
    return f'ЗУ-{new_id}-{yy}'
