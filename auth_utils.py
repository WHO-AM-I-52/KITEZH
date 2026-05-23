# ╔══════════════════════════════════════════════════════════════╗
# ║                      auth_utils.py                           ║
# ║  v2.0: гибкая система прав вместо жёстких ролей             ║
# ╚══════════════════════════════════════════════════════════════╝

import hashlib
from functools import wraps
from flask import session, redirect, url_for, flash


def hash_pw(p: str) -> str:
    return hashlib.sha256(p.encode()).hexdigest()


# ─── ПРАВА ПОЛЬЗОВАТЕЛЯ (ключи = поля в таблице users) ──────────────────────

ALL_PERMISSIONS = {
    'can_create':      'Создавать обращения',
    'can_edit_others': 'Редактировать чужие обращения',
    'can_confirm':     'Принимать / отклонять обращения',
    'can_delete':      'Удалять обращения',
    'can_rollback':    'Откат истории',
    'can_export':      'Экспорт в Excel',
    'can_classifiers': 'Управление справочниками',
    'can_users':       'Управление пользователями',
    'can_view_all':    'Просмотр всех обращений',
}

# Пресет: admin получает все права автоматически
ADMIN_PERMISSIONS = {k: 1 for k in ALL_PERMISSIONS}


def get_user_perm(key: str) -> bool:
    """
    Проверяет право текущего пользователя по ключу.
    Администратор всегда имеет все права.
    """
    if session.get('role') == 'admin':
        return True
    return bool(session.get(f'perm_{key}', 0))


# ─── ДЕКОРАТОРЫ ──────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Оставлен для обратной совместимости. Проверяет role==admin."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'admin':
            flash('Доступ запрещён', 'error')
            return redirect(url_for('requests.index'))
        return f(*args, **kwargs)
    return decorated


def permission_required(perm_key: str):
    """
    Универсальный декоратор для проверки конкретного права.
    Использование: @permission_required('can_confirm')
    """
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('auth.login'))
            if not get_user_perm(perm_key):
                flash(f'Недостаточно прав: {ALL_PERMISSIONS.get(perm_key, perm_key)}', 'error')
                return redirect(url_for('requests.index'))
            return f(*args, **kwargs)
        return decorated
    return decorator


def load_permissions_to_session(user_row) -> None:
    """
    Вызывается при логине. Записывает все права пользователя
    в сессию как perm_<key> = 0/1.
    Администратор получает все права автоматически.
    """
    if user_row['role'] == 'admin':
        for key in ALL_PERMISSIONS:
            session[f'perm_{key}'] = 1
    else:
        for key in ALL_PERMISSIONS:
            session[f'perm_{key}'] = int(user_row[key] or 0)