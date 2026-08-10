"""Журнал действий KITEZH.

Логирование не должно ломать пользовательские запросы при временной
блокировке SQLite; при отказе журналирования запрос продолжается.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

import db as _db


def log_action(conn: Any | None, user_id: int | None, username: str | None, event: str, ip: str | None = None, detail: str | None = None) -> None:
    """Пишет событие в activity_log, но не бросает исключение при блокировке БД.

    При наличии блокировки (database is locked) делает несколько коротких
    повторных попыток и в случае отказа молча возвращается.
    """

    if conn is None:
        try:
            conn = _db.get_db()
        except Exception:
            return

    payload = {
        'user_id': user_id,
        'username': username or '',
        'event': event,
        'ip': ip or '',
        'detail': detail or '',
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }

    sql = (
        "INSERT INTO activity_log (user_id, username, event, ip, detail, created_at) "
        "VALUES (:user_id, :username, :event, :ip, :detail, :created_at)"
    )

    attempts = 0
    while attempts < 3:
        try:
            conn.execute(sql, payload)
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            # database is locked → пробуем ещё раз, затем тихо отказываемся
            if 'database is locked' not in str(exc).lower():
                return
            attempts += 1
        except Exception:
            return


def log_login(conn: Any | None, user_id: int | None, username: str, event: str, ip: str | None = None) -> None:
    """Отдельный журнал логинов, с тем же принципом: не ломать запрос."""

    if conn is None:
        try:
            conn = _db.get_db()
        except Exception:
            return

    payload = {
        'user_id': user_id,
        'username': username or '',
        'event': event,
        'ip': ip or '',
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }

    sql = (
        "INSERT INTO login_log (user_id, username, event, ip, created_at) "
        "VALUES (:user_id, :username, :event, :ip, :created_at)"
    )

    attempts = 0
    while attempts < 3:
        try:
            conn.execute(sql, payload)
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            if 'database is locked' not in str(exc).lower():
                return
            attempts += 1
        except Exception:
            return
