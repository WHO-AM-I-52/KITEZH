"""Представление результатов V2 для интерфейса, ИИ и сообщений контактам."""

from __future__ import annotations

from typing import Any

from .analysis_history import get_exclusion_reason, is_site_included
from .portal_checker import calc_portal_score


def _text(value: Any, fallback: str = '') -> str:
    text = '' if value is None else str(value).strip()
    return text or fallback


def build_ai_text(site: dict[str, Any]) -> str:
    """Создаёт краткий контекст расчёта для ИИ по одной площадке."""
    lines = [
        f"Площадка: {site['name']}",
        f"ID: {site['id'] or 'не указан'}",
        f"Статус площадки: {site['status'] or 'не указан'}",
    ]
    if not site['included']:
        lines.append(f"Учитывается в оценке: нет ({site['exclusion_reason']}).")
        return '\n'.join(lines)

    lines.extend([
        'Учитывается в оценке: да.',
        f"Заполняемость V2: {site['score']}%.",
        f"Заполнено: {site['filled']} из {site['total']} обязательных полей.",
        f"Условно не учитывается: {len(site['skipped'])} полей.",
    ])
    if site['missing']:
        lines.append('Требуется заполнить:')
        lines.extend(f"{index}. {field}" for index, field in enumerate(site['missing'], 1))
    else:
        lines.append('Все учитываемые поля заполнены.')
    return '\n'.join(lines)


def build_site_result(row: dict[str, Any]) -> dict[str, Any]:
    """Строит единый результат V2, пригодный для всех представлений UI."""
    site = {
        'id': _text(row.get('global_id')),
        'name': _text(row.get('Название площадки'), 'Без названия'),
        'status': _text(row.get('Статус площадки')),
    }
    site['included'] = is_site_included(site['status'])
    site['exclusion_reason'] = get_exclusion_reason(site['status'])
    if site['included']:
        result = calc_portal_score(row)
        site.update(result)
    else:
        site.update({'score': None, 'filled': 0, 'total': 0, 'missing': [], 'skipped': []})
    site['ai_text'] = build_ai_text(site)
    return site


def build_site_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [build_site_result(row) for row in rows]
