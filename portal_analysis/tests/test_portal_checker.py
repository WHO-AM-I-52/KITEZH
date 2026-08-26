# ╔══════════════════════════════════════════════════════════════════════╗
# ║           portal_analysis/tests/test_portal_checker.py             ║
# ║  Тесты логики проверки заполняемости                               ║
# ╚══════════════════════════════════════════════════════════════════════╝
"""
Запуск:
    python -m pytest portal_analysis/tests/
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
import sqlite3

from portal_analysis.portal_checker import calc_portal_score_v2
from portal_analysis.portal_checker import _is_empty, _strip_html, calc_portal_score
from portal_analysis.portal_checker import (
    _classify_value,
    _is_empty,
    _strip_html,
    calc_portal_score,
)

def _create_v2_db() -> sqlite3.Connection:
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row

    conn.execute(
        '''
        CREATE TABLE investmap_fields (
            tech_name TEXT,
            display_name TEXT
        )
        '''
    )
    conn.execute(
        '''
        CREATE TABLE investmap_rules (
            source_field TEXT,
            source_value TEXT,
            target_field TEXT,
            recommended_text TEXT
        )
        '''
    )

    return conn

def test_classify_value_missing():
    assert _classify_value('Не применимо.') == 'missing'


def test_classify_value_placeholder():
    assert _classify_value('Требуется получение ТУ') == 'placeholder'


def test_classify_value_simple_connection_is_placeholder():
    assert _classify_value('Возможно подключение') == 'placeholder'


def test_classify_value_detailed_connection_is_declared():
    assert _classify_value(
        'Возможно подключение от ТП 1212 6 кВ '
        'на расстоянии 600 метров от площадки.'
    ) == 'declared'

def test_v2_placeholder_has_quarter_weight():
    conn = _create_v2_db()

    result = calc_portal_score_v2({
        'Название площадки': 'Тестовая площадка',
        'Водоснабжение — Наличие': 'Возможно подключение',
    }, conn)

    partial = next(
        item
        for item in result['partial']
        if item['field'] == 'Водоснабжение — Наличие'
    )

    assert partial['weight'] == 0.25
    assert result['effective_filled'] == result['filled'] - 0.75
    assert result['score'] < round(
        100 * result['filled'] / result['total']
    )

    conn.close()

def test_v2_placeholder_has_quarter_weight():
    conn = _create_v2_db()

    result = calc_portal_score_v2({
        'Название площадки': 'Тестовая площадка',
        'Водоснабжение — Наличие': 'Возможно подключение',
    }, conn)

    partial = next(
        item
        for item in result['partial']
        if item['field'] == 'Водоснабжение — Наличие'
    )

    assert partial['weight'] == 0.25
    assert result['effective_filled'] == result['filled'] - 0.75
    assert result['score'] < round(
        100 * result['filled'] / result['total']
    )

    conn.close()

# ── _strip_html ──────────────────────────────────────────────────────────────

def test_strip_html_removes_tags():
    assert _strip_html('<p>Не применимо.</p>') == 'Не применимо.'

def test_strip_html_no_tags():
    assert _strip_html('Обычный текст') == 'Обычный текст'

def test_strip_html_empty():
    assert _strip_html('') == ''


# ── _is_empty ────────────────────────────────────────────────────────────────

def test_is_empty_none():
    assert _is_empty(None) is True

def test_is_empty_blank():
    assert _is_empty('') is True

def test_is_empty_html_wrapped_irrelevant():
    assert _is_empty('<p>Не применимо.</p>') is True

def test_is_empty_real_value():
    assert _is_empty('Территория опережающего развития') is False

def test_is_empty_net_is_not_empty():
    # «Нет» — содержательный ответ для dropdown, не пустое
    assert _is_empty('Нет') is False


# ── Условные поля ────────────────────────────────────────────────────────────
# TODO: добавить тесты после заполнения PORTAL_FIELDS и CONDITIONAL_SKIP

def test_skip_pref_regime_otsutstvuet():
    """
    Если Преф. режим = Отсутствует — дочерние поля пропускаются.
    TODO: расширить после уточнения полного списка полей.
    """
    pass


def test_jd_net_no_child_fields_required():
    """
    Если Наличие ж/д = Нет — дочерние поля не нужны.
    TODO: расширить после уточнения дочерних полей ж/д.
    """
    pass
