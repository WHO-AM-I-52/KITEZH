import sqlite3

from portal_analysis.presentation import build_site_result


def _create_db() -> sqlite3.Connection:
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


def test_ai_text_contains_score_missing_and_partial_fields():
    conn = _create_db()

    site = build_site_result({
        'global_id': '1530550',
        'Название площадки': 'Тестовая площадка',
        'Статус площадки': 'Свободна',
        'Водоснабжение — Наличие': 'Возможно подключение',
    }, conn)

    assert site['included']
    assert 'ID: 1530550' in site['ai_text']
    assert f"Заполняемость V2: {site['score']}%." in site['ai_text']
    assert 'Заполнено:' in site['ai_text']
    assert site['missing'][0]['field'] in site['ai_text']

    assert site['partial']
    assert site['partial'][0]['field'] == 'Водоснабжение — Наличие'
    assert 'Требуют уточнения:' in site['ai_text']
    assert 'Частично заполнено:' in site['ai_text']

    conn.close()


def test_excluded_site_has_reason_and_no_score():
    conn = _create_db()

    site = build_site_result({
        'global_id': '1530551',
        'Название площадки': 'Проданная площадка',
        'Статус площадки': 'Продана',
    }, conn)

    assert not site['included']
    assert site['score'] is None
    assert site['partial'] == []
    assert 'Учитывается в оценке: нет (продана).' in site['ai_text']

    conn.close()
