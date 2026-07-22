"""
admin_sql_routes.py — консоль прямых SQL-запросов для администратора.

Добавить в app.py:
    from admin_sql_routes import admin_sql_bp
    app.register_blueprint(admin_sql_bp)
Доступ: http://127.0.0.1:5000/admin/sql

v1.1: fix sql_schema — sqlite_schema + pragma_table_info(?) (#audit)
      Убрана непоследовательность: _BLOCKED запрещает PRAGMA и sqlite_master
      для пользовательского ввода, но /schema сам использовал оба в коде.
      Теперь:
      - sqlite_schema (официальное название, SQLite >= 3.33.0 2020)
      - pragma_table_info(?) — табличная форма PRAGMA без
        подстановки имени таблицы через f-строку.
"""

from flask import Blueprint, render_template, request, session, redirect, url_for, jsonify
from db import get_db
import re
from datetime import datetime

admin_sql_bp = Blueprint('admin_sql', __name__, url_prefix='/admin/sql')

# Запрещённые ключевые слова (пользовательский ввод)
_BLOCKED = re.compile(
    r'\b(DROP|ATTACH|DETACH|PRAGMA|VACUUM|sqlite_master|sqlite_temp_master|sqlite_schema)\b',
    re.IGNORECASE
)

# Мутирующие операции — требуют подтверждения
_MUTATING = re.compile(
    r'^\s*(UPDATE|DELETE|INSERT|REPLACE|TRUNCATE)\b',
    re.IGNORECASE
)

# Автолимит для SELECT без своего LIMIT
_AUTO_LIMIT = 500
_HAS_LIMIT  = re.compile(r'\bLIMIT\b', re.IGNORECASE)
_IS_SELECT  = re.compile(r'^\s*SELECT\b', re.IGNORECASE)


def _require_admin():
    """None если OK, Response если надо редиректировать."""
    if session.get('role') != 'admin':
        return redirect(url_for('login_bp.login'))
    return None


def _has_multiple_statements(query: str) -> bool:
    """True если запрос содержит более одного statement (защита от stacked queries)."""
    cleaned = re.sub(r"'[^']*'", "''", query)
    cleaned = re.sub(r'"[^"]*"', '""', cleaned)
    parts = [p.strip() for p in cleaned.split(';') if p.strip()]
    return len(parts) > 1


def _apply_auto_limit(query: str) -> str:
    """SELECT без LIMIT — автоматически добавляет LIMIT 500."""
    if _IS_SELECT.match(query) and not _HAS_LIMIT.search(query):
        return query.rstrip().rstrip(';') + f' LIMIT {_AUTO_LIMIT}'
    return query


def _ensure_log_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS admin_sql_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            executed_at   TEXT NOT NULL,
            user_id       INTEGER,
            username      TEXT,
            query         TEXT NOT NULL,
            rows_affected INTEGER,
            ok            INTEGER NOT NULL DEFAULT 1,
            error         TEXT
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

    payload   = request.json or {}
    query     = payload.get('query', '').strip()
    confirmed = payload.get('confirmed', False)

    if not query:
        return jsonify({'error': 'Запрос пустой'}), 400

    if _BLOCKED.search(query):
        return jsonify({'error': 'Запрос содержит запрещённую операцию (DROP/ATTACH/PRAGMA/VACUUM)'}), 400

    if _has_multiple_statements(query):
        return jsonify({'error': 'Нельзя выполнять несколько запросов за один раз (уберите лишние строки через ";")'}), 400

    if _MUTATING.match(query) and not confirmed:
        return jsonify({
            'needs_confirm': True,
            'message': 'Запрос изменяет данные (UPDATE/DELETE/INSERT). Вы уверены?'
        }), 200

    query_exec   = _apply_auto_limit(query)
    auto_limited = query_exec != query

    conn = get_db()
    _ensure_log_table(conn)

    user_id  = session.get('user_id')
    username = session.get('username', '—')
    ok, error, columns, rows, rows_affected = 1, None, [], [], 0

    try:
        cursor = conn.execute(query_exec)
        conn.commit()
        rows_affected = cursor.rowcount

        if cursor.description:
            columns = [d[0] for d in cursor.description]
            rows = [list(r) for r in cursor.fetchall()]
        else:
            rows_affected = max(rows_affected, 0)

    except Exception as e:
        ok = 0
        error = str(e)
        conn.rollback()

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
        'columns':      columns,
        'rows':         rows,
        'rows_affected': rows_affected,
        'is_select':    bool(columns),
        'auto_limited': auto_limited,
        'limit':        _AUTO_LIMIT,
    })


@admin_sql_bp.route('/schema', methods=['GET'])
def sql_schema():
    """
    GET /admin/sql/schema — возвращает список таблиц и их колонок.

    Использует sqlite_schema (официальное название с SQLite >= 3.33.0, 2020;
    sqlite_master остаётся как alias для обратной совместимости) и
    pragma_table_info(?) — табличную форму PRAGMA, которая принимает
    имя таблицы как параметр без подстановки через f-строку.

    Запрос серверный, не пользовательский — _BLOCKED не применяется.
    """
    guard = _require_admin()
    if guard:
        return jsonify({'error': 'Нет доступа'}), 403

    conn = get_db()
    try:
        # sqlite_schema — официальное название (SQLite >= 3.33.0, 2020);
        # sqlite_master остаётся как alias для обратной совместимости.
        tables = conn.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"
        ).fetchall()

        schema = {}
        for (tname,) in tables:
            # pragma_table_info(?) — табличная форма PRAGMA, принимает
            # имя таблицы как параметр: без f-строки, без подстановки.
            cols = conn.execute(
                'SELECT name FROM pragma_table_info(?)', (tname,)
            ).fetchall()
            schema[tname] = [c[0] for c in cols]
    finally:
        conn.close()

    return jsonify(schema)
