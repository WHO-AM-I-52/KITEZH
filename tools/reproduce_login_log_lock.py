"""Reproduce a temporary SQLite write lock during login_log writing.

Run from the repository root:
    python tools/reproduce_login_log_lock.py
"""

import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routes.login_routes import _log_login


def _hold_write_lock(db_path, ready):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        ready.set()
        time.sleep(0.05)
        conn.commit()
    finally:
        conn.close()


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = str(Path(temp_dir) / 'login_lock.sqlite3')
        conn = sqlite3.connect(db_path)
        conn.execute(
            'CREATE TABLE login_log ('
            'id INTEGER PRIMARY KEY, user_id INTEGER, username TEXT, '
            'event TEXT, ip TEXT, created_at TEXT)'
        )
        conn.commit()
        conn.close()

        ready = threading.Event()
        locker = threading.Thread(target=_hold_write_lock, args=(db_path, ready))
        locker.start()
        assert ready.wait(timeout=1), 'write lock was not acquired'

        log_conn = sqlite3.connect(db_path, timeout=0.01)
        try:
            assert _log_login(log_conn, 1, 'tester', 'login', '127.0.0.1')
            count = log_conn.execute('SELECT COUNT(*) FROM login_log').fetchone()[0]
            assert count == 1, f'expected one login_log row, got {count}'
        finally:
            log_conn.close()
            locker.join(timeout=1)

    print('PASS: temporary lock was retried and login_log was written')


if __name__ == '__main__':
    main()
