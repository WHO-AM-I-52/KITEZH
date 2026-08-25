"""Тихое одноразовое назначение управляющего для карточек ОЭЗ «Кулибин»."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db import get_db
from services.investmap_rf_manager_assignment import (
    MATCH_STATUS_MANUAL,
    MATCH_STATUS_MATCHED,
    is_kulibin_special_economic_zone,
    update_card_manager_assignment,
)


def _parse_payload(value: str) -> dict:
    try:
        payload = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Некорректный JSON сохранённого API-snapshot.") from exc

    if not isinstance(payload, dict):
        raise ValueError("Сохранённый API-payload должен быть JSON-объектом.")

    return payload


def _latest_kulibin_cards(conn: sqlite3.Connection):
    rows = conn.execute(
        """
        WITH latest_snapshot_ids AS (
            SELECT global_id, MAX(id) AS snapshot_id
            FROM investmap_rf_card_snapshots
            GROUP BY global_id
        )
        SELECT
            snapshots.global_id,
            snapshots.payload_json
        FROM investmap_rf_card_snapshots AS snapshots
        INNER JOIN latest_snapshot_ids
            ON latest_snapshot_ids.snapshot_id = snapshots.id
        ORDER BY snapshots.global_id
        """
    ).fetchall()

    for row in rows:
        payload = _parse_payload(row["payload_json"])
        card = SimpleNamespace(
            global_id=int(row["global_id"]),
            payload=payload,
        )

        if is_kulibin_special_economic_zone(card):
            yield card


def backfill_kulibin_assignments(
    conn: sqlite3.Connection,
    *,
    dry_run: bool,
) -> dict[str, int]:
    """Назначает Земскова для последних сохранённых snapshot ОЭЗ «Кулибин»."""
    counters = Counter()

    try:
        for card in _latest_kulibin_cards(conn):
            counters["total"] += 1
            result = update_card_manager_assignment(
                conn,
                card=card,
                notify_admins=False,
            )

            counters["processed"] += 1
            counters[result["status"]] += 1

        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    except Exception:
        conn.rollback()
        raise

    return {
        "total": counters["total"],
        "processed": counters["processed"],
        "matched": counters[MATCH_STATUS_MATCHED],
        "manual": counters[MATCH_STATUS_MANUAL],
        "dry_run": int(dry_run),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Тихо назначает управляющего ОЭЗ «Кулибин» по последним "
            "сохранённым API-snapshot."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Проверить результат и откатить все изменения.",
    )
    args = parser.parse_args()

    conn = get_db()

    try:
        result = backfill_kulibin_assignments(
            conn,
            dry_run=args.dry_run,
        )
    finally:
        conn.close()

    if result["dry_run"]:
        print("Проверка без сохранения")
    else:
        print("Backfill Кулибина завершён")

    print(f"Всего карточек Кулибина: {result['total']}")
    print(f"Обработано: {result['processed']}")
    print(f"Назначено Земскову: {result['matched']}")
    print(f"Ручных назначений сохранено: {result['manual']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
