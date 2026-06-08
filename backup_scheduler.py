import os
import subprocess
import threading
from datetime import datetime

from activity_log import log_action

BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
BACKUP_BAT     = os.path.join(BASE_DIR, "backup.bat")
INTERVAL_SEC   = 3 * 60 * 60  # 3 часа

_timer: threading.Timer | None = None


def _run_backup():
    """3апускает backup.bat через subprocess и логирует результат."""
    try:
        result = subprocess.run(
            ["cmd", "/c", BACKUP_BAT],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if result.returncode == 0:
            log_action(
                user_id=None,
                username="system",
                action="backup_success",
                details=f"Автобэкап выполнен успешно {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            )
        else:
            log_action(
                user_id=None,
                username="system",
                action="backup_error",
                details=f"Ошибка бэкапа (code {result.returncode}): {result.stderr[:300]}",
            )
    except Exception as e:
        log_action(
            user_id=None,
            username="system",
            action="backup_error",
            details=f"Исключение при бэкапе: {e}",
        )
    finally:
        _schedule_next()


def _schedule_next():
    global _timer
    _timer = threading.Timer(INTERVAL_SEC, _run_backup)
    _timer.daemon = True
    _timer.start()


def start():
    """Запустить планировщик. Вызывать один раз из app.py при старте приложения."""
    if not os.path.exists(BACKUP_BAT):
        log_action(
            user_id=None,
            username="system",
            action="backup_error",
            details="backup.bat не найден — планировщик не запущен",
        )
        return
    _schedule_next()
    log_action(
        user_id=None,
        username="system",
        action="backup_scheduler_started",
        details=f"Планировщик запущен, интервал: каждые 3 часа",
    )


def stop():
    """0становить планировщик (необязательно)."""
    global _timer
    if _timer:
        _timer.cancel()
        _timer = None
