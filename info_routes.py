# ╔══════════════════════════════════════════════════════════════╗
# ║                      info_routes.py                          ║
# ║  Сервисные страницы: уведомления и журнал изменений          ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import Blueprint, render_template, session
from db import get_db
from auth_utils import login_required
from changelog import CHANGELOG, ROADMAP

misc_bp = Blueprint('misc', __name__)


@misc_bp.route('/notifications')
@login_required
def notifications():
    conn = get_db()
    items = conn.execute(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC",
        (session['user_id'],)
    ).fetchall()
    conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (session['user_id'],))
    conn.commit()
    conn.close()
    return render_template('notifications.html', items=items)


@misc_bp.route('/changelog')
@login_required
def changelog():
    current_version = CHANGELOG[0]['version'] if CHANGELOG else ''
    return render_template('changelog.html', changelog=CHANGELOG,
                           version=current_version, roadmap=ROADMAP)

@misc_bp.route('/ping')
def ping():
    return '', 204