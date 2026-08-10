"""Пакетный расчёт V2 и сохранение снимков инвестиционных площадок."""

from __future__ import annotations

from typing import Any

from .analysis_history import create_analysis_history_tables, create_run, get_previous_snapshot, is_site_included, make_fields_hash, save_snapshot
from .portal_checker import PORTAL_FIELDS, calc_portal_score

FORMULA_VERSION = '2.1.0'
SITE_ID_FIELD = 'global_id'
SITE_NAME_FIELD = 'Название площадки'
SITE_STATUS_FIELD = 'Статус площадки'
_GEO_FIELDS = ['Широта объекта в координатах WGS-84', 'Долгота объекта в координатах WGS-84', 'Набор координат линии объекта в координатах WGS-84', 'Набор координат полигона объекта в координатах WGS-84']
HASH_FIELDS = list(dict.fromkeys(PORTAL_FIELDS + [SITE_ID_FIELD, SITE_STATUS_FIELD] + _GEO_FIELDS))


def _text(value: Any) -> str:
    return '' if value is None else str(value).strip()


def _finish_run(conn, run_id: int, summary: dict[str, Any]) -> None:
    conn.execute("UPDATE portal_analysis_runs SET total_sites=?, active_sites=?, excluded_sites=?, error_sites=?, average_score=? WHERE id=?", (summary['total_sites'], summary['active_sites'], summary['excluded_sites'], summary['error_sites'], summary['average_score'], run_id))


def analyze_and_save_rows(conn, rows: list[dict[str, Any]], initiated_by: int | None = None, source_label: str | None = None) -> dict[str, Any]:
    """Рассчитывает выгрузку и сохраняет отдельный снимок каждой площадки."""
    create_analysis_history_tables(conn)
    run_id = create_run(conn, FORMULA_VERSION, initiated_by, source_label)
    summary = {'run_id': run_id, 'total_sites': len(rows), 'active_sites': 0, 'excluded_sites': 0, 'error_sites': 0, 'average_score': None, 'changed_source_sites': 0, 'changed_score_sites': 0, 'changed_inclusion_sites': 0, 'new_sites': 0}
    scores: list[int] = []
    for row in rows:
        site_id = _text(row.get(SITE_ID_FIELD))
        site_name, site_status = row.get(SITE_NAME_FIELD), row.get(SITE_STATUS_FIELD)
        included = is_site_included(site_status)
        if not site_id:
            summary['error_sites'] += 1
            continue
        previous = get_previous_snapshot(conn, site_id, run_id)
        field_hash = make_fields_hash(row, HASH_FIELDS)
        try:
            result = None
            if included:
                result = calc_portal_score(row)
                summary['active_sites'] += 1
                scores.append(result['score'])
            else:
                summary['excluded_sites'] += 1
            save_snapshot(conn, run_id, site_id, site_name, site_status, result=result, field_values_hash=field_hash)
            if previous is None:
                summary['new_sites'] += 1
            else:
                if previous['field_values_hash'] != field_hash:
                    summary['changed_source_sites'] += 1
                if previous['is_included'] != int(included):
                    summary['changed_inclusion_sites'] += 1
                if previous['score_percent'] != (result['score'] if result else None):
                    summary['changed_score_sites'] += 1
        except Exception as exc:
            save_snapshot(conn, run_id, site_id, site_name, site_status, field_values_hash=field_hash, error_message=str(exc))
            summary['error_sites'] += 1
    if scores:
        summary['average_score'] = round(sum(scores) / len(scores), 1)
    _finish_run(conn, run_id, summary)
    return summary
