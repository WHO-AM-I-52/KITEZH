"""Read-only SQL-запросы для мониторинга API Инвестиционной карты РФ."""

from __future__ import annotations

import json
import sqlite3
from typing import Any
from portal_analysis.api_snapshot_normalizer import api_snapshot_to_portal_row
from portal_analysis.portal_checker import V2_FORMULA_VERSION, calc_portal_score_v2

def _json_or_none(value: str | None) -> Any:
    if value is None:
        return None

    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value

def _display_text(value: Any) -> str | None:
    """Возвращает очищенный текст для пользовательского представления."""
    if value is None:
        return None

    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None

    if isinstance(value, (int, float)):
        return str(value)

    return None


def _display_number(value: Any, suffix: str = "") -> str | None:
    """Форматирует число для карточки без технических представлений."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    if isinstance(value, float) and value.is_integer():
        value = int(value)

    text = f"{value:,}".replace(",", " ")
    return f"{text}{suffix}"


def _display_list(value: Any) -> list[str]:
    """Нормализует массив строк API для вывода в карточке."""
    if not isinstance(value, list):
        return []

    result: list[str] = []

    for item in value:
        text = _display_text(item)

        if text is not None:
            result.append(text)

    return result


def _display_coordinates(value: Any) -> dict[str, Any] | None:
    """Возвращает координаты только при наличии обеих числовых компонент."""
    if not isinstance(value, dict):
        return None

    latitude = value.get("latitude")
    longitude = value.get("longitude")

    if (
        isinstance(latitude, bool)
        or isinstance(longitude, bool)
        or not isinstance(latitude, (int, float))
        or not isinstance(longitude, (int, float))
    ):
        return None

    return {
        "latitude": latitude,
        "longitude": longitude,
        "map_url": (
            "https://yandex.ru/maps/?pt="
            f"{longitude},{latitude}&z=15&l=map"
        ),
    }


def _display_utility(
    payload: dict[str, Any],
    field_name: str,
) -> dict[str, str | None]:
    """Готовит сведения об одном виде инженерной инфраструктуры."""
    source = payload.get(field_name)

    if not isinstance(source, dict):
        source = {}

    availability = _display_text(source.get("availability"))
    capacity = _display_number(source.get("availableCapacity"))
    connection_fee = _display_number(
        source.get("connectionFeeMin"),
        " руб.",
    )
    tariff_consumption = _display_text(source.get("tariffConsumption"))
    tariff_transportation = _display_text(
        source.get("tariffTransportation")
    )
    details = _display_text(
        source.get("otherFreePower") or source.get("otherFree")
    )

    return {
        "availability": availability,
        "capacity": capacity,
        "connection_fee": connection_fee,
        "tariff_consumption": tariff_consumption,
        "tariff_transportation": tariff_transportation,
        "details": details,
    }
    
_HISTORY_FIELD_META: dict[str, tuple[str, str]] = {
    "/municipality": (
        "Расположение",
        "Муниципальное образование",
    ),
    "/siteName": (
        "Основные сведения",
        "Название площадки",
    ),
    "/adressObject": (
        "Расположение",
        "Адрес объекта",
    ),
    "/fillingLevel": (
        "Основные сведения",
        "Заполненность карточки",
    ),
    "/maxMinRentalPeriod": (
        "Условия сделки",
        "Срок аренды",
    ),
    "/maxMinRentalYear": (
        "Условия сделки",
        "Срок аренды",
    ),
    "/buildingSpecifications": (
        "Характеристики объекта",
        "Характеристики здания",
    ),
    "/characteristicsCapitalBuildings": (
        "Характеристики объекта",
        "Объекты капитального строительства",
    ),
    "/descriptionApplicationProcedure": (
        "Подача заявки",
        "Порядок подачи заявки",
    ),
    "/linkToApplicationForm": (
        "Подача заявки",
        "Ссылка на форму заявки",
    ),
    "/listOfDocumentsForApplication": (
        "Подача заявки",
        "Документы для подачи заявки",
    ),
    "/nearestCity": (
        "Расположение",
        "Ближайший город",
    ),
    "/notes": (
        "Дополнительные сведения",
        "Примечание",
    ),
    "/otherInformationSite": (
        "Дополнительные сведения",
        "Иная информация о площадке",
    ),
    "/urbanPlanCharacteristicsAndLimits": (
        "Градостроительные условия",
        "Характеристики и ограничения",
    ),
}


def _history_path_parts(field_path: Any) -> list[str]:
    """Разбирает JSON Pointer пути изменения на непустые компоненты."""
    if not isinstance(field_path, str):
        return []

    return [
        part.replace("~1", "/").replace("~0", "~")
        for part in field_path.split("/")
        if part
    ]

_HISTORY_ROOT_FIELD_META: dict[str, tuple[str, str]] = {
    "siteStatus": ("Основные сведения", "Статус площадки"),
    "typeSites": ("Основные сведения", "Тип площадки"),
    "formatSites": ("Основные сведения", "Формат площадки"),
    "formOwnerships": ("Основные сведения", "Форма собственности"),
    "regions": ("Расположение", "Регион"),
    "totalSiteArea": ("Основные сведения", "Площадь площадки"),
    "areaPropertyComplex": ("Характеристики объекта", "Площадь объекта"),
    "cadastralPropertyComplexNumber": (
        "Характеристики объекта",
        "Кадастровый номер",
    ),
    "costObject": ("Условия сделки", "Стоимость объекта"),
    "accessRoadsAvailability": (
        "Транспортная доступность",
        "Подъездные дороги",
    ),
    "accessRoadsOther": (
        "Транспортная доступность",
        "Описание подъезда",
    ),
    "distanceFromRoad": (
        "Транспортная доступность",
        "Расстояние до автодороги",
    ),
    "railwayAvailability": (
        "Транспортная доступность",
        "Железнодорожный доступ",
    ),
    "truckParkingAvailability": (
        "Транспортная доступность",
        "Стоянка грузового транспорта",
    ),
    "contactPerson": ("Контакты", "Контактное лицо"),
    "contactPhoneNumber": ("Контакты", "Телефон"),
    "emailAddressForApplying": ("Контакты", "E-mail для подачи заявки"),
    "websiteContactPerson": ("Контакты", "Сайт контактного лица"),
    "coordinate": ("Расположение", "Координаты"),
    "powerSupply": ("Инженерная инфраструктура", "Электроснабжение"),
    "gasSupply": ("Инженерная инфраструктура", "Газоснабжение"),
    "heatSupply": ("Инженерная инфраструктура", "Теплоснабжение"),
    "waterSupply": ("Инженерная инфраструктура", "Водоснабжение"),
    "waterDisposal": ("Инженерная инфраструктура", "Водоотведение"),
    "mswRemoval": ("Инженерная инфраструктура", "Вывоз ТКО"),
}

_HISTORY_UTILITY_FIELD_META: dict[str, str] = {
    "availability": "доступность",
    "availableCapacity": "доступная мощность",
    "connectionFeeMin": "стоимость подключения от",
    "tariffConsumption": "тариф потребления",
    "tariffTransportation": "тариф транспортировки",
    "otherFreePower": "дополнительные сведения о мощности",
    "otherFree": "дополнительные сведения",
}

_HISTORY_COORDINATE_FIELD_META: dict[str, str] = {
    "latitude": "широта",
    "longitude": "долгота",
}

def _history_field_presentation(field_path: Any) -> dict[str, str]:
    """Возвращает раздел и понятное название изменённого поля."""
    if not isinstance(field_path, str):
        return {
            "section": "Прочее",
            "label": "Неизвестное поле",
        }

    known = _HISTORY_FIELD_META.get(field_path)
    if known is not None:
        return {
            "section": known[0],
            "label": known[1],
        }

    parts = _history_path_parts(field_path)

    if not parts:
        return {
            "section": "Прочее",
            "label": "Неизвестное поле",
        }

    root = parts[0]

    if root == "photos":
        number = parts[1] if len(parts) > 1 else None
        position = (
            str(int(number) + 1)
            if number is not None and number.isdigit()
            else None
        )
        return {
            "section": "Материалы",
            "label": (
                f"Фотография № {position}"
                if position is not None
                else "Фотографии"
            ),
        }

    if root == "economicActivitiesForImplementations":
        number = parts[1] if len(parts) > 1 else None
        position = (
            str(int(number) + 1)
            if number is not None and number.isdigit()
            else None
        )
        field_name = {
            "code": "код ОКВЭД",
            "description": "описание ОКВЭД",
            "key": "технический идентификатор",
        }.get(parts[2] if len(parts) > 2 else None)

        if field_name is not None and position is not None:
            label = f"ОКВЭД: {field_name}, позиция № {position}"
        elif position is not None:
            label = f"ОКВЭД: позиция № {position}"
        else:
            label = "Рекомендуемые виды деятельности"

        return {
            "section": "Рекомендуемые виды деятельности",
            "label": label,
        }

    if root == "transactionForms":
        number = parts[1] if len(parts) > 1 else None
        position = (
            str(int(number) + 1)
            if number is not None and number.isdigit()
            else None
        )
        return {
            "section": "Условия сделки",
            "label": (
                f"Форма сделки: позиция № {position}"
                if position is not None
                else "Формы сделки"
            ),
        }
    root_meta = _HISTORY_ROOT_FIELD_META.get(root)

    if root_meta is not None:
        section, root_label = root_meta

        if root in {
            "powerSupply",
            "gasSupply",
            "heatSupply",
            "waterSupply",
            "waterDisposal",
            "mswRemoval",
        } and len(parts) > 1:
            property_label = _HISTORY_UTILITY_FIELD_META.get(parts[1])

            if property_label is not None:
                return {
                    "section": section,
                    "label": f"{root_label}: {property_label}",
                }

        if root == "coordinate" and len(parts) > 1:
            property_label = _HISTORY_COORDINATE_FIELD_META.get(parts[1])

            if property_label is not None:
                return {
                    "section": section,
                    "label": f"{root_label}: {property_label}",
                }

        if len(parts) == 1:
            return {
                "section": section,
                "label": root_label,
            }

    return {
        "section": "Дополнительные сведения",
        "label": "Дополнительное поле API",
    }


def _history_value_display(value: Any) -> str:
    """Возвращает краткое безопасное представление значения изменения."""
    if value is None:
        return "Не указано"

    if isinstance(value, bool):
        return "Да" if value else "Нет"

    if isinstance(value, str):
        normalized = value.strip()
        return normalized or "Не указано"

    if isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return str(value)

    if isinstance(value, list):
        if not value:
            return "Список пуст"
        return f"Список: {len(value)} эл."

    if isinstance(value, dict):
        if not value:
            return "Объект пуст"
        return f"Набор данных: {len(value)} полей"

    return str(value)


def _history_value_is_complex(value: Any) -> bool:
    """Определяет, нужны ли для значения раскрываемые технические детали."""
    return isinstance(value, (dict, list))


def _build_history_change(
    row: sqlite3.Row,
) -> dict[str, Any]:
    """Подготавливает изменение API-снимка для пользовательской истории."""
    old_value = _json_or_none(row["old_value_json"])
    new_value = _json_or_none(row["new_value_json"])
    presentation = _history_field_presentation(row["field_path"])

    return {
        "change_id": row["id"],
        "previous_snapshot_id": row["previous_snapshot_id"],
        "current_snapshot_id": row["current_snapshot_id"],
        "field_path": row["field_path"],
        "section": presentation["section"],
        "label": presentation["label"],
        "old_value": old_value,
        "new_value": new_value,
        "old_display": _history_value_display(old_value),
        "new_display": _history_value_display(new_value),
        "has_complex_value": (
            _history_value_is_complex(old_value)
            or _history_value_is_complex(new_value)
        ),
        "detected_at_utc": row["detected_at_utc"],
    }
_COMPARISON_MAX_CHANGES = 500
_MISSING = object()


def _json_pointer_escape(part: str) -> str:
    return part.replace("~", "~0").replace("/", "~1")


def _comparison_change_type(old_value: Any, new_value: Any) -> str:
    if old_value is _MISSING:
        return "added"

    if new_value is _MISSING:
        return "removed"

    return "changed"


def _comparison_change_type_display(change_type: str) -> str:
    return {
        "added": "Добавлено",
        "removed": "Удалено",
        "changed": "Изменено",
    }.get(change_type, "Изменено")


def _build_comparison_change(
    field_path: str,
    old_value: Any,
    new_value: Any,
) -> dict[str, Any]:
    presentation = _history_field_presentation(field_path)
    old_display_value = None if old_value is _MISSING else old_value
    new_display_value = None if new_value is _MISSING else new_value
    change_type = _comparison_change_type(old_value, new_value)

    return {
        "field_path": field_path,
        "section": presentation["section"],
        "label": presentation["label"],
        "change_type": change_type,
        "change_type_display": _comparison_change_type_display(change_type),
        "old_value": old_display_value,
        "new_value": new_display_value,
        "old_display": _history_value_display(old_display_value),
        "new_display": _history_value_display(new_display_value),
        "has_complex_value": (
            _history_value_is_complex(old_display_value)
            or _history_value_is_complex(new_display_value)
        ),
    }


def _collect_payload_changes(
    old_value: Any,
    new_value: Any,
    field_path: str = "",
    changes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if changes is None:
        changes = []

    if len(changes) >= _COMPARISON_MAX_CHANGES:
        return changes

    if old_value is _MISSING or new_value is _MISSING:
        changes.append(
            _build_comparison_change(
                field_path or "/",
                old_value,
                new_value,
            )
        )
        return changes

    if isinstance(old_value, dict) and isinstance(new_value, dict):
        keys = sorted(
            set(old_value) | set(new_value),
            key=str,
        )

        for key in keys:
            path = f"{field_path}/{_json_pointer_escape(str(key))}"
            _collect_payload_changes(
                old_value.get(key, _MISSING),
                new_value.get(key, _MISSING),
                path,
                changes,
            )

            if len(changes) >= _COMPARISON_MAX_CHANGES:
                break

        return changes

    if isinstance(old_value, list) and isinstance(new_value, list):
        items_count = max(len(old_value), len(new_value))

        for index in range(items_count):
            path = f"{field_path}/{index}"
            _collect_payload_changes(
                old_value[index] if index < len(old_value) else _MISSING,
                new_value[index] if index < len(new_value) else _MISSING,
                path,
                changes,
            )

            if len(changes) >= _COMPARISON_MAX_CHANGES:
                break

        return changes

    if old_value != new_value:
        changes.append(
            _build_comparison_change(
                field_path or "/",
                old_value,
                new_value,
            )
        )

    return changes


def _comparison_snapshot_summary(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "snapshot_id": row["id"],
        "fetched_at_utc": row["fetched_at_utc"],
        "filling_level": row["filling_level"],
    }


def _build_snapshot_comparison(
    snapshots: list[sqlite3.Row],
    from_snapshot_id: int | None = None,
    to_snapshot_id: int | None = None,
) -> dict[str, Any]:
    if len(snapshots) < 2:
        return {
            "status": "not_enough_snapshots",
            "message": (
                "Для сравнения требуется не менее двух "
                "сохранённых снимков."
            ),
            "previous": None,
            "current": None,
            "filling_level_delta": None,
            "changes": [],
            "is_truncated": False,
        }

    snapshot_by_id = {row["id"]: row for row in snapshots}
    default_previous = snapshots[1]
    default_current = snapshots[0]
    selected_previous = snapshot_by_id.get(
        from_snapshot_id,
        default_previous,
    )
    selected_current = snapshot_by_id.get(
        to_snapshot_id,
        default_current,
    )

    invalid_selection = (
        (from_snapshot_id is not None and from_snapshot_id not in snapshot_by_id)
        or (to_snapshot_id is not None and to_snapshot_id not in snapshot_by_id)
    )

    if selected_previous["id"] == selected_current["id"]:
        return {
            "status": "same_snapshot",
            "message": (
                "Выберите два разных сохранённых снимка для сравнения."
            ),
            "previous": _comparison_snapshot_summary(selected_previous),
            "current": _comparison_snapshot_summary(selected_current),
            "filling_level_delta": None,
            "changes": [],
            "is_truncated": False,
        }

    old_payload = _json_or_none(selected_previous["payload_json"])
    new_payload = _json_or_none(selected_current["payload_json"])
    changes = _collect_payload_changes(old_payload, new_payload)
    old_filling_level = selected_previous["filling_level"]
    new_filling_level = selected_current["filling_level"]
    filling_level_delta = None

    if old_filling_level is not None and new_filling_level is not None:
        filling_level_delta = new_filling_level - old_filling_level

    return {
        "status": "ok",
        "message": (
            "Один или оба выбранных снимка не относятся к этой карточке. "
            "Показана последняя безопасная пара снимков."
            if invalid_selection
            else None
        ),
        "previous": _comparison_snapshot_summary(selected_previous),
        "current": _comparison_snapshot_summary(selected_current),
        "filling_level_delta": filling_level_delta,
        "changes": changes,
        "is_truncated": len(changes) >= _COMPARISON_MAX_CHANGES,
    }

def _build_site_overview(payload: Any) -> dict[str, Any]:
    """
    Подготавливает устойчивую пользовательскую сводку API-снимка площадки.

    Не изменяет исходный JSON и не предполагает, что все поля внешнего API
    обязательны. Пустые и некорректные значения безопасно превращаются
    в None или пустые списки.
    """
    if not isinstance(payload, dict):
        payload = {}

    economic_activities: list[dict[str, str | None]] = []

    for item in payload.get("economicActivitiesForImplementations", []):
        if not isinstance(item, dict):
            continue

        code = _display_text(item.get("code"))
        description = _display_text(item.get("description"))

        if code is not None or description is not None:
            economic_activities.append(
                {
                    "code": code,
                    "description": description,
                }
            )

    utilities = [
        {
            "name": "Электроснабжение",
            **_display_utility(payload, "powerSupply"),
        },
        {
            "name": "Газоснабжение",
            **_display_utility(payload, "gasSupply"),
        },
        {
            "name": "Теплоснабжение",
            **_display_utility(payload, "heatSupply"),
        },
        {
            "name": "Водоснабжение",
            **_display_utility(payload, "waterSupply"),
        },
        {
            "name": "Водоотведение",
            **_display_utility(payload, "waterDisposal"),
        },
        {
            "name": "Вывоз ТКО",
            **_display_utility(payload, "mswRemoval"),
        },
    ]

    return {
        "title": _display_text(payload.get("siteName")),
        "status": _display_text(payload.get("siteStatus")),
        "types": _display_list(payload.get("typeSites")),
        "formats": _display_list(payload.get("formatSites")),
        "transaction_forms": _display_list(
            payload.get("transactionForms")
        ),
        "ownership_forms": _display_list(payload.get("formOwnerships")),
        "regions": _display_list(payload.get("regions")),
        "municipality": _display_text(payload.get("municipality")),
        "address": _display_text(payload.get("adressObject")),
        "nearest_city": _display_text(payload.get("nearestCity")),
        "coordinates": _display_coordinates(payload.get("coordinate")),
        "total_area": _display_number(
            payload.get("totalSiteArea"),
            " м²",
        ),
        "property_complex_area": _display_number(
            payload.get("areaPropertyComplex"),
            " м²",
        ),
        "building_specifications": _display_text(
            payload.get("buildingSpecifications")
        ),
        "capital_buildings": _display_text(
            payload.get("characteristicsCapitalBuildings")
        ),
        "cadastral_number": _display_text(
            payload.get("cadastralPropertyComplexNumber")
        ),
        "cost": _display_number(payload.get("costObject"), " руб."),
        "access_roads": _display_text(
            payload.get("accessRoadsAvailability")
        ),
        "access_roads_other": _display_text(
            payload.get("accessRoadsOther")
        ),
        "distance_from_road": _display_number(
            payload.get("distanceFromRoad"),
            " км",
        ),
        "railway_availability": _display_text(
            payload.get("railwayAvailability")
        ),
        "truck_parking_availability": _display_text(
            payload.get("truckParkingAvailability")
        ),
        "utilities": utilities,
        "economic_activities": economic_activities,
        "contact_person": _display_text(payload.get("contactPerson")),
        "contact_phone": _display_text(payload.get("contactPhoneNumber")),
        "contact_email": _display_text(
            payload.get("emailAddressForApplying")
        ),
        "contact_website": _display_text(
            payload.get("websiteContactPerson")
        ),
        "application_procedure": _display_text(
            payload.get("descriptionApplicationProcedure")
        ),
        "application_documents": _display_text(
            payload.get("listOfDocumentsForApplication")
        ),
    }

def get_monitor_summary(conn: sqlite3.Connection) -> dict[str, int]:
    """Возвращает сводные счётчики сохранённых API-снимков."""
    row = conn.execute(
        """
        SELECT
            COUNT(DISTINCT global_id) AS cards_count,
            COUNT(*) AS snapshots_count
        FROM investmap_rf_card_snapshots
        """
    ).fetchone()

    changed_cards_count = conn.execute(
        """
        SELECT COUNT(DISTINCT global_id)
        FROM investmap_rf_card_changes
        """
    ).fetchone()[0]

    return {
        "cards_count": row["cards_count"],
        "snapshots_count": row["snapshots_count"],
        "changed_cards_count": changed_cards_count,
    }


MONITOR_CARDS_PER_PAGE = 50
MAX_MONITOR_CARDS_PER_PAGE = 100


def get_monitor_cards(
    conn: sqlite3.Connection,
    *,
    page: int = 1,
    per_page: int = MONITOR_CARDS_PER_PAGE,
    global_id: int | None = None,
) -> dict[str, Any]:
    """
    Возвращает страницу последних API-снимков по площадкам.

    При global_id выполняется точный поиск по ID. Оценка V2 берётся только
    из последнего сохранённого снимка анализа и никогда не рассчитывается
    повторно при открытии страницы мониторинга.
    """
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("page должен быть положительным целым числом.")

    if (
        isinstance(per_page, bool)
        or not isinstance(per_page, int)
        or not 1 <= per_page <= MAX_MONITOR_CARDS_PER_PAGE
    ):
        raise ValueError("per_page имеет недопустимое значение.")

    if global_id is not None and (
        isinstance(global_id, bool)
        or not isinstance(global_id, int)
        or global_id <= 0
    ):
        raise ValueError("global_id должен быть положительным целым числом.")

    where_sql = ""
    where_params: list[Any] = []

    if global_id is not None:
        where_sql = "WHERE global_id = ?"
        where_params.append(global_id)

    total_row = conn.execute(
        f"""
        WITH latest_snapshots AS (
            SELECT global_id, MAX(id) AS snapshot_id
            FROM investmap_rf_card_snapshots
            GROUP BY global_id
        )
        SELECT COUNT(*)
        FROM latest_snapshots
        {where_sql}
        """,
        where_params,
    ).fetchone()
    total = int(total_row[0])

    items_where_sql = ""
    if global_id is not None:
        items_where_sql = "WHERE latest.global_id = ?"

    pages = max(1, (total + per_page - 1) // per_page)

    if page > pages:
        page = pages

    offset = (page - 1) * per_page

    rows = conn.execute(
        f"""
        WITH latest_snapshot_ids AS (
            SELECT global_id, MAX(id) AS snapshot_id
            FROM investmap_rf_card_snapshots
            GROUP BY global_id
        ),
        latest_snapshots AS (
            SELECT
                snapshots.id AS snapshot_id,
                snapshots.global_id,
                snapshots.fetched_at_utc,
                snapshots.filling_level,
                snapshots.payload_json
            FROM investmap_rf_card_snapshots AS snapshots
            INNER JOIN latest_snapshot_ids
                ON latest_snapshot_ids.snapshot_id = snapshots.id
        ),
        previous_snapshots AS (
            SELECT
                latest.global_id,
                (
                    SELECT previous.filling_level
                    FROM investmap_rf_card_snapshots AS previous
                    WHERE previous.global_id = latest.global_id
                      AND previous.id < latest.snapshot_id
                    ORDER BY previous.id DESC
                    LIMIT 1
                ) AS previous_filling_level
            FROM latest_snapshots AS latest
        ),
        changes AS (
            SELECT
                global_id,
                COUNT(*) AS changes_count
            FROM investmap_rf_card_changes
            GROUP BY global_id
        ),
       manager_assignments AS (
            SELECT
                global_id,
                municipality_raw,
                manager_name,
                assignment_source,
                match_status,
                updated_at_utc
            FROM investmap_rf_card_manager_assignments
        ),
        latest_open_issue_ids AS (
            SELECT
                global_id,
                MAX(id) AS issue_id
            FROM investmap_rf_manager_match_issues
            WHERE is_resolved = 0
            GROUP BY global_id
        ),
        open_issues AS (
            SELECT
                issues.id,
                issues.global_id,
                issues.issue_type,
                issues.details,
                issues.municipality_raw,
                issues.updated_at_utc
            FROM investmap_rf_manager_match_issues AS issues
            INNER JOIN latest_open_issue_ids
                ON latest_open_issue_ids.issue_id = issues.id
        )
        SELECT
            latest.snapshot_id,
            latest.global_id,
            latest.fetched_at_utc,
            latest.filling_level,
            previous.previous_filling_level,
            COALESCE(changes.changes_count, 0) AS changes_count,
            latest.payload_json,
            assignments.municipality_raw AS manager_municipality_raw,
            assignments.manager_name,
            assignments.assignment_source,
            assignments.match_status,
            assignments.updated_at_utc AS manager_assignment_updated_at_utc,
            open_issues.issue_type AS open_issue_type,
            open_issues.details AS open_issue_details,
            open_issues.updated_at_utc AS open_issue_updated_at_utc
        FROM latest_snapshots AS latest
        LEFT JOIN previous_snapshots AS previous
            ON previous.global_id = latest.global_id
        LEFT JOIN changes
            ON changes.global_id = latest.global_id
        LEFT JOIN manager_assignments AS assignments
            ON assignments.global_id = latest.global_id
        LEFT JOIN open_issues
            ON open_issues.global_id = latest.global_id
        {items_where_sql}
        ORDER BY latest.fetched_at_utc DESC, latest.snapshot_id DESC
        LIMIT ? OFFSET ?
        """,
        [*where_params, per_page, offset],
    ).fetchall()

    items: list[dict[str, Any]] = []

    for row in rows:
        v2_result = calc_portal_score_v2(
            api_snapshot_to_portal_row(row["payload_json"]),
            conn,
        )

        filling_level = row["filling_level"]
        previous_filling_level = row["previous_filling_level"]
        filling_level_delta = None

        if filling_level is not None and previous_filling_level is not None:
            filling_level_delta = filling_level - previous_filling_level

        items.append(
            {
                "snapshot_id": row["snapshot_id"],
                "global_id": row["global_id"],
                "fetched_at_utc": row["fetched_at_utc"],
                "filling_level": filling_level,
                "previous_filling_level": previous_filling_level,
                "filling_level_delta": filling_level_delta,
                "changes_count": row["changes_count"],
                "v2_score": v2_result["score"],
                "v2_analysis_status": "ok",
                "v2_analyzed_at_utc": row["fetched_at_utc"],
                "v2_formula_version": V2_FORMULA_VERSION,
                "v2_missing": v2_result["missing"],
                "v2_partial": v2_result["partial"],
                "v2_skipped": v2_result["skipped"],
                "v2_filled": v2_result["filled"],
                "v2_total": v2_result["total"],
                "v2_api_score_delta": (
                    v2_result["score"] - filling_level
                    if filling_level is not None
                    else None
                ),
                "manager_municipality_raw": row["manager_municipality_raw"],
                "manager_name": row["manager_name"],
                "assignment_source": row["assignment_source"],
                "match_status": row["match_status"],
                "manager_assignment_updated_at_utc": (
                    row["manager_assignment_updated_at_utc"]
                ),
                "open_issue_type": row["open_issue_type"],
                "open_issue_details": row["open_issue_details"],
                "open_issue_updated_at_utc": row["open_issue_updated_at_utc"],
            }
        )

    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
        "search_global_id": global_id,
        "first_item_number": offset + 1 if items else 0,
        "last_item_number": offset + len(items),
    }


def get_monitor_card_detail(
    conn: sqlite3.Connection,
    global_id: int,
    *,
    from_snapshot_id: int | None = None,
    to_snapshot_id: int | None = None,
) -> dict[str, Any] | None:
    """Возвращает последнюю карточку, её снимки и историю изменений."""
    latest = conn.execute(
        """
        SELECT
            id,
            global_id,
            payload_json,
            payload_sha256,
            fetched_at_utc,
            filling_level,
            region_code
        FROM investmap_rf_card_snapshots
        WHERE global_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (global_id,),
    ).fetchone()

    if latest is None:
        return None

    snapshots = conn.execute(
        """
        SELECT
            id,
            fetched_at_utc,
            filling_level,
            region_code,
            payload_sha256,
            payload_json
        FROM investmap_rf_card_snapshots
        WHERE global_id = ?
        ORDER BY id DESC
        """,
        (global_id,),
    ).fetchall()
    

    changes = conn.execute(
        """
        SELECT
            id,
            previous_snapshot_id,
            current_snapshot_id,
            field_path,
            old_value_json,
            new_value_json,
            detected_at_utc
        FROM investmap_rf_card_changes
        WHERE global_id = ?
        ORDER BY id DESC
        """,
        (global_id,),
    ).fetchall()
    assignment = conn.execute(
        """
        SELECT
            assignments.global_id,
            assignments.municipality_raw,
            assignments.municipality_normalized,
            assignments.manager_name,
            assignments.rule_id,
            assignments.assignment_source,
            assignments.match_status,
            assignments.assigned_by_user_id,
            assignments.updated_at_utc,
            rules.municipality_name AS rule_municipality_name,
            rules.manager_name AS rule_manager_name
        FROM investmap_rf_card_manager_assignments AS assignments
        LEFT JOIN investmap_rf_municipality_manager_rules AS rules
            ON rules.id = assignments.rule_id
        WHERE assignments.global_id = ?
        """,
        (global_id,),
    ).fetchone()

    open_issues = conn.execute(
        """
        SELECT
            id,
            municipality_raw,
            municipality_normalized,
            issue_type,
            details,
            created_at_utc,
            updated_at_utc
        FROM investmap_rf_manager_match_issues
        WHERE global_id = ?
          AND is_resolved = 0
        ORDER BY id DESC
        """,
        (global_id,),
    ).fetchall()
    latest_payload = _json_or_none(latest["payload_json"])
        comparison = _build_snapshot_comparison(
        snapshots,
        from_snapshot_id=from_snapshot_id,
        to_snapshot_id=to_snapshot_id,
    )

    return {
        "latest": {
            "snapshot_id": latest["id"],
            "global_id": latest["global_id"],
            "payload": latest_payload,
            "payload_sha256": latest["payload_sha256"],
            "fetched_at_utc": latest["fetched_at_utc"],
            "filling_level": latest["filling_level"],
            "region_code": latest["region_code"],
            "comparison": comparison,
        },
        "site_overview": _build_site_overview(latest_payload),
        "assignment": (
            {
                "global_id": assignment["global_id"],
                "municipality_raw": assignment["municipality_raw"],
                "municipality_normalized": assignment[
                    "municipality_normalized"
                ],
                "manager_name": assignment["manager_name"],
                "rule_id": assignment["rule_id"],
                "assignment_source": assignment["assignment_source"],
                "match_status": assignment["match_status"],
                "assigned_by_user_id": assignment["assigned_by_user_id"],
                "updated_at_utc": assignment["updated_at_utc"],
                "rule_municipality_name": assignment[
                    "rule_municipality_name"
                ],
                "rule_manager_name": assignment["rule_manager_name"],
            }
            if assignment is not None
            else None
        ),
        "open_issues": [
            {
                "issue_id": row["id"],
                "municipality_raw": row["municipality_raw"],
                "municipality_normalized": row[
                    "municipality_normalized"
                ],
                "issue_type": row["issue_type"],
                "details": row["details"],
                "created_at_utc": row["created_at_utc"],
                "updated_at_utc": row["updated_at_utc"],
            }
            for row in open_issues
        ],
        "snapshots": [
            {
                "snapshot_id": row["id"],
                "fetched_at_utc": row["fetched_at_utc"],
                "filling_level": row["filling_level"],
                "region_code": row["region_code"],
                "payload_sha256": row["payload_sha256"],
            }
            for row in snapshots
        ],
        "changes": [
            _build_history_change(row)
            for row in changes
        ],
    }
    
