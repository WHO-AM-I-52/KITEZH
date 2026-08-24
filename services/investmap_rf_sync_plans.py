from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from services.investmap_rf_registry_runner import get_active_registry_global_ids


PLAN_STATUS_IDLE = "idle"
PLAN_STATUS_RUNNING = "running"
PLAN_STATUS_PAUSED = "paused"
PLAN_STATUS_STOPPING = "stopping"
PLAN_STATUS_COMPLETED = "completed"
PLAN_STATUS_FAILED = "failed"

RUN_STATUS_RUNNING = "running"
RUN_STATUS_PAUSED = "paused"
RUN_STATUS_STOPPED = "stopped"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"

BATCH_STATUS_PENDING = "pending"
BATCH_STATUS_RUNNING = "running"
BATCH_STATUS_COMPLETED = "completed"
BATCH_STATUS_FAILED = "failed"
BATCH_STATUS_STOPPED = "stopped"

MIN_BATCH_SIZE = 1
MAX_BATCH_SIZE = 100
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 1440


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _utc_after_minutes(minutes: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(minutes=minutes)
    ).isoformat(timespec="seconds")


def _row_to_dict(row) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _validate_batch_size(batch_size: int) -> int:
    if isinstance(batch_size, bool) or not isinstance(batch_size, int):
        raise ValueError("Размер пакета должен быть целым числом.")

    if not MIN_BATCH_SIZE <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(
            f"Размер пакета должен быть от {MIN_BATCH_SIZE} до {MAX_BATCH_SIZE}."
        )

    return batch_size


def _validate_interval_minutes(interval_minutes: int) -> int:
    if isinstance(interval_minutes, bool) or not isinstance(interval_minutes, int):
        raise ValueError("Интервал должен быть целым числом минут.")

    if not MIN_INTERVAL_MINUTES <= interval_minutes <= MAX_INTERVAL_MINUTES:
        raise ValueError(
            "Интервал должен быть "
            f"от {MIN_INTERVAL_MINUTES} до {MAX_INTERVAL_MINUTES} минут."
        )

    return interval_minutes


def get_sync_plan(conn, plan_id: int) -> dict[str, Any] | None:
    """Возвращает один план синхронизации."""
    row = conn.execute(
        """
        SELECT *
        FROM investmap_rf_sync_plans
        WHERE id = ?
        """,
        (plan_id,),
    ).fetchone()
    return _row_to_dict(row)


def get_sync_plans(conn) -> list[dict[str, Any]]:
    """Возвращает все планы в порядке последнего изменения."""
    rows = conn.execute(
        """
        SELECT *
        FROM investmap_rf_sync_plans
        ORDER BY updated_at_utc DESC, id DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_latest_sync_run(conn, plan_id: int) -> dict[str, Any] | None:
    """Возвращает последний цикл выполнения плана."""
    row = conn.execute(
        """
        SELECT *
        FROM investmap_rf_sync_runs
        WHERE plan_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (plan_id,),
    ).fetchone()
    return _row_to_dict(row)

def _get_run_global_ids(conn, run_id: int) -> list[int]:
    """Возвращает зафиксированный список площадок конкретного цикла."""
    row = conn.execute(
        """
        SELECT global_ids_json
        FROM investmap_rf_sync_runs
        WHERE id = ?
        """,
        (run_id,),
    ).fetchone()

    if row is None:
        raise ValueError("Цикл синхронизации не найден.")

    try:
        raw_ids = json.loads(row["global_ids_json"])
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Цикл синхронизации содержит некорректный список global_id."
        ) from exc

    if not isinstance(raw_ids, list):
        raise ValueError(
            "Цикл синхронизации содержит некорректный список global_id."
        )

    global_ids: list[int] = []
    seen: set[int] = set()

    for value in raw_ids:
        if isinstance(value, bool):
            raise ValueError(
                "Цикл синхронизации содержит некорректный global_id."
            )

        global_id = int(value)

        if global_id <= 0:
            raise ValueError(
                "Цикл синхронизации содержит некорректный global_id."
            )

        if global_id not in seen:
            seen.add(global_id)
            global_ids.append(global_id)

    return global_ids

