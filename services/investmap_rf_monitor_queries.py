"""Read-only SQL-запросы для мониторинга API Инвестиционной карты РФ."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


def _json_or_none(value: str | None) -> Any:
    if value is None:
        return None

    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


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
                snapshots.filling_level
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
        latest_v2_snapshot_ids AS (
            SELECT
                site_id,
                MAX(run_id) AS run_id
            FROM portal_analysis_site_snapshots
            GROUP BY site_id
        ),
        latest_v2 AS (
            SELECT
                snapshots.site_id,
                snapshots.score_percent,
                snapshots.analysis_status,
                runs.created_at AS analyzed_at_utc,
                runs.formula_version
            FROM portal_analysis_site_snapshots AS snapshots
            INNER JOIN latest_v2_snapshot_ids
                ON latest_v2_snapshot_ids.site_id = snapshots.site_id
               AND latest_v2_snapshot_ids.run_id = snapshots.run_id
            INNER JOIN portal_analysis_runs AS runs
                ON runs.id = snapshots.run_id
        )
        SELECT
            latest.snapshot_id,
            latest.global_id,
            latest.fetched_at_utc,
            latest.filling_level,
            previous.previous_filling_level,
            COALESCE(changes.changes_count, 0) AS changes_count,
            latest_v2.score_percent AS v2_score,
            latest_v2.analysis_status AS v2_analysis_status,
            latest_v2.analyzed_at_utc AS v2_analyzed_at_utc,
            latest_v2.formula_version AS v2_formula_version
        FROM latest_snapshots AS latest
        LEFT JOIN previous_snapshots AS previous
            ON previous.global_id = latest.global_id
        LEFT JOIN changes
            ON changes.global_id = latest.global_id
        LEFT JOIN latest_v2
            ON latest_v2.site_id = CAST(latest.global_id AS TEXT)
        {items_where_sql}
        ORDER BY latest.fetched_at_utc DESC, latest.snapshot_id DESC
        LIMIT ? OFFSET ?
        """,
        [*where_params, per_page, offset],
    ).fetchall()

    items: list[dict[str, Any]] = []

    for row in rows:
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
                "v2_score": row["v2_score"],
                "v2_analysis_status": row["v2_analysis_status"],
                "v2_analyzed_at_utc": row["v2_analyzed_at_utc"],
                "v2_formula_version": row["v2_formula_version"],
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
            payload_sha256
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

    return {
        "latest": {
            "snapshot_id": latest["id"],
            "global_id": latest["global_id"],
            "payload": _json_or_none(latest["payload_json"]),
            "payload_sha256": latest["payload_sha256"],
            "fetched_at_utc": latest["fetched_at_utc"],
            "filling_level": latest["filling_level"],
            "region_code": latest["region_code"],
        },
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
            {
                "change_id": row["id"],
                "previous_snapshot_id": row["previous_snapshot_id"],
                "current_snapshot_id": row["current_snapshot_id"],
                "field_path": row["field_path"],
                "old_value": _json_or_none(row["old_value_json"]),
                "new_value": _json_or_none(row["new_value_json"]),
                "detected_at_utc": row["detected_at_utc"],
            }
            for row in changes
        ],
    }
    
def get_monitor_registry_cards(conn, limit: int = 1000):
    """Возвращает активные площадки из реестра мониторинга."""
    return conn.execute(
        """
        SELECT
            global_id,
            is_active,
            source_filename,
            imported_at_utc,
            last_seen_import_at_utc,
            last_source_status
        FROM investmap_rf_monitored_cards
        WHERE is_active = 1
        ORDER BY global_id
        LIMIT ?
        """,
        (limit,),
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
