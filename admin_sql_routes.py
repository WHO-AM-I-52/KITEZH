"""
admin_sql_routes.py — консоль прямых SQL-запросов для администратора.

Добавить в app.py одну строку:
    from admin_sql_routes import admin_sql_bp
    app.register_blueprint(admin_sql_bp)
Доступ: http://127.0.0.1:5000/admin/sql
"""

from flask import Blueprint, render_template, request, session, redirect, url_for, jsonify
from db import get_db
import re
from datetime import datetime

admin_sql_bp = Blueprint('admin_sql', __name__, url_prefix='/admin/sql')

# Запрещённые ключевые слова (case-insensitive)
_BLOCKED = re.compile(
    r'\b(DROP|ATTACH|DETACH|PRAGMA|VACUUM|sqlite_master|sqlite_temp_master)\b',
    re.IGNORECASE
)


def _require_admin():
    """None если OK, Response если надо редиректировать."""
    if session.get('role') != 'admin':
        return redirect(url_for('login_bp.login'))
    return None


def _ensure_log_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_sql_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            executed_at TEXT NOT NULL,
            user_id    INTEGER,
            username   TEXT,
            query      TEXT NOT NULL,
            rows_affected INTEGER,
            ok         INTEGER NOT NULL DEFAULT 1,
            error      TEXT
        )
    """)


@admin_sql_bp.route('/', methods=['GET'])
def sql_console():
    guard = _require_admin()
    if guard:
        return guard
    return render_template('admin/sql.html')


@admin_sql_bp.route('/', methods=['POST'])
def sql_execute():
    guard = _require_admin()
    if guard:
        return jsonify({'error': 'Нет доступа'}), 403

    query = (request.json or {}).get('query', '').strip()
    if not query:
        return jsonify({'error': 'Запрос пустой'}), 400

    if _BLOCKED.search(query):
        return jsonify({'error': 'Запрос содержит запрещённую операцию (DROP/ATTACH/PRAGMA/VACUUM)'}), 400

    conn = get_db()
    _ensure_log_table(conn)

    user_id  = session.get('user_id')
    username = session.get('username', '—')
    ok, error, columns, rows, rows_affected = 1, None, [], [], 0

    try:
        cursor = conn.execute(query)
        conn.commit()
        rows_affected = cursor.rowcount

        if cursor.description:  # SELECT
            columns = [d[0] for d in cursor.description]
            rows = [list(r) for r in cursor.fetchall()]
        else:
            rows_affected = max(rows_affected, 0)

    except Exception as e:
        ok = 0
        error = str(e)
        conn.rollback()

    # Логируем
    try:
        conn.execute("""
            INSERT INTO admin_sql_log
                (executed_at, user_id, username, query, rows_affected, ok, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (datetime.now().isoformat(timespec='seconds'),
               user_id, username, query, rows_affected, ok, error))
        conn.commit()
    except Exception:
        pass

    if not ok:
        return jsonify({'error': error}), 400

    return jsonify({
        'columns': columns,
        'rows': rows,
        'rows_affected': rows_affected,
        'is_select': bool(columns)
    })