def get_latest_sync_batch(conn, plan_id: int) -> dict[str, Any] | None:
    """Возвращает последний пакет выполнения плана."""
    row = conn.execute(
        """
        SELECT *
        FROM investmap_rf_sync_batches
        WHERE plan_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (plan_id,),
    ).fetchone()
    return _row_to_dict(row)
    
def get_sync_batches_for_run(
    conn,
    run_id: int,
) -> list[dict[str, Any]]:
    """Возвращает все пакеты указанного цикла в порядке выполнения."""
    rows = conn.execute(
        """
        SELECT *
        FROM investmap_rf_sync_batches
        WHERE run_id = ?
        ORDER BY batch_number ASC, id ASC
        """,
        (run_id,),
    ).fetchall()
    return [dict(row) for row in rows]

def _extract_failed_global_ids(batches: list[dict[str, Any]]) -> list[int]:
    """Извлекает уникальные ID площадок из диагностических сообщений пакетов."""
    failed_ids: set[int] = set()

    for batch in batches:
        message = str(batch.get("error_message") or "")

        for match in re.finditer(r"(?<!\d)(\d+)\s*:", message):
            failed_ids.add(int(match.group(1)))

    return sorted(failed_ids)


def create_failed_sync_retry_job(
    conn,
    *,
    plan_id: int,
    source_run_id: int,
    created_by_user_id: int | None = None,
) -> dict[str, Any]:
    """
    Создаёт retry-задачу по ошибкам конкретного завершённого цикла.

    Список площадок извлекается из error_message ошибочных пакетов.
    Основной план, его cursor и исходный run не изменяются.
    """
    plan = get_sync_plan(conn, plan_id)
    if plan is None:
        raise ValueError("План синхронизации не найден.")

    source_run = conn.execute(
        """
        SELECT *
        FROM investmap_rf_sync_runs
        WHERE id = ? AND plan_id = ?
        """,
        (source_run_id, plan_id),
    ).fetchone()
    if source_run is None:
        raise ValueError("Исходный цикл синхронизации не найден.")

    batches = get_sync_batches_for_run(conn, source_run_id)
    failed_batches = [
        batch
        for batch in batches
        if int(batch["failed_cards_count"] or 0) > 0
        or str(batch["error_message"] or "").strip()
    ]
    global_ids = _extract_failed_global_ids(failed_batches)

    if not global_ids:
        raise ValueError(
            "В ошибочных пакетах не найдены идентификаторы площадок для повтора."
        )

    active_job = conn.execute(
        """
        SELECT id
        FROM investmap_rf_sync_retry_jobs
        WHERE plan_id = ?
          AND source_run_id = ?
          AND status IN ('pending', 'running')
        ORDER BY id DESC
        LIMIT 1
        """,
        (plan_id, source_run_id),
    ).fetchone()
    if active_job is not None:
        raise ValueError(
            "Для ошибок этого цикла уже создана незавершённая задача повтора."
        )

    now = _utc_now()
    cursor = conn.execute(
        """
        INSERT INTO investmap_rf_sync_retry_jobs (
            plan_id,
            source_run_id,
            global_ids_json,
            status,
            requested_cards_count,
            created_at_utc,
            created_by_user_id
        )
        VALUES (?, ?, ?, 'pending', ?, ?, ?)
        """,
        (
            plan_id,
            source_run_id,
            json.dumps(global_ids),
            len(global_ids),
            now,
            created_by_user_id,
        ),
    )

    row = conn.execute(
        """
        SELECT *
        FROM investmap_rf_sync_retry_jobs
        WHERE id = ?
        """,
        (cursor.lastrowid,),
    ).fetchone()
    return dict(row)

def get_sync_plan_status(conn, plan_id: int) -> dict[str, Any] | None:
    """Возвращает план вместе с последним циклом и пакетом."""
    plan = get_sync_plan(conn, plan_id)
    if plan is None:
        return None

    return {
        "plan": plan,
        "latest_run": get_latest_sync_run(conn, plan_id),
        "latest_batch": get_latest_sync_batch(conn, plan_id),
    }


def create_sync_plan(
    conn,
    *,
    name: str,
    batch_size: int = 5,
    interval_minutes: int = 10,
    created_by_user_id: int | None = None,
) -> dict[str, Any]:
    """Создаёт выключенный план синхронизации."""
    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise ValueError("Название плана обязательно.")

    batch_size = _validate_batch_size(batch_size)
    interval_minutes = _validate_interval_minutes(interval_minutes)
    now = _utc_now()

    cursor = conn.execute(
        """
        INSERT INTO investmap_rf_sync_plans (
            name,
            is_enabled,
            batch_size,
            interval_minutes,
            status,
            stop_requested,
            created_at_utc,
            updated_at_utc,
            created_by_user_id,
            updated_by_user_id
        )
        VALUES (?, 0, ?, ?, ?, 0, ?, ?, ?, ?)
        """,
        (
            normalized_name,
            batch_size,
            interval_minutes,
            PLAN_STATUS_IDLE,
            now,
            now,
            created_by_user_id,
            created_by_user_id,
        ),
    )

    return get_sync_plan(conn, cursor.lastrowid)


def update_sync_plan_settings(
    conn,
    *,
    plan_id: int,
    batch_size: int,
    interval_minutes: int,
    updated_by_user_id: int | None = None,
) -> dict[str, Any]:
    """
    Обновляет размер пакета и интервал.

    Новые значения применяются к следующему пакету. Текущий пакет не меняется.
    """
    if get_sync_plan(conn, plan_id) is None:
        raise ValueError("План синхронизации не найден.")

    batch_size = _validate_batch_size(batch_size)
    interval_minutes = _validate_interval_minutes(interval_minutes)

    conn.execute(
        """
        UPDATE investmap_rf_sync_plans
        SET
            batch_size = ?,
            interval_minutes = ?,
            updated_at_utc = ?,
            updated_by_user_id = ?
        WHERE id = ?
        """,
        (
            batch_size,
            interval_minutes,
            _utc_now(),
            updated_by_user_id,
            plan_id,
        ),
    )

    return get_sync_plan(conn, plan_id)


def start_sync_plan(
    conn,
    *,
    plan_id: int,
    started_by_user_id: int | None = None,
) -> dict[str, Any]:
    """
    Запускает новый полный цикл.

    Обработка пакетов выполняется отдельным исполнителем; эта функция
    только готовит состояние и создаёт запись цикла.
    """
    plan = get_sync_plan(conn, plan_id)
    if plan is None:
        raise ValueError("План синхронизации не найден.")

    if plan["status"] in {PLAN_STATUS_RUNNING, PLAN_STATUS_STOPPING}:
        raise ValueError("План уже выполняется.")

    active_ids = get_active_registry_global_ids(conn)
    if not active_ids:
        raise ValueError("В реестре нет активных площадок для синхронизации.")

    now = _utc_now()

    run_cursor = conn.execute(
        """
        INSERT INTO investmap_rf_sync_runs (
            plan_id,
            status,
            started_at_utc,
            requested_cards_count,
            global_ids_json,
            started_by_user_id
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            plan_id,
            RUN_STATUS_RUNNING,
            now,
            len(active_ids),
            json.dumps(active_ids),
            started_by_user_id,
        ),
    )

    conn.execute(
        """
        UPDATE investmap_rf_sync_plans
        SET
            is_enabled = 1,
            status = ?,
            next_run_at_utc = ?,
            cursor_global_id = NULL,
            current_cycle_started_at_utc = ?,
            last_run_started_at_utc = ?,
            last_run_finished_at_utc = NULL,
            last_error = NULL,
            stop_requested = 0,
            updated_at_utc = ?,
            updated_by_user_id = ?
        WHERE id = ?
        """,
        (
            PLAN_STATUS_RUNNING,
            now,
            now,
            now,
            now,
            started_by_user_id,
            plan_id,
        ),
    )

    return {
        "plan": get_sync_plan(conn, plan_id),
        "run": get_latest_sync_run(conn, plan_id),
        "active_cards_count": len(active_ids),
        "run_id": run_cursor.lastrowid,
    }


