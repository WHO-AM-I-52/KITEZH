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


def get_monitor_cards(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """
    Возвращает последнюю карточку каждого global_id с предыдущим filling_level.

    Результат не содержит полный payload_json: он доступен только на detail-экране.
    """
    rows = conn.execute(
        """
        WITH ranked_snapshots AS (
            SELECT
                id,
                global_id,
                fetched_at_utc,
                filling_level,
                region_code,
                ROW_NUMBER() OVER (
                    PARTITION BY global_id
                    ORDER BY id DESC
                ) AS position
            FROM investmap_rf_card_snapshots
        ),
        latest_snapshots AS (
            SELECT
                id,
                global_id,
                fetched_at_utc,
                filling_level,
                region_code
            FROM ranked_snapshots
            WHERE position = 1
        ),
        previous_snapshots AS (
            SELECT
                global_id,
                filling_level AS previous_filling_level
            FROM ranked_snapshots
            WHERE position = 2
        ),
        changes AS (
            SELECT
                global_id,
                COUNT(*) AS changes_count
            FROM investmap_rf_card_changes
            GROUP BY global_id
        )
        SELECT
            latest_snapshots.id AS snapshot_id,
            latest_snapshots.global_id,
            latest_snapshots.fetched_at_utc,
            latest_snapshots.filling_level,
            previous_snapshots.previous_filling_level,
            latest_snapshots.region_code,
            COALESCE(changes.changes_count, 0) AS changes_count
        FROM latest_snapshots
        LEFT JOIN previous_snapshots
            ON previous_snapshots.global_id = latest_snapshots.global_id
        LEFT JOIN changes
            ON changes.global_id = latest_snapshots.global_id
        ORDER BY latest_snapshots.fetched_at_utc DESC, latest_snapshots.id DESC
        """
    ).fetchall()

    cards: list[dict[str, Any]] = []
    for row in rows:
        filling_level = row["filling_level"]
        previous_filling_level = row["previous_filling_level"]
        filling_level_delta = None

        if filling_level is not None and previous_filling_level is not None:
            filling_level_delta = filling_level - previous_filling_level

        cards.append(
            {
                "snapshot_id": row["snapshot_id"],
                "global_id": row["global_id"],
                "fetched_at_utc": row["fetched_at_utc"],
                "filling_level": filling_level,
                "previous_filling_level": previous_filling_level,
                "filling_level_delta": filling_level_delta,
                "region_code": row["region_code"],
                "changes_count": row["changes_count"],
            }
        )

    return cards


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
