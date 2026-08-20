from __future__ import annotations

import json
from typing import Any, Callable

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


def _read_batch_for_execution(
    get_connection: Callable[[], Any],
    *,
    plan_id: int,
    batch_id: int,
) -> dict[str, Any]:
    """Проверяет план и пакет, затем освобождает SQLite до API-запросов."""
    conn = get_connection()

    try:
        plan = get_sync_plan(conn, plan_id)
        if plan is None:
            raise ValueError("План синхронизации не найден.")

        batch = _get_running_batch(conn, plan_id, batch_id)

        if int(plan["stop_requested"]) == 1:
            final_plan = finalize_stop_sync_plan(conn, plan_id=plan_id)
            conn.commit()

            return {
                "stop_requested": True,
                "plan": final_plan,
                "batch": batch,
                "global_ids": [],
            }

        try:
            global_ids = json.loads(batch["global_ids_json"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Пакет не содержит корректного списка global_id."
            ) from exc

        if not isinstance(global_ids, list) or not global_ids:
            raise ValueError("Пакет не содержит корректного списка global_id.")

        normalized_ids: list[int] = []

        for value in global_ids:
            if isinstance(value, bool):
                raise ValueError("Пакет содержит некорректный global_id.")

            global_id = int(value)

            if global_id <= 0:
                raise ValueError("Пакет содержит некорректный global_id.")

            normalized_ids.append(global_id)

        return {
            "stop_requested": False,
            "plan": plan,
            "batch": batch,
            "global_ids": normalized_ids,
        }

    finally:
        conn.close()


def _save_completed_batch(
    get_connection: Callable[[], Any],
    *,
    plan_id: int,
    batch_id: int,
    report,
) -> dict[str, Any]:
    """Сохраняет итоги пакета после завершения API-запросов."""
    conn = get_connection()

    try:
        metrics = _calculate_batch_metrics(report)
        summary = complete_sync_batch(
            conn,
            plan_id=plan_id,
            batch_id=batch_id,
            **metrics,
        )

        batch_errors = _collect_batch_errors(report)

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

        conn.commit()

        return {
            "status": "completed",
            "plan_status": summary,
            "batch_id": batch_id,
            "report": report,
            "item_errors": batch_errors,
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def _save_failed_batch(
    get_connection: Callable[[], Any],
    *,
    plan_id: int,
    batch_id: int,
    error_message: str,
) -> dict[str, Any]:
    """Фиксирует фатальную ошибку пакета отдельным подключением."""
    conn = get_connection()

    try:
        summary = fail_sync_batch(
            conn,
            plan_id=plan_id,
            batch_id=batch_id,
            error_message=error_message,
        )
        conn.commit()

        return {
            "status": "failed",
            "plan_status": summary,
            "batch_id": batch_id,
            "report": None,
            "item_errors": error_message,
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def execute_sync_batch(
    get_connection: Callable[[], Any],
    *,
    plan_id: int,
    batch_id: int,
    delay_seconds: float = 1.0,
) -> dict[str, Any]:
    """
    Выполняет один подготовленный пакет синхронизации.

    Ключевое правило: API-вызовы run_batch() выполняются без открытой
    транзакции соединения планов. Это предотвращает database is locked.

    get_connection — функция без аргументов, которая создаёт новое соединение
    SQLite, например get_db из модуля db.
    """
    try:
        prepared = _read_batch_for_execution(
            get_connection,
            plan_id=plan_id,
            batch_id=batch_id,
        )

        if prepared["stop_requested"]:
            return {
                "status": "stopped_before_start",
                "plan_status": prepared["plan"],
                "batch_id": batch_id,
                "report": None,
                "item_errors": None,
            }

        report = run_batch(
            global_ids=prepared["global_ids"],
            delay_seconds=delay_seconds,
        )

        if report.interrupted:
            return _save_failed_batch(
                get_connection,
                plan_id=plan_id,
                batch_id=batch_id,
                error_message="Выполнение пакета было прервано.",
            )

        return _save_completed_batch(
            get_connection,
            plan_id=plan_id,
            batch_id=batch_id,
            report=report,
        )

    except Exception as exc:
        return _save_failed_batch(
            get_connection,
            plan_id=plan_id,
            batch_id=batch_id,
            error_message=f"{type(exc).__name__}: {exc}",
        )
