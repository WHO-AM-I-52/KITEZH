from __future__ import annotations

import threading

from db import get_db
from services.investmap_rf_daily_sync import run_weekday_daily_sync
from services.investmap_rf_sync_plans import recover_interrupted_sync_batches
from services.investmap_rf_sync_scheduler import run_due_sync_plans

INTERVAL_SEC = 60

_lock = threading.Lock()
_state_lock = threading.Lock()
_timer: threading.Timer | None = None
_started = False


def _recover_interrupted_batches() -> None:
    """Переводит пакеты, зависшие после перезапуска, в безопасную паузу."""
    conn = None

    try:
        conn = get_db()
        recovered = recover_interrupted_sync_batches(conn)

        if recovered:
            conn.commit()
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _run_once() -> None:
    try:
        if not _lock.acquire(blocking=False):
            return

        try:
            run_weekday_daily_sync(get_db)
            run_due_sync_plans(get_db)
        finally:
            _lock.release()
    except Exception:
        # Ошибка одной проверки не должна останавливать планировщик.
        pass
    finally:
        _schedule_next()


def _schedule_next() -> None:
    global _timer

    with _state_lock:
        if not _started:
            return

        _timer = threading.Timer(INTERVAL_SEC, _run_once)
        _timer.daemon = True
        _timer.start()


def start() -> None:
    """Запускает минутную проверку готовых планов один раз на процесс."""
    global _started

    with _state_lock:
        if _started:
            return
        _started = True

    _recover_interrupted_batches()
    _schedule_next()


def stop() -> None:
    """Останавливает планировщик при штатном завершении процесса."""
    global _started, _timer

    with _state_lock:
        _started = False

        if _timer is not None:
            _timer.cancel()
            _timer = None