def pause_sync_plan(
    conn,
    *,
    plan_id: int,
    updated_by_user_id: int | None = None,
) -> dict[str, Any]:
    """Приостанавливает план перед следующим пакетом."""
    plan = get_sync_plan(conn, plan_id)
    if plan is None:
        raise ValueError("План синхронизации не найден.")

    if plan["status"] not in {PLAN_STATUS_RUNNING, PLAN_STATUS_STOPPING}:
        raise ValueError("Приостановить можно только выполняемый план.")

    conn.execute(
        """
        UPDATE investmap_rf_sync_plans
        SET
            is_enabled = 0,
            status = ?,
            stop_requested = 0,
            next_run_at_utc = NULL,
            updated_at_utc = ?,
            updated_by_user_id = ?
        WHERE id = ?
        """,
        (
            PLAN_STATUS_PAUSED,
            _utc_now(),
            updated_by_user_id,
            plan_id,
        ),
    )

    latest_run = get_latest_sync_run(conn, plan_id)
    if latest_run and latest_run["status"] == RUN_STATUS_RUNNING:
        conn.execute(
            """
            UPDATE investmap_rf_sync_runs
            SET status = ?
            WHERE id = ?
            """,
            (RUN_STATUS_PAUSED, latest_run["id"]),
        )

    return get_sync_plan(conn, plan_id)

