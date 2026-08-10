"""Нормализация заголовков и геоданных выгрузки инвестиционных площадок."""

from __future__ import annotations

from typing import Any


_EXPORT_FIELD_ALIASES = {
    'Порядок определения стоимости (для всех форм сделки)': 'Порядок определения стоимости',
    'Стоимость, руб./год за кв. м': 'Стоимость, руб./год за кв.м.',
    'Иные характеристики (транспортная доступность)': 'Иные характеристики',
}

_LATITUDE_FIELD = 'Широта объекта в координатах WGS-84'
_LONGITUDE_FIELD = 'Долгота объекта в координатах WGS-84'
_LINE_FIELD = 'Набор координат линии объекта в координатах WGS-84'
_POLYGON_FIELD = 'Набор координат полигона объекта в координатах WGS-84'


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip().casefold() in {'', 'none', 'null', 'nan'}


def _is_coordinate(value: Any, lower: float, upper: float) -> bool:
    if _is_blank(value):
        return False
    try:
        coordinate = float(str(value).strip().replace(',', '.'))
    except (TypeError, ValueError):
        return False
    return lower <= coordinate <= upper


def has_geoposition(row: dict[str, Any]) -> bool:
    """Возвращает True при наличии корректной геопривязки в выгрузке."""
    latitude_ok = _is_coordinate(row.get(_LATITUDE_FIELD), -90, 90)
    longitude_ok = _is_coordinate(row.get(_LONGITUDE_FIELD), -180, 180)
    return (
        (latitude_ok and longitude_ok)
        or not _is_blank(row.get(_LINE_FIELD))
        or not _is_blank(row.get(_POLYGON_FIELD))
    )


def normalize_export_row(row: dict[str, Any]) -> dict[str, Any]:
    """Добавляет расчётные ключи, совместимые с текущей формулой V2."""
    normalized = dict(row)
    for target_field, source_field in _EXPORT_FIELD_ALIASES.items():
        if _is_blank(normalized.get(target_field)):
            normalized[target_field] = normalized.get(source_field, '')

    if _is_blank(normalized.get('Геопривязка')) and has_geoposition(normalized):
        normalized['Геопривязка'] = 'Заполнено по координатам WGS-84'
    return normalized


def normalize_export_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Нормализует строки одной выгрузки, не изменяя исходные словари."""
    return [normalize_export_row(row) for row in rows]
