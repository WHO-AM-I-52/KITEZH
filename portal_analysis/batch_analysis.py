"""Пакетный расчёт и сохранение снимков инвестиционных площадок."""

from __future__ import annotations

from typing import Any, Callable

from .analysis_history import (
    create_analysis_history_tables,
    create_run,
    get_previous_snapshot,
    is_site_included,
    make_fields_hash,
    save_snapshot,
)
from .portal_checker import PORTAL_FIELDS, calc_portal_score

FORMULA_VERSION = "2.1.0"
SITE_ID_FIELD = "global_id"
SITE_NAME_FIELD = "Название площадки"
SITE_STATUS_FIELD = "Статус площадки"
_GEO_FIELDS = [
    "Широта объекта в координатах WGS-84",
    "Долгота объекта в координатах WGS-84",
    "Набор координат линии объекта в координатах WGS-84",
    "Набор координат полигона объекта в координатах WGS-84",
]
HASH_FIELDS = list(
    dict.fromkeys(PORTAL_FIELDS + [SITE_ID_FIELD, SITE_STATUS_FIELD] + _GEO_FIELDS)
)

ScoreFn = Callable[[dict[str, Any]], dict[str, Any] | None]


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _normalize_site_id(value: Any) -> str:
    """Нормализует global_id для history-снимка."""
    return _text(value)


def _is_valid_site_id(value: Any) -> bool:
    """Проверяет техническую валидность global_id.

    Без отдельного доменного контракта не вводим regex, UUID- или числовую
    валидацию: schema хранит site_id как TEXT. Невалидны только None,
    пустое значение и строка из пробелов.
    """
    return bool(_normalize_site_id(value))


def _finish_run(conn, run_id: int, summary: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE portal_analysis_runs
        SET total_sites = ?,
            active_sites = ?,
            excluded_sites = ?,
            error_sites = ?,
            average_score = ?
        WHERE id = ?
        """,
        (
            summary["total_sites"],
            summary["active_sites"],
            summary["excluded_sites"],
            summary["error_sites"],
            summary["average_score"],
            run_id,
        ),
    )


def _error_item(
    row_index: int,
    site_id: str,
    error_type: str,
    message: str,
) -> dict[str, Any]:
    return {
        "row_index": row_index,
        "site_id": site_id,
        "error_type": error_type,
        "message": message,
    }


def _snapshot_exists(conn, run_id: int, site_id: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM portal_analysis_site_snapshots
        WHERE run_id = ? AND site_id = ?
        """,
        (run_id, site_id),
    ).fetchone()
    return row is not None