def resume_sync_plan(
    conn,
    *,
    plan_id: int,
    updated_by_user_id: int | None = None,
) -> dict[str, Any]:
    """
    Возобновляет приостановленный цикл без сброса cursor_global_id.

    Не создаёт новый run: следующий пакет продолжит текущий цикл
    с площадки после последнего успешно сохранённого курсора.
    """
    plan = get_sync_plan(conn, plan_id)
    if plan is None:
        raise ValueError("План синхронизации не найден.")

    if plan["status"] != PLAN_STATUS_PAUSED:
        raise ValueError("Возобновить можно только приостановленный план.")

    latest_run = get_latest_sync_run(conn, plan_id)
    if latest_run is None:
        raise ValueError("Для плана отсутствует цикл синхронизации.")

    if latest_run["status"] not in {RUN_STATUS_PAUSED, RUN_STATUS_RUNNING}:
        raise ValueError("Последний цикл плана нельзя возобновить.")

    active_ids = get_active_registry_global_ids(conn)
    if not active_ids:
        raise ValueError("В реестре нет активных площадок для синхронизации.")

    now = _utc_now()

    conn.execute(
        """
        UPDATE investmap_rf_sync_plans
        SET
            is_enabled = 1,
            status = ?,
            next_run_at_utc = ?,
            stop_requested = 0,
            last_error = NULL,
            updated_at_utc = ?,
            updated_by_user_id = ?
        WHERE id = ?
        """,
        (
            PLAN_STATUS_RUNNING,
            now,
            now,
            updated_by_user_id,
            plan_id,
        ),
    )

    if latest_run["status"] == RUN_STATUS_PAUSED:
        conn.execute(
            """
            UPDATE investmap_rf_sync_runs
            SET
                status = ?,
                finished_at_utc = NULL,
                error_message = NULL
            WHERE id = ?
            """,
            (RUN_STATUS_RUNNING, latest_run["id"]),
        )

    return {
        "plan": get_sync_plan(conn, plan_id),
        "run": get_latest_sync_run(conn, plan_id),
        "active_cards_count": len(active_ids),
    }


