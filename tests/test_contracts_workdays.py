# ╔══════════════════════════════════════════════════════════════╗
# ║        tests/test_contracts_workdays.py                       ║
# ║  Проверки расчёта сроков: рабочие, календарные, календарь.   ║
# ╚══════════════════════════════════════════════════════════════╝

import sys
import os
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from contracts.workdays import (
    add_days, calendar_deadline, workday_deadline, fmt, is_workday,
)


def test_workdays_skip_weekend():
    # Пт 13.11.2026 + 5 раб. дней: 16,17,18,19,20 -> Пт 20.11.2026
    assert workday_deadline(date(2026, 11, 13), 5) == date(2026, 11, 20)


def test_workdays_one_day():
    # Чт + 1 раб. день = Пт
    assert workday_deadline(date(2026, 11, 12), 1) == date(2026, 11, 13)


def test_workdays_over_friday():
    # Пт + 1 раб. день = Пн
    assert workday_deadline(date(2026, 11, 13), 1) == date(2026, 11, 16)


def test_calendar_days():
    assert calendar_deadline(date(2026, 11, 13), 5) == date(2026, 11, 18)


def test_day_type_work():
    assert add_days(date(2026, 11, 13), 5, 'work') == date(2026, 11, 20)
    assert add_days(date(2026, 11, 13), 5, 'cal') == date(2026, 11, 18)


def test_holiday_in_calendar():
    cal = {'2027-01-08': 'holiday'}
    # Чт 07.01.2027 + 1: пт 08.01 праздничный -> Пн 11.01
    assert add_days(date(2027, 1, 7), 1, calendar=cal) == date(2027, 1, 11)


def test_working_saturday_in_calendar():
    cal = {'2027-01-09': 'workday'}
    # Пт 08.01 + 1 раб. день: пт 08.01 - праздничный, сб 09.01 - рабочий
    cal['2027-01-08'] = 'holiday'
    assert add_days(date(2027, 1, 7), 1, calendar=cal) == date(2027, 1, 9)


def test_string_start_date():
    assert workday_deadline('2026-11-13', 5) == date(2026, 11, 20)


def test_zero_days_raises():
    for fn in (calendar_deadline, workday_deadline):
        try:
            fn(date(2026, 11, 13), 0)
        except ValueError:
            pass
        else:
            raise AssertionError('Ожидалась ошибка при 0 дней')


def test_fmt():
    assert fmt(date(2026, 11, 16)) == '16.11.2026'
    assert fmt(None) == ''


def test_is_workday_weekend():
    assert is_workday(date(2026, 11, 14)) is False  # суббота
    assert is_workday(date(2026, 11, 15)) is False  # воскресенье
    assert is_workday(date(2026, 11, 16)) is True
