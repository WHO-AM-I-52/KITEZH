from portal_analysis.presentation import build_site_result


def test_ai_text_contains_score_and_required_fields():
    site = build_site_result({'global_id': '1530550', 'Название площадки': 'Тестовая площадка', 'Статус площадки': 'Свободна'})

    assert site['included']
    assert 'ID: 1530550' in site['ai_text']
    assert f"Заполняемость V2: {site['score']}%." in site['ai_text']
    assert 'Заполнено:' in site['ai_text']
    assert site['missing'][0] in site['ai_text']


def test_excluded_site_has_reason_and_no_score():
    site = build_site_result({'global_id': '1530551', 'Название площадки': 'Проданная площадка', 'Статус площадки': 'Продана'})

    assert not site['included']
    assert site['score'] is None
    assert 'Учитывается в оценке: нет (продана).' in site['ai_text']
