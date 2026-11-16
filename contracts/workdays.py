# ╔════════════════════════════════════════════════════════════════════════╗
# ║                          workdays.py                                     ║
# ║  Расчёт дат по рабочим и календарным дням с учётом                     ║
# ║  производственного календаря раздела «Договоры».                      ║
# ║                                                                        ║
# ║  Контракт: функции только вычисляют дату; SQL и файлы здесь нет.       ║
# ║  Существующая utils.form_utils.add_workdays() не меняется.             ║
# ╚══════════════════════════════════════════════════════════════════════╝

from datetime import date, timedelta


def _to_date(value) -> date:
    """Строка ГГГГ-ММ-ДД или date -> date."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def calendar_deadline(start, days: int) -> date:
    """Срок в календарных днях. days>=1; день события не считается."""
    if days <= 0:
        raise ValueError('Количество дней должно быть больше 0')
    return _to_date(start) + timedelta(days=days)


def is_workday(d: date, calendar=None) -> bool:
    """Пн-пт по умолчанию; календарь может переопределить любой день.

    calendar — отображение 'ГГГГ-ММ-ДД' -> 'holiday' | 'workday'.
    """
    key = d.isoformat()
    if calendar:
        kind = calendar.get(key)
        if kind == 'holiday':
            return False
        if kind == 'workday':
            return True
    return d.weekday() < 5


def workday_deadline(start, days: int, calendar=None) -> date:
    """Срок в рабочих днях от даты события.

    Считаются только рабочие дни; дата события не входит.
    Перенос: если срок выпал бы на нерабочий день — добавляются
    следующие рабочие дни до первого рабочего.
    """
    if days <= 0:
        raise ValueError('Количество дней должно быть больше 0')
    d = _to_date(start)
    left = days
    # Защита от бесконечного цикла при полностью «нерабочем» календаре.
    for _ in range(days + 4000):
        d += timedelta(days=1)
        if is_workday(d, calendar):
            left -= 1
            if left == 0:
                return d
    raise ValueError('Не удалось найти рабочую дату: проверьте производственный календарь')


def add_days(start, days: int, day_type: str = 'work', calendar=None) -> date:
    """Единая точка расчёта: 'work' — рабочие, 'cal' — календарные."""
    if day_type not in ('work', 'cal'):
        raise ValueError("Тип дней должен быть 'work' или 'cal'")
    if day_type == 'cal':
        return calendar_deadline(start, days)
    return workday_deadline(start, days, calendar)


def fmt(d) -> str:
    """Дата в виде ДД.ММ.ГГГГ для карточки и документов."""
    if d is None:
        return ''
    return _to_date(d).strftime('%d.%m.%Y')
