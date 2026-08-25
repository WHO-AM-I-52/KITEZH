"""Одноразовое заполнение назначений управляющих по сохранённым API-снимкам."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from types import SimpleNamespace

from db import get_db
from services.investmap_rf_manager_assignment import (
    MATCH_STATUS_AMBIGUOUS,
    MATCH_STATUS_MANUAL,
    MATCH_STATUS_MATCHED,
    MATCH_STATUS_UNMATCHED,
    update_card_manager_assignment,
)


_STATUS_LABELS = {
    MATCH_STATUS_MATCHED: "matched",
    MATCH_STATUS_UNMATCHED: "unmatched",
    MATCH_STATUS_AMBIGUOUS: "ambiguous",
    MATCH_STATUS_MANUAL: "manual",
}


def _latest_cards(conn: sqlite3.Connection):
    return conn.execute(
        """
        SELECT
            snapshots.global_id,
            snapshots.payload_json
        FROM investmap_rf_card_snapshots AS snapshots
        INNER JOIN (
            SELECT global_id, MAX(id) AS snapshot_id
            FROM investmap_rf_card_snapshots
            GROUP BY global_id
        ) AS latest
            ON latest.snapshot_id = snapshots.id
        ORDER BY snapshots.global_id
        """
    ).fetchall()


def _parse_payload(payload_json: str | None) -> dict:
    if not payload_json:
        return {}

    payload = json.loads(payload_json)

    if not isinstance(payload, dict):
        raise ValueError("payload_json должен содержать JSON-объект.")

    return payload


def backfill_manager_assignments(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """
    Создаёт или обновляет назначения по последнему snapshot каждой карточки.

    При dry_run все изменения откатываются после обработки и возвращается
    расчётная статистика.
    """
    counters = Counter()
    cards = _latest_cards(conn)

    try:
        for row in cards:
            card = SimpleNamespace(
                global_id=int(row["global_id"]),
                payload=_parse_payload(row["payload_json"]),
            )
            result = update_card_manager_assignment(conn, card=card)
            status = result["status"]

            counters["processed"] += 1
            counters[_STATUS_LABELS.get(status, status)] += 1

            if result["notification_created"]:
                counters["notifications_created"] += 1

        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    except Exception:
        conn.rollback()
        raise

    return {
        "cards_total": len(cards),
        "processed": counters["processed"],
        "matched": counters[MATCH_STATUS_MATCHED],
        "unmatched": counters[MATCH_STATUS_UNMATCHED],
        "ambiguous": counters[MATCH_STATUS_AMBIGUOUS],
        "manual": counters[MATCH_STATUS_MANUAL],
        "notifications_created": counters["notifications_created"],
        "dry_run": int(dry_run),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Заполняет назначения территориальных управляющих по "
            "последнему сохранённому API-снимку каждой карточки."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Проверить результат без сохранения изменений. "
            "Транзакция будет откатена."
        ),
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    conn = get_db()

    try:
        result = backfill_manager_assignments(
            conn,
            dry_run=args.dry_run,
        )
    finally:
        conn.close()

    mode = "Проверка без сохранения" if args.dry_run else "Backfill завершён"
    print(mode)
    print(f"Всего карточек: {result['cards_total']}")
    print(f"Обработано: {result['processed']}")
    print(f"Сопоставлено: {result['matched']}")
    print(f"Не найдено: {result['unmatched']}")
    print(f"Неоднозначно: {result['ambiguous']}")
    print(f"Ручных назначений сохранено: {result['manual']}")
    print(f"Новых уведомлений: {result['notifications_created']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
