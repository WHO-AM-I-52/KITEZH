# ╔══════════════════════════════════════════════════════════════╗
# ║ settings_routes.py                                           ║
# ║ feat #10: Страница настроек пользователя                    ║
# ║  - смена пароля                                              ║
# ║  - выбор темы (light/dark/system)                           ║
# ║  - уведомления на почту вкл/выкл                            ║
# ║ feat: переключение ветки main/dev (только admin)            ║
# ╚══════════════════════════════════════════════════════════════╝

import os
import sys
import subprocess
from flask import Blueprint, render_template, request, session, redirect, url_for, flash
from db import get_db, BASE_DIR
from auth_utils import login_required, hash_pw

settings_bp = Blueprint('settings', __name__)

BRANCH_FILE  = os.path.join(BASE_DIR, '_branch.txt')
RESTART_FLAG = os.path.join(BASE_DIR, '_restart.flag')
LOCK_FILE    = os.path.join(BASE_DIR, '_updating.lock')


def _get_active_branch() -> str:
    """Читает активную ветку из _branch.txt (по умолчанию main)."""
    if os.path.exists(BRANCH_FILE):
        try:
            val = open(BRANCH_FILE, encoding='utf-8').read().strip()
            if val in ('main', 'dev'):
                return val
        except Exception:
            pass
    return 'main'


@settings_bp.route('/settings', methods=['GET'])
@login_required
def settings():
    db = get_db()
    user = db.execute(
        'SELECT id, username, full_name, role, email, theme, email_notifications '
        'FROM users WHERE id = ?',
        (session['user_id'],)
    ).fetchone()
    db.close()
    active_branch = _get_active_branch()
    return render_template('settings.html', user=user, active_branch=active_branch)


@settings_bp.route('/settings/password', methods=['POST'])
@login_required
def settings_password():
    current_pw  = request.form.get('current_password', '').strip()
    new_pw      = request.form.get('new_password', '').strip()
    confirm_pw  = request.form.get('confirm_password', '').strip()

    if not current_pw or not new_pw or not confirm_pw:
        flash('Заполните все поля для смены пароля.', 'error')
        return redirect(url_for('settings.settings'))

    if new_pw != confirm_pw:
        flash('Новый пароль и подтверждение не совпадают.', 'error')
        return redirect(url_for('settings.settings'))

    if len(new_pw) < 6:
        flash('Новый пароль должен содержать не менее 6 символов.', 'error')
        return redirect(url_for('settings.settings'))

    db = get_db()
    user = db.execute(
        'SELECT password FROM users WHERE id = ?', (session['user_id'],)
    ).fetchone()

    if not user or user['password'] != hash_pw(current_pw):
        db.close()
        flash('Текущий пароль указан неверно.', 'error')
        return redirect(url_for('settings.settings'))

    db.execute(
        'UPDATE users SET password = ?, must_change_password = 0 WHERE id = ?',
        (hash_pw(new_pw), session['user_id'])
    )
    db.commit()
    db.close()
    flash('Пароль успешно изменён.', 'success')
    return redirect(url_for('settings.settings'))


@settings_bp.route('/settings/preferences', methods=['POST'])
@login_required
def settings_preferences():
    theme               = request.form.get('theme', 'light')
    email_notifications = 1 if request.form.get('email_notifications') else 0
    email               = request.form.get('email', '').strip()

    if theme not in ('light', 'dark', 'zone', 'system'):
        theme = 'light'

    db = get_db()
    cols = [r[1] for r in db.execute('PRAGMA table_info(users)').fetchall()]
    if 'email' not in cols:
        db.execute('ALTER TABLE users ADD COLUMN email TEXT DEFAULT NULL')
    if 'theme' not in cols:
        db.execute("ALTER TABLE users ADD COLUMN theme TEXT DEFAULT 'light'")
    if 'email_notifications' not in cols:
        db.execute('ALTER TABLE users ADD COLUMN email_notifications INTEGER DEFAULT 0')

    db.execute(
        'UPDATE users SET theme = ?, email_notifications = ?, email = ? WHERE id = ?',
        (theme, email_notifications, email or None, session['user_id'])
    )
    db.commit()
    db.close()

    session['theme'] = theme
    flash('Настройки сохранены.', 'success')
    return redirect(url_for('settings.settings'))


@settings_bp.route('/settings/switch-branch', methods=['POST'])
@login_required
def switch_branch():
    """Переключает активную ветку (main/dev) и перезапускает сервер."""
    if session.get('role') != 'admin':
        flash('Недостаточно прав.', 'error')
        return redirect(url_for('settings.settings'))

    # Защита от двойного запуска
    if os.path.exists(LOCK_FILE):
        flash('Переключение уже выполняется. Подождите.', 'warning')
        return redirect(url_for('settings.settings'))

    target = request.form.get('branch', 'main')
    if target not in ('main', 'dev'):
        flash('Неверная ветка.', 'error')
        return redirect(url_for('settings.settings'))

    current = _get_active_branch()
    if target == current:
        flash(f'Уже на ветке {target}.', 'info')
        return redirect(url_for('settings.settings'))

    # Записываем lock
    try:
        open(LOCK_FILE, 'w').write('switching')
    except Exception:
        pass

    try:
        # Сохраняем целевую ветку в _branch.txt
        with open(BRANCH_FILE, 'w', encoding='utf-8') as f:
            f.write(target)

        # Запускаем _updater.py в фоне (скачает архив нужной ветки)
        updater = os.path.join(BASE_DIR, '_updater.py')
        subprocess.Popen(
            [sys.executable, updater],
            cwd=BASE_DIR,
            stdout=open(os.path.join(BASE_DIR, '_switch.log'), 'w', encoding='utf-8'),
            stderr=subprocess.STDOUT,
        )

        # Создаём _restart.flag — run_server.py увидит его и вернёт exit(42)
        # батник перезапустит сервер автоматически
        with open(RESTART_FLAG, 'w') as f:
            f.write('branch-switch')

    except Exception as e:
        # Откатываем ветку при ошибке
        with open(BRANCH_FILE, 'w', encoding='utf-8') as f:
            f.write(current)
        try:
            os.remove(LOCK_FILE)
        except Exception:
            pass
        flash(f'Ошибка при переключении: {e}', 'error')
        return redirect(url_for('settings.settings'))

    # Останавливаем Flask — run_server.py увидит _restart.flag → exit(42) → батник перезапустит
    label = '🧪 DEV' if target == 'dev' else '✅ main'
    flash(f'Переключение на {label}. Сервер перезапускается...', 'success')

    # Даём Flask отправить ответ, потом завершаем процесс
    import threading
    def _shutdown():
        import time
        time.sleep(1.5)
        os.kill(os.getpid(), 15)  # SIGTERM → run_server.py поймает и проверит _restart.flag
    threading.Thread(target=_shutdown, daemon=True).start()

    return redirect(url_for('settings.settings'))
