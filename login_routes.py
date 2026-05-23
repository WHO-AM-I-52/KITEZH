# ╔══════════════════════════════════════════════════════════════╗
# ║                       login_routes.py                        ║
# ║  v2.0: аутентификация + журнал входов + гибкие права        ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import (
    Blueprint, render_template, request,
    redirect, url_for, session, flash, abort
)
from datetime import datetime

from db import get_db
from auth_utils import hash_pw, load_permissions_to_session

auth_bp = Blueprint('auth', __name__)


def _log_login(conn, user_id, username, event, ip):
    """Записывает событие входа/выхода в таблицу login_log."""
    conn.execute(
        "INSERT INTO login_log (user_id, username, event, ip, created_at) "
        "VALUES (?,?,?,?,?)",
        (user_id, username, event,  ip,
         datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    )
    conn.commit()


# ─── ВХОД ────────────────────────────────────────────────────────────────────

@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u  = request.form.get('username', '').strip()
        p  = request.form.get('password', '')
        ip = request.remote_addr or '—'

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username=? AND password=?",
            (u, hash_pw(p))
        ).fetchone()

        if user:
            # Проверка: требуется ли смена пароля
            must_change = bool(user['must_change_password'])

            session['user_id']           = user['id']
            session['username']          = user['username']
            session['full_name']         = user['full_name']
            session['role']              = user['role']
            session['must_change_password'] = must_change

            # Загружаем все права в сессию
            load_permissions_to_session(user)

            _log_login(conn, user['id'], user['username'], 'login', ip)
            conn.close()

            if must_change:
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


# ─── СМЕНА ПАРОЛЯ (обязательная при временном пароле) ────────────────────────

@auth_bp.route('/change-password', methods=['GET', 'POST'])
def change_password():
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        new_pw  = request.form.get('new_password', '').strip()
        confirm = request.form.get('confirm_password', '').strip()

        if len(new_pw) < 6:
            flash('Пароль должен содержать не менее 6 символов', 'error')
        elif new_pw != confirm:
            flash('Пароли не совпадают', 'error')
        else:
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


# ─── ВЫХОД ───────────────────────────────────────────────────────────────────

@auth_bp.route('/logout')
def logout():
    if 'user_id' in session:
        conn = get_db()
        _log_login(conn, session['user_id'], session.get('username', '—'), 'logout',
                   request.remote_addr or '—')
        conn.close()
    session.clear()
    return redirect(url_for('auth.login'))


# ─── IMPERSONATION ───────────────────────────────────────────────────────────

@auth_bp.route('/impersonate/<int:user_id>')
def impersonate(user_id):
    if session.get('role') != 'admin':
        abort(403)

    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
    db.close()

    if not user:
        abort(404)

    # Сохраняем исходную сессию администратора
    session['original_user_id']   = session['user_id']
    session['original_username']  = session['username']
    session['original_full_name'] = session.get('full_name', session['username'])
    session['original_role']      = session['role']

    # Сохраняем права администратора
    from auth_utils import ALL_PERMISSIONS
    for key in ALL_PERMISSIONS:
        session[f'original_perm_{key}'] = session.get(f'perm_{key}', 0)

    # Подменяем на выбранного пользователя
    session['user_id']   = user['id']
    session['username']  = user['username']
    session['full_name'] = user['full_name']
    session['role']      = user['role']
    session['is_impersonating'] = True

    load_permissions_to_session(user)

    return redirect('/')


@auth_bp.route('/impersonate/stop')
def impersonate_stop():
    if not session.get('is_impersonating'):
        return redirect('/')

    from auth_utils import ALL_PERMISSIONS

    session['user_id']   = session.pop('original_user_id')
    session['username']  = session.pop('original_username')
    session['full_name'] = session.pop('original_full_name', session['username'])
    session['role']      = session.pop('original_role')
    session.pop('is_impersonating', None)

    # Восстанавливаем права администратора
    for key in ALL_PERMISSIONS:
        session[f'perm_{key}'] = session.pop(f'original_perm_{key}', 1)

    return redirect('/')