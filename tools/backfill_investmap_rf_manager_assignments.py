"""Одноразовое заполнение назначений управляющих по сохранённым API-снимкам."""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from types import SimpleNamespace
from typing import Any

from db import get_db
from services.investmap_rf_manager_assignment import (
    MATCH_STATUS_AMBIGUOUS,
    MATCH_STATUS_MANUAL,
    MATCH_STATUS_MATCHED,
    MATCH_STATUS_UNMATCHED,
    ZIMIN_MANAGER_NAME,
    is_zimin_contact_person,
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


def _parse_payload(payload_json: str | None) -> dict[str, Any]:
    if not payload_json:
        return {}

    payload = json.loads(payload_json)

    if not isinstance(payload, dict):
        raise ValueError("payload_json должен содержать JSON-объект.")

    return payload


def _existing_assignment(conn: sqlite3.Connection, global_id: int):
    return conn.execute(
        """
        SELECT
            manager_name,
            assignment_source
        FROM investmap_rf_card_manager_assignments
        WHERE global_id = ?
        """,
        (global_id,),
    ).fetchone()


def backfill_manager_assignments(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    zimin_only: bool = False,
) -> dict[str, Any]:
    """
    Создаёт или обновляет назначения по последнему snapshot каждой карточки.

    При dry_run все изменения откатываются после обработки и возвращается
    расчётная статистика. При zimin_only обрабатываются только карточки,
    в contactPerson которых указан Зимин Дмитрий Валерьевич.
    """
    counters = Counter()
    manager_changes: list[dict[str, Any]] = []
    cards = _latest_cards(conn)

    try:
        for row in cards:
            card = SimpleNamespace(
                global_id=int(row["global_id"]),
                payload=_parse_payload(row["payload_json"]),
            )

            if zimin_only and not is_zimin_contact_person(card):
                continue

            existing = _existing_assignment(conn, card.global_id)
            previous_manager_name = (
                existing["manager_name"] if existing is not None else None
            )
            previous_assignment_source = (
                existing["assignment_source"] if existing is not None else None
            )

            result = update_card_manager_assignment(
                conn,
                card=card,
                notify_admins=False,
            )
            assignment = result["assignment"]
            status = result["status"]

            counters["processed"] += 1
            counters[_STATUS_LABELS.get(status, status)] += 1

            if zimin_only:
                counters["zimin_contact_cards"] += 1
            if zimin_only and status == MATCH_STATUS_MANUAL:
                counters["manual_assignments_skipped"] += 1
            elif (
                zimin_only
                and previous_manager_name != assignment["manager_name"]
                and assignment["manager_name"] == ZIMIN_MANAGER_NAME
            ):
                manager_changes.append(
                    {
                        "global_id": card.global_id,
                        "previous_manager_name": previous_manager_name,
                        "previous_assignment_source": previous_assignment_source,
                        "manager_name": assignment["manager_name"],
                        "assignment_source": assignment["assignment_source"],
                    }
                )
                counters["reassigned_to_zimin"] += 1
            elif (
                zimin_only
                and assignment["manager_name"] == ZIMIN_MANAGER_NAME
                and previous_manager_name == ZIMIN_MANAGER_NAME
            ):
                counters["already_assigned_to_zimin"] += 1

            if result["notification_created"]:
                counters["matching_issues_created"] += 1

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
        "matching_issues_created": counters["matching_issues_created"],
        "zimin_contact_cards": counters["zimin_contact_cards"],
        "reassigned_to_zimin": counters["reassigned_to_zimin"],
        "already_assigned_to_zimin": counters["already_assigned_to_zimin"],
        "manual_assignments_skipped": counters["manual_assignments_skipped"],
        "manager_changes": manager_changes,
        "dry_run": int(dry_run),
        "zimin_only": int(zimin_only),
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
    parser.add_argument(
        "--zimin-only",
        action="store_true",
        help=(
            "Обработать только карточки, в contactPerson которых "
            "указан Зимин Дмитрий Валерьевич."
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
            zimin_only=args.zimin_only,
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
    print(f"Новых проблем сопоставления: {result['matching_issues_created']}")

    if args.zimin_only:
        print(f"Площадок с Зиминым в contactPerson: {result['zimin_contact_cards']}")
        print(f"Переназначено на Зимина: {result['reassigned_to_zimin']}")
        print(f"Уже назначено Зимину: {result['already_assigned_to_zimin']}")
        print(
            "Пропущено ручных назначений: "
            f"{result['manual_assignments_skipped']}"
        )

        changes = result["manager_changes"]
        if changes:
            print("\nИзменения:")
            for change in changes:
                previous_manager_name = (
                    change["previous_manager_name"] or "Не назначен"
                )
                print(
                    f"{change['global_id']}: {previous_manager_name} → "
                    f"{change['manager_name']}"
                )
        else:
            print("\nИзменения: нет")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
