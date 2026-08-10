from portal_analysis.export_normalizer import has_geoposition, normalize_export_row


def test_normalize_real_export_headers():
    row = {
        'Порядок определения стоимости': 'По результатам торгов',
        'Стоимость, руб./год за кв.м.': '1200',
        'Иные характеристики': 'Подъезд с региональной трассы',
    }

    normalized = normalize_export_row(row)

    assert normalized['Порядок определения стоимости (для всех форм сделки)'] == 'По результатам торгов'
    assert normalized['Стоимость, руб./год за кв. м'] == '1200'
    assert normalized['Иные характеристики (транспортная доступность)'] == 'Подъезд с региональной трассы'


def test_geoposition_uses_valid_latitude_and_longitude():
    row = {
        'Широта объекта в координатах WGS-84': '56,3269',
        'Долгота объекта в координатах WGS-84': '44.0059',
    }

    assert has_geoposition(row)
    assert normalize_export_row(row)['Геопривязка'] == 'Заполнено по координатам WGS-84'


def test_geoposition_requires_both_coordinates_when_no_geometry():
    row = {'Широта объекта в координатах WGS-84': '56.3269'}

    assert not has_geoposition(row)
    assert 'Геопривязка' not in normalize_export_row(row)


def test_geoposition_accepts_polygon_and_keeps_existing_value():
    polygon_row = {'Набор координат полигона объекта в координатах WGS-84': '56.3,44.0;56.4,44.1'}
    existing_row = {'Геопривязка': '55.0, 44.0'}

    assert has_geoposition(polygon_row)
    assert normalize_export_row(polygon_row)['Геопривязка'] == 'Заполнено по координатам WGS-84'
    assert normalize_export_row(existing_row)['Геопривязка'] == '55.0, 44.0'