def run_batch_history(
    conn,
    source_rows: list[dict[str, Any]],
    score_fn: ScoreFn,
    formula_version: str,
    initiated_by: int | None = None,
    source_label: str | None = None,
) -> dict[str, Any]:
    """Выполняет пакетный расчёт через переданный scorer.

    Соединением SQLite владеет вызывающий код: сервис не вызывает get_db(),
    commit(), rollback() или close(). Таблицы history должны быть созданы
    bootstrap/migration либо вызывающим кодом до вызова этого service.
    """
    run_id = create_run(conn, formula_version, initiated_by, source_label)

    active_sites = 0
    excluded_sites = 0
    error_sites = 0
    saved_sites = 0
    scores: list[int | float] = []
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen_site_ids: set[str] = set()

    for row_index, row in enumerate(source_rows):
        site_id = _normalize_site_id(row.get(SITE_ID_FIELD))
        site_name = row.get(SITE_NAME_FIELD)
        site_status = row.get(SITE_STATUS_FIELD)

        if not site_id:
            error_message = "Пустой или некорректный global_id"
            error_sites += 1
            errors.append(
                _error_item(
                    row_index,
                    site_id,
                    "invalid_global_id",
                    error_message,
                )
            )
            results.append(
                {
                    "row_index": row_index,
                    "site_id": "",
                    "included": None,
                    "snapshot_saved": False,
                    "is_new": False,
                    "changed_source": False,
                    "changed_inclusion": False,
                    "changed_score": False,
                    "result": None,
                    "analysis_status": "invalid_id",
                    "error": error_message,
                }
            )
            continue

        if site_id in seen_site_ids:
            error_message = "Повторный global_id в одном пакете"
            error_sites += 1
            errors.append(
                _error_item(
                    row_index,
                    site_id,
                    "duplicate_global_id",
                    error_message,
                )
            )
            results.append(
                {
                    "row_index": row_index,
                    "site_id": site_id,
                    "included": None,
                    "snapshot_saved": False,
                    "is_new": False,
                    "changed_source": False,
                    "changed_inclusion": False,
                    "changed_score": False,
                    "result": None,
                    "analysis_status": "error",
                    "error": error_message,
                }
            )
            continue

        seen_site_ids.add(site_id)
        included = is_site_included(site_status)

        if included:
            active_sites += 1
        else:
            excluded_sites += 1

        result: dict[str, Any] | None = None
        field_hash: str | None = None
        previous = None

        try:
            # Единственный вызов переданного scorer для валидной,
            # уникальной строки. V2 scorer сам определяет свой результат.
            result = score_fn(row)

            if (
                included
                and isinstance(result, dict)
                and result.get("score") is not None
            ):
                scores.append(result["score"])

            field_hash = make_fields_hash(row, HASH_FIELDS)
            previous = get_previous_snapshot(conn, site_id, run_id)

            save_snapshot(
                conn,
                run_id,
                site_id,
                site_name,
                site_status,
                result=result,
                field_values_hash=field_hash,
            )
            saved_sites += 1

            previous_score = previous["score_percent"] if previous else None
            current_score = result.get("score") if result else None

            results.append(
                {
                    "row_index": row_index,
                    "site_id": site_id,
                    "included": included,
                    "snapshot_saved": True,
                    "is_new": previous is None,
                    "changed_source": bool(
                        previous
                        and previous["field_values_hash"] != field_hash
                    ),
                    "changed_inclusion": bool(
                        previous
                        and previous["is_included"] != int(included)
                    ),
                    "changed_score": bool(
                        previous and previous_score != current_score
                    ),
                    "result": result,
                    "analysis_status": "ok" if included else "excluded",
                    "error": None,
                }
            )
        except Exception as exc:
            error_sites += 1
            error_message = str(exc)

            # Если первичная вставка не создала snapshot, можно попытаться
            # сохранить один error snapshot. При уже существующей записи
            # повторный INSERT не выполняем: это защищает UNIQUE(run_id, site_id).
            if not _snapshot_exists(conn, run_id, site_id):
                try:
                    if field_hash is None:
                        field_hash = make_fields_hash(row, HASH_FIELDS)

                    save_snapshot(
                        conn,
                        run_id,
                        site_id,
                        site_name,
                        site_status,
                        field_values_hash=field_hash,
                        error_message=error_message,
                    )
                    saved_sites += 1
                except Exception:
                    pass

            errors.append(
                _error_item(
                    row_index,
                    site_id,
                    "score_or_snapshot_error",
                    error_message,
                )
            )
            results.append(
                {
                    "row_index": row_index,
                    "site_id": site_id,
                    "included": included,
                    "snapshot_saved": _snapshot_exists(conn, run_id, site_id),
                    "is_new": False,
                    "changed_source": False,
                    "changed_inclusion": False,
                    "changed_score": False,
                    "result": None,
                    "analysis_status": "error",
                    "error": error_message,
                }
            )

    average_score = round(sum(scores) / len(scores), 1) if scores else None

    _finish_run(
        conn,
        run_id,
        {
            "total_sites": len(source_rows),
            "active_sites": active_sites,
            "excluded_sites": excluded_sites,
            "error_sites": error_sites,
            "average_score": average_score,
        },
    )

    return {
        "run_id": run_id,
        "results": results,
        "total_sites": len(source_rows),
        "saved_sites": saved_sites,
        "active_sites": active_sites,
        "excluded_sites": excluded_sites,
        "error_sites": error_sites,
        "errors": errors,
    }


def _legacy_score_fn(row: dict[str, Any]) -> dict[str, Any] | None:
    """Сохраняет V1-семантику: excluded площадки не передаются V1 scorer."""
    if not is_site_included(row.get(SITE_STATUS_FIELD)):
        return None
    return calc_portal_score(row)


def analyze_and_save_rows(
    conn,
    rows: list[dict[str, Any]],
    initiated_by: int | None = None,
    source_label: str | None = None,
) -> dict[str, Any]:
    """Legacy V1-wrapper поверх чистого batch history service."""
    # Сохраняем прежнюю bootstrap-совместимость legacy entry point.
    create_analysis_history_tables(conn)

    batch_result = run_batch_history(
        conn=conn,
        source_rows=rows,
        score_fn=_legacy_score_fn,
        formula_version=FORMULA_VERSION,
        initiated_by=initiated_by,
        source_label=source_label,
    )

    changed_source_sites = sum(
        1
        for item in batch_result["results"]
        if item["snapshot_saved"] and item["changed_source"]
    )
    changed_score_sites = sum(
        1
        for item in batch_result["results"]
        if item["snapshot_saved"] and item["changed_score"]
    )
    changed_inclusion_sites = sum(
        1
        for item in batch_result["results"]
        if item["snapshot_saved"] and item["changed_inclusion"]
    )
    new_sites = sum(
        1
        for item in batch_result["results"]
        if item["snapshot_saved"] and item["is_new"]
    )

    score_rows = conn.execute(
        """
        SELECT score_percent
        FROM portal_analysis_site_snapshots
        WHERE run_id = ?
          AND is_included = 1
          AND score_percent IS NOT NULL
        """,
        (batch_result["run_id"],),
    ).fetchall()
    scores = [row["score_percent"] for row in score_rows]
    average_score = round(sum(scores) / len(scores), 1) if scores else None

    return {
        "run_id": batch_result["run_id"],
        "total_sites": batch_result["total_sites"],
        "active_sites": batch_result["active_sites"],
        "excluded_sites": batch_result["excluded_sites"],
        "error_sites": batch_result["error_sites"],
        "average_score": average_score,
        "changed_source_sites": changed_source_sites,
        "changed_score_sites": changed_score_sites,
        "changed_inclusion_sites": changed_inclusion_sites,
        "new_sites": new_sites,
    }
