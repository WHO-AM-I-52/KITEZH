import os
import shutil
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

from core.activity_log import log_action
from db import DB_PATH, get_db

BASE_DIR = Path(__file__).resolve().parent.parent
BACKUPS_DIR = BASE_DIR / "backups"
INTERVAL_SEC = 3 * 60 * 60
KEEP_DAYS = 5

_timer: threading.Timer | None = None
_backup_lock = threading.Lock()


def _log(action: str, detail: str) -> None:
    db = None
    try:
        db = get_db()
        log_action(db, user_id=None, action=action, detail=detail)
        db.commit()
    except Exception:
        pass
    finally:
        if db is not None:
            db.close()


def _notify_admins(message: str, link: str = "/notifications") -> None:
    db = None
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
    except Exception as exc:
        _log("backup_error", f"Ошибка записи уведомления: {exc}")
    finally:
        if db is not None:
            db.close()


def _copy_database(destination: Path) -> None:
    source_path = Path(DB_PATH)
    if not source_path.is_file():
        raise FileNotFoundError(f"Рабочая БД не найдена: {source_path}")

    destination.parent.mkdir(parents=True, exist_ok=True)

    source = sqlite3.connect(str(source_path), timeout=15)
    try:
        target = sqlite3.connect(str(destination), timeout=15)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()

    check = sqlite3.connect(str(destination), timeout=15)
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()
        if result is None or result[0] != "ok":
            raise RuntimeError(f"Проверка копии БД не пройдена: {result}")
    finally:
        check.close()


def _prune_old_days() -> None:
    days = sorted(
        (
            path for path in BACKUPS_DIR.iterdir()
            if path.is_dir()
            and len(path.name) == 10
            and path.name[4] == "-"
            and path.name[7] == "-"
            and path.name[:4].isdigit()
            and path.name[5:7].isdigit()
            and path.name[8:10].isdigit()
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    for path in days[KEEP_DAYS:]:
        shutil.rmtree(path)


def _create_backup() -> Path:
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now().strftime("%Y-%m-%d")
    destination = BACKUPS_DIR / day
    staging = BACKUPS_DIR / f".staging-{uuid.uuid4().hex}"
    previous = BACKUPS_DIR / f".previous-{day}-{uuid.uuid4().hex}"

    if destination.exists() and not destination.is_dir():
        raise RuntimeError(f"Путь дневной копии не является папкой: {destination}")

    try:
        staging.mkdir()
        _copy_database(staging / "db" / "database.db")

        for name in ("uploads", "reports"):
            source = BASE_DIR / name
            if not source.is_dir():
                raise FileNotFoundError(f"Исходная папка не найдена: {source}")
            shutil.copytree(source, staging / name)

        if destination.exists():
            destination.rename(previous)

        try:
            staging.rename(destination)
        except Exception:
            if previous.exists():
                previous.rename(destination)
            raise

        # Новая дневная копия уже опубликована. Прежнюю удаляем после этого.
        if previous.exists():
            shutil.rmtree(previous)

        _prune_old_days()
        return destination
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _run_backup(schedule_next: bool = True) -> bool:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    if not _backup_lock.acquire(blocking=False):
        msg = f"❌ Бэкап не запущен: предыдущий запуск ещё выполняется — {ts}"
        _log("backup_error", msg)
        _notify_admins(msg)
        if schedule_next:
            _schedule_next()
        return False

    try:
        destination = _create_backup()
        msg = f"✅ Бэкап выполнен успешно — {ts}; папка: {destination}"
        _log("backup_success", msg)
        _notify_admins(msg)
        return True
    except Exception as exc:
        msg = f"❌ Ошибка бэкапа — {ts}. Причина: {exc}"
        _log("backup_error", msg)
        _notify_admins(msg)
        return False
    finally:
        _backup_lock.release()
        if schedule_next:
            _schedule_next()


def _schedule_next() -> None:
    global _timer
    _timer = threading.Timer(INTERVAL_SEC, _run_backup)
    _timer.daemon = True
    _timer.start()


def start() -> None:
    """Запустить планировщик. Вызывать один раз из app.py."""
    global _timer
    if _timer is not None and _timer.is_alive():
        return
    _schedule_next()
    _log("backup_scheduler_started", "Планировщик запущен, интервал: каждые 3 часа")


def stop() -> None:
    """Остановить ожидание следующего запуска."""
    global _timer
    if _timer:
        _timer.cancel()
        _timer = None


if __name__ == "__main__":
    raise SystemExit(0 if _run_backup(schedule_next=False) else 1)
