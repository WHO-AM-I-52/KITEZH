"""Автоматический запуск синхронизации по настраиваемому расписанию МСК."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable

from services.investmap_rf_sync_plans import (
    PLAN_STATUS_PAUSED,
    PLAN_STATUS_RUNNING,
    PLAN_STATUS_STOPPING,
    create_sync_plan,
    start_sync_plan,
)

MOSCOW_TZ = timezone(timedelta(hours=3), name="MSK")
DAILY_PLAN_NAME = "Автоматическая синхронизация активных площадок"
DAILY_BATCH_SIZE = 5
DAILY_INTERVAL_MINUTES = 10


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _schedule_is_due(schedule: dict[str, Any], current_msk: datetime) -> bool:
    if not schedule["is_enabled"]:
        return False
    frequency = schedule["frequency"]
    if frequency not in {"daily", "weekly", "weekdays", "monthly"}:
        raise ValueError("Неизвестный режим автоматического расписания.")
    if frequency == "weekdays" and current_msk.weekday() >= 5:
        return False
    if frequency == "weekly" and current_msk.weekday() != int(schedule["weekday"]):
        return False
    if frequency == "monthly" and current_msk.day != int(schedule["month_day"]):
        return False
    start_time = time.fromisoformat(schedule["start_time_msk"])
    return current_msk.time() >= start_time


def _get_daily_record(conn, scheduled_date_msk: str):
    return conn.execute(
        "SELECT * FROM investmap_rf_daily_sync_runs WHERE scheduled_date_msk = ?",
        (scheduled_date_msk,),
    ).fetchone()


def _get_or_create_daily_plan(conn, schedule: dict[str, Any]) -> dict[str, Any]:
    if schedule["plan_id"] is not None:
        row = conn.execute(
            "SELECT * FROM investmap_rf_sync_plans WHERE id = ?",
            (schedule["plan_id"],),
        ).fetchone()
        if row is None:
            raise ValueError("Автоматический план расписания не найден.")
        return dict(row)

    row = conn.execute(
        "SELECT * FROM investmap_rf_sync_plans WHERE name = ? ORDER BY id ASC LIMIT 1",
        (DAILY_PLAN_NAME,),
    ).fetchone()
    plan = dict(row) if row is not None else create_sync_plan(
        conn,
        name=DAILY_PLAN_NAME,
        batch_size=DAILY_BATCH_SIZE,
        interval_minutes=DAILY_INTERVAL_MINUTES,
    )
    conn.execute(
        "UPDATE investmap_rf_sync_schedule SET plan_id = ? WHERE id = 1",
        (int(plan["id"]),),
    )
    return plan


def _create_daily_record(
    conn,
    *,
    scheduled_date_msk: str,
    scheduled_at_utc: str,
    status: str,
    plan_id: int | None = None,
    run_id: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    cursor = conn.execute(
        """
        INSERT INTO investmap_rf_daily_sync_runs (
            scheduled_date_msk, scheduled_at_utc, status,
            plan_id, run_id, reason, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (scheduled_date_msk, scheduled_at_utc, status,
         plan_id, run_id, reason, scheduled_at_utc),
    )
    row = conn.execute(
        "SELECT * FROM investmap_rf_daily_sync_runs WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return dict(row)


def run_weekday_daily_sync(
    get_connection: Callable[[], Any],
    *,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Обрабатывает сегодняшний запуск; имя сохранено для фонового модуля.

    После выбранного времени возможен поздний старт в тот же день.
    Прошлые даты не догоняются; на дату создаётся не более одной записи.
    """
    current_utc = now_utc or _utc_now()
    current_msk = current_utc.astimezone(MOSCOW_TZ)
    scheduled_date_msk = current_msk.date().isoformat()
    conn = get_connection()
    try:
        # Сериализует проверку журнала и создание запуска между процессами.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM investmap_rf_sync_schedule WHERE id = 1"
        ).fetchone()
        if row is None:
            raise ValueError("Настройки расписания отсутствуют; примените миграции.")
        schedule = dict(row)
        if not _schedule_is_due(schedule, current_msk):
            conn.rollback()
            return {"status": "not_due", "scheduled_date_msk": scheduled_date_msk}

        plan = _get_or_create_daily_plan(conn, schedule)
        existing = _get_daily_record(conn, scheduled_date_msk)
        if existing is not None:
            conn.commit()
            return {
                "status": "already_handled_today",
                "scheduled_date_msk": scheduled_date_msk,
                "record": dict(existing),
            }

        scheduled_at_utc = _utc_text(current_utc)
        if plan["status"] in {PLAN_STATUS_RUNNING, PLAN_STATUS_PAUSED, PLAN_STATUS_STOPPING}:
            reason = (
                "Ежедневный запуск пропущен: предыдущий автоматический "
                f"план #{plan['id']} имеет статус {plan['status']}."
            )
            record = _create_daily_record(
                conn, scheduled_date_msk=scheduled_date_msk,
                scheduled_at_utc=scheduled_at_utc, status="skipped_overlap",
                plan_id=int(plan["id"]), reason=reason,
            )
            conn.commit()
            return {"status": "skipped_overlap", "scheduled_date_msk": scheduled_date_msk, "record": record}

        try:
            started = start_sync_plan(conn, plan_id=int(plan["id"]))
        except ValueError as exc:
            message = str(exc)
            status = "skipped_empty_registry" if "нет активных площадок" in message else "failed"
            record = _create_daily_record(
                conn, scheduled_date_msk=scheduled_date_msk,
                scheduled_at_utc=scheduled_at_utc, status=status,
                plan_id=int(plan["id"]), reason=message,
            )
            conn.commit()
            return {"status": status, "scheduled_date_msk": scheduled_date_msk, "record": record}

        record = _create_daily_record(
            conn, scheduled_date_msk=scheduled_date_msk,
            scheduled_at_utc=scheduled_at_utc, status="created",
            plan_id=int(started["plan"]["id"]), run_id=int(started["run_id"]),
        )
        conn.commit()
        return {
            "status": "created", "scheduled_date_msk": scheduled_date_msk,
            "record": record, "plan": started["plan"], "run": started["run"],
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