def recover_interrupted_sync_batches(
    conn,
    *,
    updated_by_user_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    Переводит зависшие running-пакеты после рестарта в безопасную паузу.

    Никаких API-вызовов не выполняет. Возобновление требует явного
    действия администратора через resume_sync_plan().
    """
    rows = conn.execute(
        """
        SELECT id, plan_id
        FROM investmap_rf_sync_batches
        WHERE status = ?
        ORDER BY plan_id, id
        """,
        (BATCH_STATUS_RUNNING,),
    ).fetchall()

    if not rows:
        return []

    now = _utc_now()
    message = (
        "Пакет прерван перезапуском приложения; "
        "возобновите план для повторного выполнения пакета."
    )
    recovered_plan_ids: set[int] = set()

    for row in rows:
        batch_id = int(row["id"])
        plan_id = int(row["plan_id"])

        conn.execute(
            """
            UPDATE investmap_rf_sync_batches
            SET
                status = ?,
                finished_at_utc = ?,
                error_message = ?
            WHERE id = ? AND status = ?
            """,
            (
                BATCH_STATUS_FAILED,
                now,
                message,
                batch_id,
                BATCH_STATUS_RUNNING,
            ),
        )

        recovered_plan_ids.add(plan_id)

    recovered: list[dict[str, Any]] = []

    for plan_id in sorted(recovered_plan_ids):
        plan = get_sync_plan(conn, plan_id)
        if plan is None:
            continue

        if plan["status"] in {
            PLAN_STATUS_RUNNING,
            PLAN_STATUS_PAUSED,
            PLAN_STATUS_STOPPING,
        }:
            conn.execute(
                """
                UPDATE investmap_rf_sync_plans
                SET
                    is_enabled = 0,
                    status = ?,
                    stop_requested = 0,
                    next_run_at_utc = NULL,
                    last_error = ?,
                    updated_at_utc = ?,
                    updated_by_user_id = ?
                WHERE id = ?
                """,
                (
                    PLAN_STATUS_PAUSED,
                    message,
                    now,
                    updated_by_user_id,
                    plan_id,
                ),
            )

            latest_run = get_latest_sync_run(conn, plan_id)
            if latest_run and latest_run["status"] == RUN_STATUS_RUNNING:
                conn.execute(
                    """
                    UPDATE investmap_rf_sync_runs
                    SET
                        status = ?,
                        error_message = ?
                    WHERE id = ?
                    """,
                    (
                        RUN_STATUS_PAUSED,
                        message,
                        latest_run["id"],
                    ),
                )

        recovered.append(
            {
                "plan_id": plan_id,
                "plan": get_sync_plan(conn, plan_id),
                "latest_run": get_latest_sync_run(conn, plan_id),
            }
        )

    return recovered

def request_stop_sync_plan(
    conn,
    *,
    plan_id: int,
    updated_by_user_id: int | None = None,
) -> dict[str, Any]:
    """
    Запрашивает мягкую остановку.

    Исполнитель завершит текущую площадку/пакет и перед запуском следующего
    проверит stop_requested.
    """
    plan = get_sync_plan(conn, plan_id)
    if plan is None:
        raise ValueError("План синхронизации не найден.")

    if plan["status"] not in {PLAN_STATUS_RUNNING, PLAN_STATUS_PAUSED}:
        raise ValueError("Остановить можно только выполняемый или приостановленный план.")

    conn.execute(
        """
        UPDATE investmap_rf_sync_plans
        SET
            is_enabled = 0,
            status = ?,
            stop_requested = 1,
            next_run_at_utc = NULL,
            updated_at_utc = ?,
            updated_by_user_id = ?
        WHERE id = ?
        """,
        (
            PLAN_STATUS_STOPPING,
            _utc_now(),
            updated_by_user_id,
            plan_id,
        ),
    )

    return get_sync_plan(conn, plan_id)


def finalize_stop_sync_plan(
    conn,
    *,
    plan_id: int,
    updated_by_user_id: int | None = None,
) -> dict[str, Any]:
    """
    Фиксирует остановку после завершения текущего пакета.

    Эту функцию вызывает исполнитель, когда видит stop_requested = 1.
    """
    plan = get_sync_plan(conn, plan_id)
    if plan is None:
        raise ValueError("План синхронизации не найден.")

    now = _utc_now()

    conn.execute(
        """
        UPDATE investmap_rf_sync_plans
        SET
            is_enabled = 0,
            status = ?,
            next_run_at_utc = NULL,
            last_run_finished_at_utc = ?,
            stop_requested = 0,
            updated_at_utc = ?,
            updated_by_user_id = ?
        WHERE id = ?
        """,
        (
            PLAN_STATUS_IDLE,
            now,
            now,
            updated_by_user_id,
            plan_id,
        ),
    )

    latest_run = get_latest_sync_run(conn, plan_id)
    if latest_run and latest_run["status"] in {
        RUN_STATUS_RUNNING,
        RUN_STATUS_PAUSED,
    }:
        conn.execute(
            """
            UPDATE investmap_rf_sync_runs
            SET
                status = ?,
                finished_at_utc = ?
            WHERE id = ?
            """,
            (RUN_STATUS_STOPPED, now, latest_run["id"]),
        )

    return get_sync_plan(conn, plan_id)


def prepare_next_sync_batch(
    conn,
    *,
    plan_id: int,
) -> dict[str, Any] | None:
    """
    Подготавливает следующий пакет без обращения к API.

    Возвращает None, если план приостановлен/остановлен/завершён
    либо если время следующего запуска ещё не наступило.
    """
    plan = get_sync_plan(conn, plan_id)
    if plan is None:
        raise ValueError("План синхронизации не найден.")

    if plan["stop_requested"]:
        return None

    if not plan["is_enabled"] or plan["status"] != PLAN_STATUS_RUNNING:
        return None

    latest_run = get_latest_sync_run(conn, plan_id)

    if latest_run is None or latest_run["status"] != RUN_STATUS_RUNNING:
        raise ValueError("Для плана отсутствует активный цикл синхронизации.")

    active_ids = _get_run_global_ids(conn, int(latest_run["id"]))

    cursor_global_id = plan["cursor_global_id"]
    if cursor_global_id is None:
        remaining_ids = active_ids
    else:
        remaining_ids = [
            global_id
            for global_id in active_ids
            if global_id > int(cursor_global_id)
        ]

    if not remaining_ids:
        return None

    batch_size = _validate_batch_size(int(plan["batch_size"]))
    batch_ids = remaining_ids[:batch_size]

    batch_number_row = conn.execute(
        """
        SELECT COALESCE(MAX(batch_number), 0) AS last_batch_number
        FROM investmap_rf_sync_batches
        WHERE run_id = ?
        """,
        (latest_run["id"],),
    ).fetchone()

    batch_number = int(batch_number_row["last_batch_number"]) + 1
    now = _utc_now()

    batch_cursor = conn.execute(
        """
        INSERT INTO investmap_rf_sync_batches (
            plan_id,
            run_id,
            batch_number,
            status,
            global_ids_json,
            started_at_utc
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            plan_id,
            latest_run["id"],
            batch_number,
            BATCH_STATUS_RUNNING,
            json.dumps(batch_ids),
            now,
        ),
    )

    return {
        "plan": get_sync_plan(conn, plan_id),
        "run": latest_run,
        "batch_id": batch_cursor.lastrowid,
        "batch_number": batch_number,
        "global_ids": batch_ids,
    }


