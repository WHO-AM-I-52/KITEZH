from __future__ import annotations

from typing import Any

from services.investmap_rf_batch_runner import run_batch


def get_active_registry_global_ids(conn) -> list[int]:
    """Возвращает активные global_id реестра в стабильном порядке."""
    rows = conn.execute(
        """
        SELECT global_id
        FROM investmap_rf_monitored_cards
        WHERE is_active = 1
        ORDER BY global_id
        """
    ).fetchall()

    return [int(row["global_id"]) for row in rows]


def run_active_registry_batch(
    conn,
    *,
    max_cards: int | None = None,
    **batch_kwargs: Any,
) -> dict[str, Any]:
    """
    Запускает мониторинг для активных площадок реестра.

    Соединение conn передаётся вызывающей стороной и не закрывается.
    commit() не выполняется: транзакцией управляет вызывающая сторона.
    """
    global_ids = get_active_registry_global_ids(conn)

    if max_cards is not None:
        if isinstance(max_cards, bool) or not isinstance(max_cards, int):
            raise ValueError("max_cards должен быть целым числом или None.")
        if max_cards < 1:
            raise ValueError("max_cards должен быть положительным числом.")
        global_ids = global_ids[:max_cards]

    if not global_ids:
        return {
            "requested_count": 0,
            "global_ids": [],
            "batch": None,
        }

    batch_result = run_batch(
        global_ids=global_ids,
        **batch_kwargs,
    )

    return {
        "requested_count": len(global_ids),
        "global_ids": global_ids,
        "batch": batch_result,
    }
