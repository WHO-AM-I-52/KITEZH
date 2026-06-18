# ╔══════════════════════════════════════════════════════════════╗
# ║                      info_routes.py                          ║
# ║  Сервисные страницы: уведомления, журнал изменений, онлайн   ║
# ║  v2.2.0: /ping фиксирует присутствие, /api/online — счётчик  ║
# ║  v2.4.0: /dashboard роут добавлен                            ║
# ║  v2.5.0: управление уведомлениями (mark_all_read,            ║
# ║           delete_selected, delete_read)                      ║
# ║  v2.6.0: /maintenance/on|off|status — ручное и авто ТО       ║
# ║  v2.7.0: /api/tray/notify-level — уровень уведомлений трея    ║
# ║  v2.8.0: /api/online возвращает users[] для tooltip          ║
# ║  v2.9.0: /api/update/* вынесен в update_routes.py             ║
# ║  v2.9.1: fix — /ping использует utcnow() для синхронизации   ║
# ║           с datetime('now') SQLite (UTC)                      ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import Blueprint, render_template, session, jsonify, request as flask_request, redirect, url_for
from db import get_db, BASE_DIR
from auth_utils import login_required
from activity_log import log_action
from changelog import CHANGELOG
from roadmap import ROADMAP
from dashboard import build_dash
from datetime import datetime
import os

misc_bp = Blueprint('misc', __name__)

_MAINTENANCE_FLAG = os.path.join(BASE_DIR, '.maintenance')


@misc_bp.route('/notifications')
@login_required
def notifications():
    conn = get_db()
    items = conn.execute(
        "SELECT id, message, link, is_read, created_at FROM notifications WHERE user_id=? ORDER BY created_at DESC",
        (session['user_id'],)
    ).fetchall()
    conn.close()
    return render_template('notifications.html', items=items)


@misc_bp.route('/notifications/mark_all_read', methods=['POST'])
@login_required
def notifications_mark_all_read():
    conn = get_db()
    conn.execute(
        "UPDATE notifications SET is_read=1 WHERE user_id=?",
        (session['user_id'],)
    )
    log_action(conn, session['user_id'], 'notifications_mark_all_read', detail='Помечены все уведомления как прочитанные')
    conn.commit()
    conn.close()
    return redirect(url_for('misc.notifications'))


@misc_bp.route('/notifications/delete_selected', methods=['POST'])
@login_required
def notifications_delete_selected():
    ids = flask_request.form.getlist('ids')
    if ids:
        conn = get_db()
        placeholders = ','.join('?' * len(ids))
        conn.execute(
            f"DELETE FROM notifications WHERE id IN ({placeholders}) AND user_id=?",
            (*ids, session['user_id'])
        )
        log_action(conn, session['user_id'], 'notifications_delete_selected', detail=f'Удалено уведомлений: {len(ids)}')
        conn.commit()
        conn.close()
    return redirect(url_for('misc.notifications'))


@misc_bp.route('/notifications/delete_read', methods=['POST'])
@login_required
def notifications_delete_read():
    conn = get_db()
    conn.execute(
        "DELETE FROM notifications WHERE user_id=? AND is_read=1",
        (session['user_id'],)
    )
    log_action(conn, session['user_id'], 'notifications_delete_read', detail='Удалены все прочитанные уведомления')
    conn.commit()
    conn.close()
    return redirect(url_for('misc.notifications'))


@misc_bp.route('/changelog')
@login_required
def changelog():
    conn = get_db()
    row = conn.execute(
        "SELECT value FROM classifiers WHERE category=? LIMIT 1",
        ('tray_notify_level',)
    ).fetchone()
    conn.close()
    tray_notify_level = row['value'] if row else 'critical'
    current_version = CHANGELOG[0]['version'] if CHANGELOG else ''
    session['seen_version'] = current_version
    return render_template('changelog.html', changelog=CHANGELOG,
                           version=current_version, roadmap=ROADMAP,
                           tray_notify_level=tray_notify_level)


@misc_bp.route('/dashboard')
@login_required
def dashboard():
    period = flask_request.args.get('period', 'all')
    conn = get_db()
    data = build_dash(conn, period)
    conn.close()
    return render_template('dashboard.html', dash=data, period=period)


@misc_bp.route('/ping')
def ping():
    """Хеартбит: обновляет online_presence если пользователь авторизован."""
    uid = session.get('user_id')
    if uid:
        try:
            conn = get_db()
            # Используем utcnow() для синхронизации с datetime('now') SQLite,
            # который всегда возвращает UTC.
            now = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
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
    """Возвращает число и список пользователей активных за последние 5 минут."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT u.full_name
        FROM online_presence op
        JOIN users u ON u.id = op.user_id
        WHERE op.last_seen >= datetime('now', '-5 minutes')
        ORDER BY op.last_seen DESC
        """
    ).fetchall()
    conn.close()
    users = [r['full_name'] for r in rows if r['full_name']]
    return jsonify({'online': len(users), 'users': users})


# ─── Управление режимом ТО ────────────────────────────────────────────────────────

@misc_bp.route('/maintenance/on', methods=['POST'])
def maintenance_on():
    """Включает режим ТО вручную (только admin)."""
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403
    try:
        open(_MAINTENANCE_FLAG, 'w').close()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    conn = get_db()
    log_action(conn, session['user_id'], 'maintenance_on', detail='Включён режим технического обслуживания')
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'active': True})


@misc_bp.route('/maintenance/off', methods=['POST'])
def maintenance_off():
    """Выключает режим ТО вручную (только admin)."""
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403
    try:
        if os.path.exists(_MAINTENANCE_FLAG):
            os.remove(_MAINTENANCE_FLAG)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    conn = get_db()
    log_action(conn, session['user_id'], 'maintenance_off', detail='Выключён режим технического обслуживания')
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'active': False})


@misc_bp.route('/api/maintenance/status')
def maintenance_status():
    """Возвращает текущий статус режима ТО (только admin)."""
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403
    return jsonify({'active': os.path.exists(_MAINTENANCE_FLAG)})


# ─── Уровень уведомлений трея ─────────────────────────────────────────────────

@misc_bp.route('/api/tray/notify-level', methods=['POST'])
def api_tray_notify_level():
    """
    Переключает уровень уведомлений трея (GET — чтение, POST — запись).
    Доступно только admin.
    Тело: { "level": "critical" } или { "level": "extended" }
    """
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403

    data = flask_request.get_json(silent=True) or {}
    level = data.get('level', '')
    if level not in ('critical', 'extended'):
        return jsonify({'error': 'invalid_level', 'allowed': ['critical', 'extended']}), 400

    conn = get_db()
    conn.execute(
        "UPDATE classifiers SET value=? WHERE category=?",
        (level, 'tray_notify_level')
    )
    log_action(conn, session['user_id'], 'tray_notify_level_change',
               detail=f'Уровень уведомлений трея: {level}')
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'level': level})


@misc_bp.route('/api/tray/notify-level', methods=['GET'])
def api_tray_notify_level_get():
    """Возвращает текущий уровень уведомлений трея (только admin)."""
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403
    conn = get_db()
    row = conn.execute(
        "SELECT value FROM classifiers WHERE category=? LIMIT 1",
        ('tray_notify_level',)
    ).fetchone()
    conn.close()
    return jsonify({'level': row['value'] if row else 'critical'})
