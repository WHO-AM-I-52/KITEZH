from __future__ import annotations

from typing import Any

from services.investmap_rf_batch_runner import run_batch
from services.investmap_rf_sync_plans import (
    BATCH_STATUS_RUNNING,
    complete_sync_batch,
    fail_sync_batch,
    finalize_stop_sync_plan,
    get_sync_plan,
)


def _get_running_batch(conn, plan_id: int, batch_id: int) -> dict[str, Any]:
    """Возвращает выполняемый пакет плана или сообщает об ошибке."""
    row = conn.execute(
        """
        SELECT
            id,
            plan_id,
            run_id,
            batch_number,
            status,
            global_ids_json
        FROM investmap_rf_sync_batches
        WHERE id = ? AND plan_id = ?
        """,
        (batch_id, plan_id),
    ).fetchone()

    if row is None:
        raise ValueError("Пакет синхронизации не найден.")

    batch = dict(row)

    if batch["status"] != BATCH_STATUS_RUNNING:
        raise ValueError("Выполнить можно только пакет со статусом running.")

    return batch


def _calculate_batch_metrics(report) -> dict[str, int]:
    """Преобразует BatchReport в счётчики таблицы синхронизации."""
    processed_cards_count = int(report.processed_count)
    failed_cards_count = int(report.errors_count)
    successful_cards_count = sum(
        item.status in {"new", "unchanged"}
        for item in report.items
    )
    changed_cards_count = sum(
        item.changes_count > 0
        for item in report.items
        if item.status in {"new", "unchanged"}
    )

    return {
        "processed_cards_count": processed_cards_count,
        "successful_cards_count": successful_cards_count,
        "failed_cards_count": failed_cards_count,
        "changed_cards_count": changed_cards_count,
    }

def _collect_batch_errors(report) -> str | None:
    """Собирает ошибки отдельных карточек в компактный текст для журнала."""
    errors = [
        f"{item.global_id}: {item.error}"
        for item in report.items
        if item.status == "error" and item.error
    ]

    return "\n".join(errors) if errors else None

def execute_sync_batch(
    conn,
    *,
    plan_id: int,
    batch_id: int,
    delay_seconds: float = 1.0,
) -> dict[str, Any]:
    """
    Выполняет один подготовленный пакет синхронизации.

    Функция не делает commit() и не закрывает conn. API-снимки сохраняются
    через существующий run_batch()/collect_card_snapshot().
    """
    plan = get_sync_plan(conn, plan_id)
    if plan is None:
        raise ValueError("План синхронизации не найден.")

    batch = _get_running_batch(conn, plan_id, batch_id)

    if int(plan["stop_requested"]) == 1:
        final_plan = finalize_stop_sync_plan(conn, plan_id=plan_id)
        return {
            "status": "stopped_before_start",
            "plan": final_plan,
            "batch_id": batch_id,
            "report": None,
        }

    try:
        import json

        global_ids = json.loads(batch["global_ids_json"])

        if not isinstance(global_ids, list) or not global_ids:
            raise ValueError("Пакет не содержит корректного списка global_id.")

        normalized_ids = []
        for value in global_ids:
            if isinstance(value, bool):
                raise ValueError("Пакет содержит некорректный global_id.")

            global_id = int(value)
            if global_id <= 0:
                raise ValueError("Пакет содержит некорректный global_id.")

            normalized_ids.append(global_id)

        report = run_batch(
            global_ids=normalized_ids,
            delay_seconds=delay_seconds,
        )

        metrics = _calculate_batch_metrics(report)
        batch_errors = _collect_batch_errors(report)

        if report.interrupted:
            summary = fail_sync_batch(
                conn,
                plan_id=plan_id,
                batch_id=batch_id,
                error_message="Выполнение пакета было прервано.",
            )
            return {
                "status": "interrupted",
                "plan_status": summary,
                "batch_id": batch_id,
                "report": report,
            }

        summary = complete_sync_batch(
            conn,
            plan_id=plan_id,
            batch_id=batch_id,
            **metrics,
        )

        if batch_errors:
            conn.execute(
                """
                UPDATE investmap_rf_sync_batches
                SET error_message = ?
                WHERE id = ? AND plan_id = ?
                """,
                (
                    batch_errors,
                    batch_id,
                    plan_id,
                ),
            )

        return {
            "status": "completed",
            "plan_status": summary,
            "batch_id": batch_id,
            "report": report,
            "item_errors": batch_errors,
        }

    except Exception as exc:
        summary = fail_sync_batch(
            conn,
            plan_id=plan_id,
            batch_id=batch_id,
            error_message=f"{type(exc).__name__}: {exc}",
        )
        return {
            "status": "failed",
            "plan_status": summary,
            "batch_id": batch_id,
            "report": None,
        }
