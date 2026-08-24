"""Автоматический запуск синхронизации активных площадок по будням."""

from __future__ import annotations

from datetime import datetime, time, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from services.investmap_rf_sync_plans import (
    PLAN_STATUS_PAUSED,
    PLAN_STATUS_RUNNING,
    PLAN_STATUS_STOPPING,
    create_sync_plan,
    start_sync_plan,
)

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
DAILY_START_TIME = time(hour=13, minute=0)
DAILY_PLAN_NAME = "Автоматическая синхронизация активных площадок"
DAILY_BATCH_SIZE = 5
DAILY_INTERVAL_MINUTES = 10


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _get_daily_record(conn, scheduled_date_msk: str):
    return conn.execute(
        """
        SELECT *
        FROM investmap_rf_daily_sync_runs
        WHERE scheduled_date_msk = ?
        """,
        (scheduled_date_msk,),
    ).fetchone()


def _get_or_create_daily_plan(conn) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT *
        FROM investmap_rf_sync_plans
        WHERE name = ?
        ORDER BY id ASC
        LIMIT 1
        """,
        (DAILY_PLAN_NAME,),
    ).fetchone()

    if row is not None:
        return dict(row)

    return create_sync_plan(
        conn,
        name=DAILY_PLAN_NAME,
        batch_size=DAILY_BATCH_SIZE,
        interval_minutes=DAILY_INTERVAL_MINUTES,
    )


def _get_active_daily_plan(conn) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM investmap_rf_sync_plans
        WHERE name = ?
          AND status IN (?, ?, ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (
            DAILY_PLAN_NAME,
            PLAN_STATUS_RUNNING,
            PLAN_STATUS_PAUSED,
            PLAN_STATUS_STOPPING,
        ),
    ).fetchone()
    return dict(row) if row is not None else None


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
            scheduled_date_msk,
            scheduled_at_utc,
            status,
            plan_id,
            run_id,
            reason,
            created_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            scheduled_date_msk,
            scheduled_at_utc,
            status,
            plan_id,
            run_id,
            reason,
            scheduled_at_utc,
        ),
    )

    row = conn.execute(
        """
        SELECT *
        FROM investmap_rf_daily_sync_runs
        WHERE id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()
    return dict(row)


def run_weekday_daily_sync(
    get_connection: Callable[[], Any],
    *,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """
    Создаёт один ежедневный цикл синхронизации после 13:00 MSK в будни.

    Функция идемпотентна в пределах даты Москвы: таблица журнала не позволяет
    создать два автоматических запуска после нескольких тиков или рестарта.
    """
    current_utc = now_utc or _utc_now()
    current_msk = current_utc.astimezone(MOSCOW_TZ)

    if current_msk.weekday() >= 5:
        return {
            "status": "not_workday",
            "scheduled_date_msk": current_msk.date().isoformat(),
        }

    if current_msk.time() < DAILY_START_TIME:
        return {
            "status": "not_due",
            "scheduled_date_msk": current_msk.date().isoformat(),
        }

    scheduled_date_msk = current_msk.date().isoformat()
    scheduled_at_utc = _utc_text(current_utc)
    conn = get_connection()

    try:
        existing = _get_daily_record(conn, scheduled_date_msk)
        if existing is not None:
            return {
                "status": "already_handled_today",
                "scheduled_date_msk": scheduled_date_msk,
                "record": dict(existing),
            }

        active_plan = _get_active_daily_plan(conn)
        if active_plan is not None:
            reason = (
                "Ежедневный запуск пропущен: предыдущий автоматический "
                f"план #{active_plan['id']} имеет статус "
                f"{active_plan['status']}."
            )
            record = _create_daily_record(
                conn,
                scheduled_date_msk=scheduled_date_msk,
                scheduled_at_utc=scheduled_at_utc,
                status="skipped_overlap",
                plan_id=int(active_plan["id"]),
                reason=reason,
            )
            conn.commit()
            return {
                "status": "skipped_overlap",
                "scheduled_date_msk": scheduled_date_msk,
                "record": record,
            }

        plan = _get_or_create_daily_plan(conn)

        try:
            started = start_sync_plan(
                conn,
                plan_id=int(plan["id"]),
            )
        except ValueError as exc:
            message = str(exc)
            status = (
                "skipped_empty_registry"
                if "нет активных площадок" in message
                else "failed"
            )
            record = _create_daily_record(
                conn,
                scheduled_date_msk=scheduled_date_msk,
                scheduled_at_utc=scheduled_at_utc,
                status=status,
                plan_id=int(plan["id"]),
                reason=message,
            )
            conn.commit()
            return {
                "status": status,
                "scheduled_date_msk": scheduled_date_msk,
                "record": record,
            }

        record = _create_daily_record(
            conn,
            scheduled_date_msk=scheduled_date_msk,
            scheduled_at_utc=scheduled_at_utc,
            status="created",
            plan_id=int(started["plan"]["id"]),
            run_id=int(started["run_id"]),
        )
        conn.commit()
        return {
            "status": "created",
            "scheduled_date_msk": scheduled_date_msk,
            "record": record,
            "plan": started["plan"],
            "run": started["run"],
        }

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
