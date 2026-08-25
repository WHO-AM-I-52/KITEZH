"""CLI для read-only получения и сохранения снимка одной карточки Инвесткарты РФ."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from typing import Callable, Sequence

from db import get_db
from services.investmap_rf_client import InvestmapRfClientError, fetch_card
from services.investmap_rf_manager_assignment import update_card_manager_assignment
from services.investmap_rf_snapshot_store import (
    SnapshotSaveResult,
    save_card_snapshot,
)


def collect_card_snapshot(
    global_id: int,
    *,
    fetch_card_fn: Callable = fetch_card,
    get_db_fn: Callable[[], sqlite3.Connection] = get_db,
) -> SnapshotSaveResult:
    """
    Получает одну карточку, сохраняет новый снимок и управляет транзакцией.

    Соединение создаётся и закрывается в этой функции. При любой ошибке
    незавершённая транзакция откатывается.
    """
    conn = get_db_fn()
    try:
        card = fetch_card_fn(global_id)
        result = save_card_snapshot(conn, card)
        update_card_manager_assignment(conn, card=card)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Получает одну карточку API Инвестиционной карты РФ "
            "и сохраняет изменённый SQLite-снимок."
        )
    )
    parser.add_argument(
        "global_id",
        type=int,
        help="Положительный global_id инвестиционной площадки.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.global_id <= 0:
        print("global_id должен быть положительным числом.", file=sys.stderr)
        return 2

    try:
        result = collect_card_snapshot(args.global_id)
    except InvestmapRfClientError as exc:
        print(f"Не удалось получить карточку: {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"Не удалось сохранить снимок в SQLite: {exc}", file=sys.stderr)
        return 1

    status = "создан новый снимок" if result.is_new_snapshot else "изменений нет"
    print(
        f"Карточка {args.global_id}: {status}; "
        f"snapshot_id={result.snapshot_id}; "
        f"changes={result.changes_count}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
