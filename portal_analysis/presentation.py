"""Представление результатов V2 для интерфейса, ИИ и сообщений контактам."""

from __future__ import annotations

from typing import Any

from .analysis_history import get_exclusion_reason, is_site_included
from .portal_checker import calc_portal_score


def _text(value: Any, fallback: str = '') -> str:
    text = '' if value is None else str(value).strip()
    return text or fallback


def build_ai_text(site: dict[str, Any]) -> str:
    lines = [f"Площадка: {site['name']}", f"ID: {site['id'] or 'не указан'}", f"Статус площадки: {site['status'] or 'не указан'}"]
    if not site['included']:
        lines.append(f"Учитывается в оценке: нет ({site['exclusion_reason']}).")
        return '\n'.join(lines)
    lines.extend(['Учитывается в оценке: да.', f"Заполняемость V2: {site['score']}%.", f"Заполнено: {site['filled']} из {site['total']} обязательных полей.", f"Условно не учитывается: {len(site['skipped'])} полей."])
    if site['missing']:
        lines.append('Требуется заполнить:')
        lines.extend(f"{index}. {field}" for index, field in enumerate(site['missing'], 1))
    else:
        lines.append('Все учитываемые поля заполнены.')
    return '\n'.join(lines)


def build_site_result(row: dict[str, Any]) -> dict[str, Any]:
    site = {'id': _text(row.get('global_id')), 'name': _text(row.get('Название площадки'), 'Без названия'), 'status': _text(row.get('Статус площадки')), 'contact': _text(row.get('Контактное лицо'))}
    site['included'] = is_site_included(site['status'])
    site['exclusion_reason'] = get_exclusion_reason(site['status'])
    if site['included']:
        site.update(calc_portal_score(row))
    else:
        site.update({'score': None, 'filled': 0, 'total': 0, 'missing': [], 'skipped': []})
    site['ai_text'] = build_ai_text(site)
    return site


def build_site_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [build_site_result(row) for row in rows]


def build_contact_messages(sites: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for site in sites:
        if site['included']:
            groups.setdefault(site['contact'] or '__no_contact__', []).append(site)
    messages = []
    for contact, contact_sites in sorted(groups.items()):
        blocks = []
        for site in contact_sites:
            lines = [f"📍 «{site['name']}» (ID: {site['id'] or 'не указан'}) — заполняемость {site['score']}%."]
            if site['missing']:
                lines.append('Просим заполнить: ' + '; '.join(site['missing']) + '.')
            else:
                lines.append('Все учитываемые поля заполнены.')
            blocks.append('\n'.join(lines))
        if contact == '__no_contact__':
            text = 'ПЛОЩАДКИ БЕЗ КОНТАКТНОГО ЛИЦА\n\n' + '\n\n'.join(blocks)
        else:
            text = f"Добрый день, {contact}!\n\nПо результатам проверки карточек на Инвестиционной карте:\n\n" + '\n\n'.join(blocks) + '\n\nПосле внесения изменений просим сообщить — проведём повторную проверку.'
        messages.append({'contact': contact, 'sites': [site['name'] for site in contact_sites], 'text': text})
    return messages
