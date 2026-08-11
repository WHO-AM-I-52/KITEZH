# ╔══════════════════════════════════════════════════════════════
# ║                       login_routes.py                        ║
# ║  v2.4: fix #61 — rate limiting 10 попыток/мин с IP           ║
# ╚══════════════════════════════════════════════════════════════╗

import logging
import sqlite3
import time
from datetime import datetime

from flask import (
    Blueprint, render_template, request,
    redirect, url_for, session, flash
)

from db import get_db
from core.auth_utils import hash_pw, check_pw, is_legacy_hash, load_permissions_to_session
from core.limiter import limiter

auth_bp = Blueprint('auth', __name__)
logger = logging.getLogger(__name__)

_LOGIN_LOG_RETRY_DELAYS = (0.1, 0.2)
_TRANSIENT_LOCK_MESSAGES = ('database is locked', 'database is busy')


def _is_transient_lock_error(exc):
    return any(message in str(exc).lower() for message in _TRANSIENT_LOCK_MESSAGES)


def _log_login(conn, user_id, username, event, ip):
    """Записывает событие входа/выхода, не блокируя вход при временном lock."""
    params = (
        user_id, username, event, ip,
        datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    )

    for attempt, delay in enumerate((*_LOGIN_LOG_RETRY_DELAYS, None), start=1):
        try:
            conn.execute(
                "INSERT INTO login_log (user_id, username, event, ip, created_at) "
                "VALUES (?,?,?,?,?)",
                params,
            )
            conn.commit()
            return True
        except sqlite3.OperationalError as exc:
            if not _is_transient_lock_error(exc):
                raise

            conn.rollback()
            if delay is None:
                logger.warning(
                    "login_log unavailable after %s attempts due to SQLite lock; "
                    "continuing without audit record",
                    attempt,
                    exc_info=True,
                )
                return False
            time.sleep(delay)


# ─── ВХОД ────────────────────────────────────────────────────────────────────────────────
# fix #61: не более 10 POST-запросов в минуту с одного IP
@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if request.method == 'POST':
        u = request.form.get('username', '').strip()
        p = request.form.get('password', '')
        ip = request.remote_addr or '—'

        conn = get_db()
        try:
            user = conn.execute(
                "SELECT * FROM users WHERE username=?",
                (u,),
            ).fetchone()

            if user and check_pw(user['password'], p):
                must_change = bool(user['must_change_password'])

                # ─── Политика безопасности: если хеш устаревший (SHA-256) ───
                if is_legacy_hash(user['password']):
                    must_change = True
                    conn.execute(
                        "UPDATE users SET must_change_password=1 WHERE id=?",
                        (user['id'],),
                    )
                    conn.commit()

                # ─── Сессия: permanent=True, срок жизни 8 ч (PERMANENT_SESSION_LIFETIME в app.py) ───
                session.permanent = True

                session['user_id'] = user['id']
                session['username'] = user['username']
                session['full_name'] = user['full_name']
                session['role'] = user['role']
                session['must_change_password'] = must_change

                load_permissions_to_session(user)
                _log_login(conn, user['id'], user['username'], 'login', ip)

                if must_change:
                    if is_legacy_hash(user['password']):
                        flash('В связи с обновлением политики безопасности необходимо сменить пароль', 'warning')
                    else:
                        flash('Необходимо сменить временный пароль перед продолжением', 'warning')
                    return redirect(url_for('auth.change_password'))

                return redirect(url_for('requests.requests_list'))

            # Неудачная попытка — логируем без user_id.
            _log_login(conn, None, u, 'failed', ip)
            flash('Неверный логин или пароль', 'error')
        finally:
            conn.close()

    return render_template('login.html')


# ─── СМЕНА ПАРОЛЯ ────────────────────────────────────────────────────────────────────────────────────

@auth_bp.route('/change-password', methods=['GET', 'POST'])
def change_password():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        new_pw = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')

        if len(new_pw) < 6:
            flash('Пароль должен быть не менее 6 символов', 'error')
            return render_template('change_password.html')
        if new_pw != confirm:
            flash('Пароли не совпадают', 'error')
            return render_template('change_password.html')

        conn = get_db()
        conn.execute(
            "UPDATE users SET password=?, must_change_password=0 WHERE id=?",
            (hash_pw(new_pw), session['user_id']),
        )
        conn.commit()
        conn.close()

        session['must_change_password'] = False
        flash('Пароль успешно изменён', 'success')
        return redirect(url_for('requests.requests_list'))

    return render_template('change_password.html')


# ─── ВЫХОД ────────────────────────────────────────────────────────────────────────────────

@auth_bp.route('/logout')
def logout():
    ip = request.remote_addr or '—'
    user_id = session.get('user_id')
    username = session.get('username', '—')
    if user_id:
        try:
            conn = get_db()
            _log_login(conn, user_id, username, 'logout', ip)
            conn.close()
        except Exception:
            pass
    session.clear()
    return redirect(url_for('auth.login'))