def get_monitor_registry_cards(
    conn,
    limit: int = 1000,
    global_id: int | None = None,
):
    """Возвращает активные площадки реестра, при необходимости по ID."""
    if global_id is not None:
        if (
            isinstance(global_id, bool)
            or not isinstance(global_id, int)
            or global_id <= 0
        ):
            raise ValueError(
                "global_id должен быть положительным целым числом."
            )

    where_sql = "WHERE is_active = 1"
    params: list[Any] = []

    if global_id is not None:
        where_sql += " AND global_id = ?"
        params.append(global_id)

    params.append(limit)

    return conn.execute(
        f"""
        SELECT
            global_id,
            is_active,
            source_filename,
            imported_at_utc,
            last_seen_import_at_utc,
            last_source_status,
            last_api_check_at_utc,
            last_api_check_status,
            last_api_check_error,
            api_not_found_pending_decision,
            api_not_found_detected_at_utc
        FROM investmap_rf_monitored_cards
        {where_sql}
        ORDER BY
            api_not_found_pending_decision DESC,
            global_id
        LIMIT ?
        """,
        params,
    ).fetchall()


def get_monitor_registry_events(conn, limit: int = 30):
    """Возвращает последние события реестра с причиной и автором изменения."""
    return conn.execute(
        """
        SELECT
            events.id,
            events.global_id,
            events.event_type,
            events.previous_status,
            events.current_status,
            events.source_filename,
            events.occurred_at_utc,
            events.reason,
            events.changed_by_user_id,
            users.username AS changed_by_username,
            users.full_name AS changed_by_full_name
        FROM investmap_rf_monitor_registry_events AS events
        LEFT JOIN users
            ON users.id = events.changed_by_user_id
        ORDER BY events.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
