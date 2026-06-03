# ╔══════════════════════════════════════════════════════════════╗
# ║ context_processors.py                                        ║
# ║ Глобальные переменные для всех Jinja-шаблонов                ║
# ║ (вынесено из app.py)                                         ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import session
from db import get_db
from changelog import CHANGELOG


def inject_globals():
    users_for_impersonate = []
    unread_count = 0
    active_requests_count = 0

    if session.get('user_id'):
        db = get_db()

        unread_count = db.execute(
            'SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0',
            (session['user_id'],)
        ).fetchone()[0]

        try:
            if session.get('role') == 'admin' or session.get('can_view_all'):
                active_requests_count = db.execute(
                    "SELECT COUNT(*) FROM requests "
                    "WHERE status NOT IN ('closed', 'draft')"
                ).fetchone()[0]
            else:
                active_requests_count = db.execute(
                    "SELECT COUNT(*) FROM requests "
                    "WHERE status NOT IN ('closed', 'draft') AND created_by=?",
                    (session['user_id'],)
                ).fetchone()[0]
        except Exception:
            active_requests_count = 0

        if session.get('role') == 'admin':
            users_for_impersonate = db.execute(
                'SELECT id,full_name,role FROM users WHERE id!=? ORDER BY full_name',
                (session.get('user_id', 0),)
            ).fetchall()
        db.close()

    from auth_utils import ALL_PERMISSIONS, get_user_perm
    perms = {key: get_user_perm(key) for key in ALL_PERMISSIONS}

    return dict(
        app_version=CHANGELOG[0]['version'] if CHANGELOG else '—',
        app_name='InvestLand',
        app_subtitle='Инвестиционный земельный модуль Нижегородской области',
        unread_count=unread_count,
        active_requests_count=active_requests_count,
        users_for_impersonate=users_for_impersonate,
        perms=perms,
        ALL_PERMISSIONS=ALL_PERMISSIONS,
    )
