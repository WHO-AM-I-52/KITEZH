"""Преобразование API-snapshot Инвесткарты РФ в canonical-поля V2."""

from __future__ import annotations

import json
from typing import Any


_EMPTY_TEXT_VALUES = frozenset({"", "none", "null", "nan"})


def _is_blank(value: Any) -> bool:
    return (
        value is None
        or (
            isinstance(value, str)
            and value.strip().casefold() in _EMPTY_TEXT_VALUES
        )
    )


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _first_non_blank(*values: Any) -> Any:
    for value in values:
        if not _is_blank(value):
            return value
    return ""


def _list_to_text(value: Any, *, item_key: str | None = None) -> str:
    if not isinstance(value, list):
        return _as_text(value)

    result: list[str] = []
    for item in value:
        if isinstance(item, dict):
            item = item.get(item_key, "") if item_key else ""
        text = _as_text(item)
        if text:
            result.append(text)
    return ", ".join(result)


def _parse_payload(payload_json: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(payload_json, dict):
        return dict(payload_json)

    if not isinstance(payload_json, str) or not payload_json.strip():
        return {}

    try:
        parsed = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError):
        return {}

    return parsed if isinstance(parsed, dict) else {}


def _valid_coordinate(value: Any, lower: float, upper: float) -> bool:
    if _is_blank(value):
        return False

    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return False

    return lower <= number <= upper


def _set_if_present(result: dict[str, Any], field: str, value: Any) -> None:
    if not _is_blank(value):
        result[field] = value


def _append_contact_parts(payload: dict[str, Any]) -> str:
    values = [
        payload.get("contactPerson"),
        payload.get("contactPhoneNumber"),
        payload.get("emailAddressForApplying"),
    ]
    return "\n".join(
        text for text in (_as_text(value) for value in values) if text
    )


def _add_resource_fields(
    result: dict[str, Any],
    resource: dict[str, Any],
    *,
    title: str,
    unit: str,
    capacity_title: str,
) -> None:
    _set_if_present(result, f"{title} — Наличие", resource.get("availability"))
    _set_if_present(
        result,
        f"{title} — Тариф на потребление, руб./{unit}",
        resource.get("tariffConsumption"),
    )
    _set_if_present(
        result,
        f"{title} — Тариф на транспортировку, руб./{unit}",
        resource.get("tariffTransportation"),
    )
    _set_if_present(
        result,
        f"{title} — Тариф на технологическое присоединение, руб.",
        _first_non_blank(
            resource.get("connectionFeeMin"),
            resource.get("connectionFeeMax"),
        ),
    )
    _set_if_present(
        result,
        f"Объекты {capacity_title} — Максимально допустимая мощность, {unit}/ч",
        resource.get("availableCapacity"),
    )
    _set_if_present(
        result,
        f"Объекты {capacity_title} — Свободная мощность, {unit}/ч",
        resource.get("freePower"),
    )
    _set_if_present(
        result,
        f"Сети {capacity_title} — Пропускная способность, {unit}/ч",
        resource.get("bandwidth"),
    )
    _set_if_present(
        result,
        f"Объекты {capacity_title} — Иные характеристики",
        resource.get("otherFreePower"),
    )


def api_snapshot_to_portal_row(
    payload_json: str | dict[str, Any] | None,
) -> dict[str, Any]:
    """Возвращает V2-совместимые поля из JSON актуального API-snapshot."""
    payload = _parse_payload(payload_json)
    result: dict[str, Any] = {}

    direct_fields = {
        "siteName": "Название площадки",
        "siteStatus": "Статус площадки",
        "municipality": "Муниципальное образование",
        "adressObject": "Адрес объекта",
        "nearestCity": "Ближайший город",
        "nameOwner": "Наименование собственника / администратора объекта",
        "costObject": "Стоимость объекта, руб. (покупки или месячной аренды)",
        "costPerSqM": "Стоимость, руб./год за кв. м",
        "procedureDeterminingCostStr": (
            "Порядок определения стоимости (для всех форм сделки)"
        ),
        "descriptionApplicationProcedure": "Описание процедуры подачи заявки",
        "emailAddressForApplying": "Адрес эл. почты для подачи заявки",
        "accessRoadsAvailability": "Наличие подъездных путей",
        "railwayAvailability": "Наличие ж/д",
        "truckParkingAvailability": "Наличие парковки грузового транспорта",
    }
    for source_field, target_field in direct_fields.items():
        _set_if_present(result, target_field, payload.get(source_field))

    _set_if_present(
        result,
        "min и max сроки аренды (если применимо), лет",
        _first_non_blank(
            payload.get("maxMinRentalYear"),
            payload.get("maxMinRentalPeriod"),
        ),
    )
    _set_if_present(
        result,
        "Телефон контактного лица, e-mail",
        _append_contact_parts(payload),
    )

    list_fields = {
        "formatSites": "Формат площадки",
        "typeSites": "Тип площадки",
        "formOwnerships": "Форма собственности объекта",
        "transactionForms": "Форма сделки",
        "preferentialTreatmentSites": "Преференциальный режим",
        "supportInfrastructureObjects": "Объект инфраструктуры поддержки",
        "photos": "Фотографии объекта",
    }
    for source_field, target_field in list_fields.items():
        _set_if_present(
            result,
            target_field,
            _list_to_text(payload.get(source_field)),
        )

    _set_if_present(
        result,
        "Перечень видов экономической деятельности, возможных к реализации на площадке",
        _list_to_text(
            payload.get("economicActivitiesForImplementations"),
            item_key="description",
        ),
    )

    coordinate = payload.get("coordinate")
    if isinstance(coordinate, dict) and (
        _valid_coordinate(coordinate.get("latitude"), -90, 90)
        and _valid_coordinate(coordinate.get("longitude"), -180, 180)
    ):
        result["Геопривязка"] = "Заполнено по координатам API"

    resource_specs = (
        ("waterSupply", "Водоснабжение", "куб. м", "водоснабжения"),
        ("waterDisposal", "Водоотведение", "куб. м", "водоотведения"),
        ("gasSupply", "Газоснабжение", "куб. м", "газоснабжения"),
        ("powerSupply", "Электроснабжение", "МВт", "электроснабжения"),
        ("heatSupply", "Теплоснабжение", "Гкал", "теплоснабжения"),
    )
    for source_field, title, unit, capacity_title in resource_specs:
        resource = payload.get(source_field)
        if isinstance(resource, dict):
            _add_resource_fields(
                result,
                resource,
                title=title,
                unit=unit,
                capacity_title=capacity_title,
            )

    return result


def merge_missing_portal_values(
    base_row: dict[str, Any],
    api_row: dict[str, Any],
) -> dict[str, Any]:
    """Дополняет пустые V2-поля API-данными, не затирая значения XLSX."""
    merged = dict(base_row)
    for field, value in api_row.items():
        if _is_blank(merged.get(field)) and not _is_blank(value):
            merged[field] = value
    return merged
