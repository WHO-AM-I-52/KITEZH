from __future__ import annotations

import threading

from db import get_db
from services.investmap_rf_sync_scheduler import run_due_sync_plans

INTERVAL_SEC = 60

_lock = threading.Lock()
_timer: threading.Timer | None = None
_started = False


def _run_once() -> None:
    try:
        if not _lock.acquire(blocking=False):
            return

        try:
            run_due_sync_plans(get_db)
        finally:
            _lock.release()
    except Exception:
        # Ошибка одного запуска не должна останавливать фоновый планировщик.
        pass
    finally:
        _schedule_next()


def _schedule_next() -> None:
    global _timer

    if not _started:
        return

    _timer = threading.Timer(INTERVAL_SEC, _run_once)
    _timer.daemon = True
    _timer.start()


def start() -> None:
    """Запускает минутную проверку готовых планов один раз на процесс."""
    global _started

    if _started:
        return

    _started = True
    _schedule_next()


def stop() -> None:
    """Останавливает планировщик; полезно при штатном завершении процесса."""
    global _started, _timer

    _started = False

    if _timer is not None:
        _timer.cancel()
        _timer = None
