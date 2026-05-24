# ╔══════════════════════════════════════════════════════════════╗
# ║                      info_routes.py                          ║
# ║  Сервисные страницы: уведомления, журнал изменений, онлайн   ║
# ║  v2.2.0: /ping фиксирует присутствие, /api/online — счётчик  ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import Blueprint, render_template, session, jsonify, request as flask_request
from db import get_db
from auth_utils import login_required
from changelog import CHANGELOG, ROADMAP
from datetime import datetime

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
    # Отмечаем версию как просмотренную
    session['seen_version'] = current_version
    return render_template('changelog.html', changelog=CHANGELOG,
                           version=current_version, roadmap=ROADMAP)


@misc_bp.route('/ping')
def ping():
    """Heartbeat: обновляет online_presence если пользователь авторизован."""
    uid = session.get('user_id')
    if uid:
        try:
            conn = get_db()
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            conn.execute(
                """
                INSERT INTO online_presence (user_id, last_seen)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET last_seen=excluded.last_seen
                """,
                (uid, now)
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
    return '', 204


@misc_bp.route('/api/online')
@login_required
def api_online():
    """Возвращает число уникальных пользователей активных за последние 5 минут."""
    conn = get_db()
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT user_id) AS cnt
        FROM online_presence
        WHERE last_seen >= datetime('now', '-5 minutes')
        """
    ).fetchone()
    conn.close()
    count = row['cnt'] if row else 0
    return jsonify({'online': count})


@misc_bp.route('/api/search')
@login_required
def api_search():
    """Глобальный поиск по обращениям: номер или имя заявителя."""
    q = flask_request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'results': []})
    conn = get_db()
    like = f'%{q}%'
    rows = conn.execute(
        """
        SELECT id, request_number, applicant_short_name, applicant_full_name,
               status, project_name
        FROM requests
        WHERE request_number LIKE ?
           OR applicant_short_name LIKE ?
           OR applicant_full_name  LIKE ?
           OR project_name         LIKE ?
        ORDER BY id DESC