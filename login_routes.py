# ╔══════════════════════════════════════════════════════════════╗
# ║                       login_routes.py                        ║
# ║  v2.2: session.permanent=True (сессия 15 мин бездействия)   ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import (
    Blueprint, render_template, request,
    redirect, url_for, session, flash
)
from datetime import datetime

from db import get_db
from auth_utils import hash_pw, check_pw, is_legacy_hash, load_permissions_to_session

auth_bp = Blueprint('auth', __name__)


def _log_login(conn, user_id, username, event, ip):
    """Записывает событие входа/выхода в таблицу login_log."""
    conn.execute(
        "INSERT INTO login_log (user_id, username, event, ip, created_at) "
        "VALUES (?,?,?,?,?)",
        (user_id, username, event, ip,
         datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    conn.commit()


# ─── ВХОД ───────────────────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u  = request.form.get('username', '').strip()
        p  = request.form.get('password', '')
        ip = request.remote_addr or '—'

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username=?",
            (u,)
        ).fetchone()

        if user and check_pw(user['password'], p):
            must_change = bool(user['must_change_password'])

            # ─── Политика безопасности: если хеш устаревший (SHA-256) ───
            if is_legacy_hash(user['password']):
                must_change = True
                conn.execute(
                    "UPDATE users SET must_change_password=1 WHERE id=?",
                    (user['id'],)
                )
                conn.commit()

            # ─── Сессия: permanent=True + таймаут бездействия 15 мин ───
            # PERMANENT_SESSION_LIFETIME задан в app.py, SESSION_REFRESH_EACH_REQUEST=True
            # — при каждом запросе таймер сбрасывается.
            session.permanent = True

            session['user_id']              = user['id']
            session['username']             = user['username']
            session['full_name']            = user['full_name']
            session['role']                 = user['role']
            session['must_change_password'] = must_change

            load_permissions_to_session(user)

            _log_login(conn, user['id'], user['username'], 'login', ip)
            conn.close()

            if must_change:
                if is_legacy_hash(user['password']):
                    flash('В связи с обновлением политики безопасности необходимо сменить пароль', 'warning')
                else:
                    flash('Необходимо сменить временный пароль перед продолжением', 'warning')
                return redirect(url_for('auth.change_password'))

            return redirect(url_for('requests.index'))

        # Неудачная попытка — логируем без user_id
        conn.execute(
            "INSERT INTO login_log (user_id, username, event, ip, created_at) "
            "VALUES (?,?,?,?,?)",
            (None, u, 'failed', ip,
             datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        )
        conn.commit()
        conn.close()

        flash('Неверный логин или пароль', 'error')

    return render_template('login.html')


# ─── СМЕНА ПАРОЛЯ ───────────────────────────────────────────────────────────

@auth_bp.route('/change-password', methods=['GET', 'POST'])
def change_password():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        new_pw  = request.form.get('new_password', '')
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
            (hash_pw(new_pw), session['user_id'])
        )
        conn.commit()
        conn.close()

        session['must_change_password'] = False
        flash('Пароль успешно изменён', 'success')
        return redirect(url_for('requests.index'))

    return render_template('change_password.html')


# ─── ВЫХОД ──────────────────────────────────────────────────────────────────

@auth_bp.route('/logout')
def logout():
    ip = request.remote_addr or '—'
    user_id  = session.get('user_id')
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
