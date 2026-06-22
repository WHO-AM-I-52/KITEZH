# ╔══════════════════════════════════════════════════════════════╗
# ║ settings_routes.py                                           ║
# ║ feat #10: Страница настроек пользователя                    ║
# ║  - смена пароля                                              ║
# ║  - выбор темы (light/dark/zone/system)                      ║
# ║  - уведомления на почту вкл/выкл                            ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import Blueprint, render_template, request, session, redirect, url_for, flash
from db import get_db
from core.auth_utils import login_required, hash_pw, check_pw

settings_bp = Blueprint('settings', __name__)


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
    return render_template('settings.html', user=user)


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

    if not user or not check_pw(user['password'], current_pw):
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