def complete_sync_batch(
    conn,
    *,
    plan_id: int,
    batch_id: int,
    processed_cards_count: int,
    successful_cards_count: int,
    failed_cards_count: int,
    changed_cards_count: int,
) -> dict[str, Any]:
    """
    Сохраняет итоги выполненного пакета и назначает следующий запуск.

    Фактический запуск API выполняется другим сервисом. Эта функция принимает
    только итоговые числовые показатели уже завершённого пакета.
    """
    plan = get_sync_plan(conn, plan_id)
    if plan is None:
        raise ValueError("План синхронизации не найден.")

    batch_row = conn.execute(
        """
        SELECT *
        FROM investmap_rf_sync_batches
        WHERE id = ? AND plan_id = ?
        """,
        (batch_id, plan_id),
    ).fetchone()

    if batch_row is None:
        raise ValueError("Пакет синхронизации не найден.")

    if batch_row["status"] != BATCH_STATUS_RUNNING:
        raise ValueError("Завершить можно только выполняемый пакет.")

    metrics = {
        "processed_cards_count": processed_cards_count,
        "successful_cards_count": successful_cards_count,
        "failed_cards_count": failed_cards_count,
        "changed_cards_count": changed_cards_count,
    }

    for field_name, value in metrics.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field_name} должен быть неотрицательным целым числом.")

    if successful_cards_count + failed_cards_count > processed_cards_count:
        raise ValueError(
            "Сумма успешных и ошибочных карточек не может превышать обработанные."
        )

    global_ids = json.loads(batch_row["global_ids_json"])
    if not isinstance(global_ids, list) or not global_ids:
        raise ValueError("Пакет содержит некорректный список площадок.")

    cursor_global_id = max(int(value) for value in global_ids)
    now = _utc_now()
    next_run_at_utc = _utc_after_minutes(int(plan["interval_minutes"]))

    conn.execute(
        """
        UPDATE investmap_rf_sync_batches
        SET
            status = ?,
            finished_at_utc = ?,
            processed_cards_count = ?,
            successful_cards_count = ?,
            failed_cards_count = ?,
            changed_cards_count = ?
        WHERE id = ?
        """,
        (
            BATCH_STATUS_COMPLETED,
            now,
            processed_cards_count,
            successful_cards_count,
            failed_cards_count,
            changed_cards_count,
            batch_id,
        ),
    )

    latest_run = get_latest_sync_run(conn, plan_id)
    if latest_run is None:
        raise ValueError("Активный цикл синхронизации не найден.")

    conn.execute(
        """
        UPDATE investmap_rf_sync_runs
        SET
            processed_cards_count = processed_cards_count + ?,
            successful_cards_count = successful_cards_count + ?,
            failed_cards_count = failed_cards_count + ?,
            changed_cards_count = changed_cards_count + ?
        WHERE id = ?
        """,
        (
            processed_cards_count,
            successful_cards_count,
            failed_cards_count,
            changed_cards_count,
            latest_run["id"],
        ),
    )

    if plan["stop_requested"]:
        return finalize_stop_sync_plan(conn, plan_id=plan_id)

    active_ids = _get_run_global_ids(conn, int(latest_run["id"]))
    is_cycle_complete = not any(
        int(global_id) > cursor_global_id for global_id in active_ids
    )

    if is_cycle_complete:
        conn.execute(
            """
            UPDATE investmap_rf_sync_plans
            SET
                is_enabled = 0,
                status = ?,
                next_run_at_utc = NULL,
                cursor_global_id = ?,
                last_run_finished_at_utc = ?,
                updated_at_utc = ?
            WHERE id = ?
            """,
            (
                PLAN_STATUS_COMPLETED,
                cursor_global_id,
                now,
                now,
                plan_id,
            ),
        )

        conn.execute(
            """
            UPDATE investmap_rf_sync_runs
            SET
                status = ?,
                finished_at_utc = ?
            WHERE id = ?
            """,
            (RUN_STATUS_COMPLETED, now, latest_run["id"]),
        )
    else:
        conn.execute(
            """
            UPDATE investmap_rf_sync_plans
            SET
                status = ?,
                next_run_at_utc = ?,
                cursor_global_id = ?,
                updated_at_utc = ?
            WHERE id = ?
            """,
            (
                PLAN_STATUS_RUNNING,
                next_run_at_utc,
                cursor_global_id,
                now,
                plan_id,
            ),
        )

    return get_sync_plan_status(conn, plan_id)


