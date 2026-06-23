"""
field_validator.py — чистые функции проверки значений полей площадки.
Зависимости: только stdlib. Без Flask, без db.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass
class FieldCheckResult:
    field_key: str
    portal_name: str
    passed: bool
    reason: str        # пустая строка если passed=True
    recommendation: str


def validate_field(field_rule: dict, value: Any) -> FieldCheckResult | None:
    """
    Проверяет одно поле по правилам из text_checks.

    Возвращает FieldCheckResult или None если поле не имеет text_checks
    (такие поля следует пропускать — dropdown, file, geo и т.п.).

    Логика по типу поля:
      text   → irrelevant_values, min_length, pattern
      number → min_value
    """
    checks = field_rule.get("text_checks")
    if not checks:
        return None

    field_key = field_rule.get("key", "")
    portal_name = field_rule.get("portal_name", "")
    recommendation = checks.get("recommendation", "")
    field_type = field_rule.get("type", "text")

    # Пустое / None значение — единый случай для обоих типов
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return FieldCheckResult(
            field_key=field_key,
            portal_name=portal_name,
            passed=False,
            reason="Значение отсутствует",
            recommendation=recommendation,
        )

    if field_type == "number":
        # Приводим к числу
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return FieldCheckResult(
                field_key=field_key,
                portal_name=portal_name,
                passed=False,
                reason="Значение должно быть числом",
                recommendation=recommendation,
            )

        min_value = checks.get("min_value")
        if min_value is not None and numeric < min_value:
            return FieldCheckResult(
                field_key=field_key,
                portal_name=portal_name,
                passed=False,
                reason=f"Значение {numeric} меньше минимально допустимого {min_value}",
                recommendation=recommendation,
            )

        return FieldCheckResult(
            field_key=field_key,
            portal_name=portal_name,
            passed=True,
            reason="",
            recommendation=recommendation,
        )

    # text / text-подобные поля
    str_value = str(value).strip()

    irrelevant = checks.get("irrelevant_values", [])
    if str_value.lower() in [v.lower() for v in irrelevant]:
        return FieldCheckResult(
            field_key=field_key,
            portal_name=portal_name,
            passed=False,
            reason=f"Значение «{str_value}» является нерелевантным",
            recommendation=recommendation,
        )

    min_length = checks.get("min_length")
    if min_length is not None and len(str_value) < min_length:
        return FieldCheckResult(
            field_key=field_key,
            portal_name=portal_name,
            passed=False,
            reason=f"Длина значения ({len(str_value)}) меньше минимально допустимой ({min_length})",
            recommendation=recommendation,
        )

    pattern = checks.get("pattern")
    if pattern and not re.search(pattern, str_value):
        return FieldCheckResult(
            field_key=field_key,
            portal_name=portal_name,
            passed=False,
            reason=f"Значение не соответствует формату ({pattern})",
            recommendation=recommendation,
        )

    return FieldCheckResult(
        field_key=field_key,
        portal_name=portal_name,
        passed=True,
        reason="",
        recommendation=recommendation,
    )


def validate_site(site_dict: dict, rules: list[dict]) -> list[FieldCheckResult]:
    """
    Проверяет все поля площадки по списку правил из site_field_rules.json.

    Пропускает поля без text_checks.
    Возвращает список FieldCheckResult только для проверенных полей.

    :param site_dict: словарь значений площадки {key: value}
    :param rules:     список правил из site_field_rules.json
    :return:          список результатов проверки
    """
    results: list[FieldCheckResult] = []
    for rule in rules:
        key = rule.get("key", "")
        value = site_dict.get(key)
        result = validate_field(rule, value)
        if result is not None:
            results.append(result)
    return results
