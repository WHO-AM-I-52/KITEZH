import os

from flask import request, redirect, url_for, session, send_file

from db import get_db, UPLOADS_DIR
from auth_utils import login_required
from activity_log import log_action
from . import requests_bp


@requests_bp.route('/request/<int:rid>/favorite', methods=['POST'])
@login_required
def toggle_favorite(rid):
    conn = get_db()
    uid  = session['user_id']
    row  = conn.execute(
        "SELECT id FROM favorites WHERE user_id=? AND request_id=?", (uid, rid)
    ).fetchone()
    if row:
        conn.execute("DELETE FROM favorites WHERE id=?", (row['id'],))
        log_action(conn, uid, 'favorite', rid, 'Убрано из избранного')
    else:
        conn.execute(
            "INSERT OR IGNORE INTO favorites (user_id,request_id) VALUES (?,?)", (uid, rid)
        )
        log_action(conn, uid, 'favorite', rid, 'Добавлено в избранное')
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('requests.index'))


@requests_bp.route('/uploads/<path:filename>')
@login_required
def uploaded_file(filename):
    return send_file(os.path.join(UPLOADS_DIR, filename), as_attachment=True)