def fail_sync_batch(
    conn,
    *,
    plan_id: int,
    batch_id: int,
    error_message: str,
) -> dict[str, Any]:
    """Фиксирует фатальную ошибку пакета и переводит план в состояние failed."""
    plan = get_sync_plan(conn, plan_id)
    if plan is None:
        raise ValueError("План синхронизации не найден.")

    message = str(error_message or "").strip()
    if not message:
        message = "Неизвестная ошибка выполнения пакета."

    now = _utc_now()

    conn.execute(
        """
        UPDATE investmap_rf_sync_batches
        SET
            status = ?,
            finished_at_utc = ?,
            error_message = ?
        WHERE id = ? AND plan_id = ?
        """,
        (
            BATCH_STATUS_FAILED,
            now,
            message,
            batch_id,
            plan_id,
        ),
    )

    conn.execute(
        """
        UPDATE investmap_rf_sync_plans
        SET
            is_enabled = 0,
            status = ?,
            next_run_at_utc = NULL,
            last_run_finished_at_utc = ?,
            last_error = ?,
            updated_at_utc = ?
        WHERE id = ?
        """,
        (
            PLAN_STATUS_FAILED,
            now,
            message,
            now,
            plan_id,
        ),
    )

    latest_run = get_latest_sync_run(conn, plan_id)
    if latest_run and latest_run["status"] == RUN_STATUS_RUNNING:
        conn.execute(
            """
            UPDATE investmap_rf_sync_runs
            SET
                status = ?,
                finished_at_utc = ?,
                error_message = ?
            WHERE id = ?
            """,
            (RUN_STATUS_FAILED, now, message, latest_run["id"]),
        )

    return get_sync_plan_status(conn, plan_id)
