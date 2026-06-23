import os
import subprocess
import threading
from datetime import datetime

from core.activity_log import log_action
from db import get_db

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
BACKUP_BAT    = os.path.join(BASE_DIR, "backup.bat")
INTERVAL_SEC  = 3 * 60 * 60  # 3 часа

_timer: threading.Timer | None = None


def _log(action: str, detail: str) -> None:
    """Системный лог без пользователя — открывает conn самостоятельно."""
    try:
        db = get_db()
        log_action(db, user_id=None, action=action, detail=detail)
        db.commit()
        db.close()
    except Exception:
        pass


def _notify_admins(message: str, link: str = "/notifications") -> None:
    """Пишет уведомление в таблицу notifications для всех пользователей с role='admin'."""
    try:
        db = get_db()
        admins = db.execute(
            "SELECT id FROM users WHERE role = 'admin'"
        ).fetchall()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for admin in admins:
            db.execute(
                "INSERT INTO notifications (user_id, message, link, is_read, created_at) "
                "VALUES (?, ?, ?, 0, ?)",
                (admin["id"], message, link, now),
            )
        db.commit()
        db.close()
    except Exception as e:
        _log("backup_error", f"Ошибка записи уведомления: {e}")


def _run_backup() -> None:
    """Запускает backup.bat через subprocess и логирует результат."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
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
            msg = f"✅ Бэкап выполнен успешно — {ts}"
            _log("backup_success", msg)
            _notify_admins(msg)
        else:
            stderr_clean = (result.stderr or "").strip()[:500]
            stdout_clean = (result.stdout or "").strip()[-300:]
            reason = stderr_clean or stdout_clean or f"code {result.returncode}"
            msg = f"❌ Ошибка бэкапа — {ts}. Причина: {reason}"
            _log("backup_error", msg)
            _notify_admins(msg)
    except subprocess.TimeoutExpired:
        msg = f"❌ Бэкап превысил временной лимит (300 сек) — {ts}"
        _log("backup_error", msg)
        _notify_admins(msg)
    except Exception as e:
        msg = f"❌ Непредвиденная ошибка бэкапа — {ts}: {e}"
        _log("backup_error", msg)
        _notify_admins(msg)
    finally:
        _schedule_next()


def _schedule_next() -> None:
    global _timer
    _timer = threading.Timer(INTERVAL_SEC, _run_backup)
    _timer.daemon = True
    _timer.start()


def start() -> None:
    """Запустить планировщик. Вызывать один раз из app.py при старте приложения."""
    if not os.path.exists(BACKUP_BAT):
        msg = "❌ backup.bat не найден — планировщик бэкапа не запущен"
        _log("backup_error", msg)
        _notify_admins(msg)
        return
    _schedule_next()
    _log("backup_scheduler_started", "Планировщик запущен, интервал: каждые 3 часа")


def stop() -> None:
    """Остановить планировщик (необязательно)."""
    global _timer
    if _timer:
        _timer.cancel()
        _timer = None
