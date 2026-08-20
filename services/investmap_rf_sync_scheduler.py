from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from services.investmap_rf_sync_executor import execute_sync_batch
from services.investmap_rf_sync_plans import (
    PLAN_STATUS_RUNNING,
    finalize_stop_sync_plan,
    prepare_next_sync_batch,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None

    normalized = value.replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def get_due_sync_plan_ids(conn, *, now_utc: datetime | None = None) -> list[int]:
    """
    Возвращает планы, которым пора запускать следующий пакет.

    План без next_run_at_utc не считается готовым к запуску.
    """
    now = now_utc or _utc_now()

    rows = conn.execute(
        """
        SELECT id, next_run_at_utc
        FROM investmap_rf_sync_plans
        WHERE is_enabled = 1
          AND status = ?
          AND stop_requested = 0
          AND next_run_at_utc IS NOT NULL
        ORDER BY next_run_at_utc, id
        """,
        (PLAN_STATUS_RUNNING,),
    ).fetchall()

    due_plan_ids: list[int] = []

    for row in rows:
        next_run_at = _parse_utc(row["next_run_at_utc"])
        if next_run_at is not None and next_run_at <= now:
            due_plan_ids.append(int(row["id"]))

    return due_plan_ids


def run_due_sync_plans(
    get_connection: Callable[[], Any],
    *,
    delay_seconds: float = 1.0,
    now_utc: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Финализирует запрошенные остановки и выполняет не более одного
    пакета для каждого готового плана.

    Функция предназначена для вызова планировщиком раз в минуту.
    Каждая операция использует собственное подключение к БД.
    """
    conn = get_connection()

    try:
        stopping_rows = conn.execute(
            """
            SELECT id
            FROM investmap_rf_sync_plans
            WHERE status = ?
              AND stop_requested = 1
            ORDER BY id
            """,
            ("stopping",),
        ).fetchall()
        stopping_plan_ids = [int(row["id"]) for row in stopping_rows]

        due_plan_ids = get_due_sync_plan_ids(conn, now_utc=now_utc)
    finally:
        conn.close()

    results: list[dict[str, Any]] = []

    for plan_id in stopping_plan_ids:
        conn = get_connection()

        try:
            final_plan = finalize_stop_sync_plan(conn, plan_id=plan_id)
            conn.commit()
            results.append(
                {
                    "plan_id": plan_id,
                    "status": "stopped",
                    "plan": final_plan,
                }
            )
        except Exception as exc:
            conn.rollback()
            results.append(
                {
                    "plan_id": plan_id,
                    "status": "stop_finalize_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        finally:
            conn.close()

    for plan_id in due_plan_ids:
        conn = get_connection()

        try:
            prepared_batch = prepare_next_sync_batch(conn, plan_id=plan_id)

            if prepared_batch is None:
                plan = conn.execute(
                    """
                    SELECT stop_requested
                    FROM investmap_rf_sync_plans
                    WHERE id = ?
                    """,
                    (plan_id,),
                ).fetchone()

                if plan is not None and int(plan["stop_requested"]) == 1:
                    final_plan = finalize_stop_sync_plan(conn, plan_id=plan_id)
                    conn.commit()
                    results.append(
                        {
                            "plan_id": plan_id,
                            "status": "stopped",
                            "plan": final_plan,
                        }
                    )
                else:
                    conn.commit()
                    results.append(
                        {
                            "plan_id": plan_id,
                            "status": "not_due_or_completed",
                        }
                    )

                continue

            conn.commit()

        except Exception as exc:
            conn.rollback()
            results.append(
                {
                    "plan_id": plan_id,
                    "status": "prepare_failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            continue

        finally:
            conn.close()

        execution_result = execute_sync_batch(
            get_connection,
            plan_id=plan_id,
            batch_id=prepared_batch["batch_id"],
            delay_seconds=delay_seconds,
        )

        results.append(
            {
                "plan_id": plan_id,
                "batch_id": prepared_batch["batch_id"],
                "batch_number": prepared_batch["batch_number"],
                "global_ids": prepared_batch["global_ids"],
                "status": execution_result["status"],
                "item_errors": execution_result.get("item_errors"),
            }
        )

    return results
