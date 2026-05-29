# ╔══════════════════════════════════════════════════════════════╗
# ║                      auth_utils.py                           ║
# ║  v2.3: +can_export_full, +can_import_full                    ║
# ╚══════════════════════════════════════════════════════════════╝

import hashlib
from functools import wraps
from flask import session, redirect, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash


# ─── ХЕШИРОВАНИЕ ───────────────────────────────────────────────────────────

def hash_pw(p: str) -> str:
    """Хеширует пароль через werkzeug (scrypt или pbkdf2 в зависимости от версии)."""
    return generate_password_hash(p)


def check_pw(stored: str, entered: str) -> bool:
    """
    Проверяет пароль. Поддерживает все форматы:
      - scrypt:...   (werkzeug >= 2.3, новый дефолт)
      - pbkdf2:...   (werkzeug < 2.3)
      - 64-сим. hex  (legacy SHA-256 без соли)
    """
    if not stored:
        return False
    if stored.startswith('scrypt:') or stored.startswith('pbkdf2:'):
        return check_password_hash(stored, entered)
    return stored == hashlib.sha256(entered.encode()).hexdigest()


def is_legacy_hash(stored: str) -> bool:
    """Возвращает True если хеш старый (SHA-256 без соли)."""
    return bool(stored) and not stored.startswith('scrypt:') and not stored.startswith('pbkdf2:')


# ─── ПРАВА ───────────────────────────────────────────────────────────────

ALL_PERMISSIONS = {
    'can_create':       'Создавать обращения',
    'can_edit_others':  'Редактировать чужие обращения',
    'can_confirm':      'Принимать / отклонять обращения',
    'can_delete':       'Удалять обращения',
    'can_rollback':     'Откат истории',
    'can_export':       'Экспорт в Excel (стандартный)',
    'can_export_full':  'Скачать полную базу (Excel)',
    'can_import_full':  'Загрузить обновлённый Excel (импорт)',
    'can_classifiers':  'Управление справочниками',
    'can_users':        'Управление пользователями',
    'can_view_all':     'Видит все обращения (вкл. поиск)',
}

ADMIN_PERMISSIONS = {k: 1 for k in ALL_PERMISSIONS}


def get_user_perm(key: str) -> bool:
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
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') != 'admin':
            flash('Доступ запрещён', 'error')
            return redirect(url_for('requests.index'))
        return f(*args, **kwargs)
    return decorated


def permission_required(perm_key: str):
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
    if user_row['role'] == 'admin':
        for key in ALL_PERMISSIONS:
            session[f'perm_{key}'] = 1
    else:
        for key in ALL_PERMISSIONS:
            session[f'perm_{key}'] = int(user_row[key] or 0)
