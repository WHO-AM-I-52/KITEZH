# ╔════════════════════════════════════════════════════════════════╗
# ║                         db.py                               ║
# ║  Подключение к базе данных и пути к папкам приложения       ║
# ╚════════════════════════════════════════════════════════════════╝

import sqlite3
import os

# ─── ПУТИ ──────────────────────────────────────────────────────────────────────────────────

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BASE_DIR, 'db', 'database.db')
UPLOADS_DIR = os.path.join(BASE_DIR, 'uploads')
REPORTS_DIR = os.path.join(BASE_DIR, 'reports')
ALLOWED_EXT = {'pdf', 'ppt', 'pptx', 'doc', 'docx', 'xlsx', 'zip'}

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)


# ─── МИГРАЦИЯ ───────────────────────────────────────────────────────────────────

def _has_column(conn, table: str, column: str) -> bool:
    """Труе если колонка уже есть в таблице."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r['name'] == column for r in rows)


def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,)
    ).fetchone()
    return row is not None


def _migrate(conn):
    """
    Автоматическое добавление новых таблиц и колонок если они отсутствуют.
    ВАЖНО: все изменения должны быть идемпотентны — при повторном запуске
    на уже обновлённой БД ничего не должно ломаться.
    """

    # ─ Таблица присутствия онлайн
    conn.execute("""
        CREATE TABLE IF NOT EXISTS online_presence (
            user_id   INTEGER PRIMARY KEY,
            last_seen TEXT NOT NULL
        )
    """)

    # ─ Колонка action в request_history
    if not _has_column(conn, 'request_history', 'action'):
        conn.execute(
            "ALTER TABLE request_history ADD COLUMN action TEXT DEFAULT 'edit'"
        )

    # ════════════════════════════════════════════════════════════════
    # МинЭК: справочники и новые поля
    # ════════════════════════════════════════════════════════════════

    # ─ Справочник «Предмет обращения»
    conn.execute("""
        CREATE TABLE IF NOT EXISTS subject_types (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
    """)

    cnt = conn.execute("SELECT COUNT(*) FROM subject_types").fetchone()[0]
    if cnt == 0:
        conn.executemany(
            "INSERT OR IGNORE INTO subject_types (name) VALUES (?)",
            [
                ('подбор зу',),
                ('подбор мер поддержки',),
                ('подбор индустриального парка',),
                ('подбор зу, помещений',),
                ('консультация',),
            ]
        )

    # ─ Справочник «Итоги работы по обращению»
    result_types_exists_before = _table_exists(conn, 'result_types')
    conn.execute("""
        CREATE TABLE IF NOT EXISTS result_types (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT NOT NULL UNIQUE,
            color_hex TEXT NOT NULL DEFAULT 'FFFFFF'
        )
    """)

    default_results = [
        ('Вопрос решен',                           'FF0000'),
        ('Проект взят на сопровождение',          '92D050'),
        ('Обращение частично отработано',         'A6A6A6'),
        ('На исполнении',                         'FFFFFF'),
        ('Взято на сопровождение',                 '92D050'),
        ('В работе',                               'FFFFFF'),
        ('Отвечено',                              'FF0000'),
        ('Подобранные зу направлены инвестору',   'FF0000'),
        ('Подобранные помещения направлены инвестору', 'FF0000'),
        ('Подобранные зу и помещения направлены инвестору', 'FF0000'),
        ('Проведено рабочее совещание с инвестором',  '92D050'),
        ('Проведено совещание (с участием ОИВ/ОМС)',  '92D050'),
        ('Отказ',                                   'A6A6A6'),
    ]

    result_cnt = conn.execute("SELECT COUNT(*) FROM result_types").fetchone()[0]
    if (not result_types_exists_before) or result_cnt == 0:
        conn.executemany(
            "INSERT OR IGNORE INTO result_types (name, color_hex) VALUES (?, ?)",
            default_results
        )

    # ─ Новые поля в таблице requests
    if not _has_column(conn, 'requests', 'subject_type_id'):
        conn.execute(
            "ALTER TABLE requests ADD COLUMN subject_type_id INTEGER REFERENCES subject_types(id)"
        )

    if not _has_column(conn, 'requests', 'feedback_date'):
        conn.execute(
            "ALTER TABLE requests ADD COLUMN feedback_date TEXT"
        )

    if not _has_column(conn, 'requests', 'result_type_id'):
        conn.execute(
            "ALTER TABLE requests ADD COLUMN result_type_id INTEGER REFERENCES result_types(id)"
        )

    # ─ Bugfix #3: колонка incoming_number (номер входящего в Directum/СЭДО)
    if not _has_column(conn, 'requests', 'incoming_number'):
        conn.execute(
            "ALTER TABLE requests ADD COLUMN incoming_number TEXT"
        )

    # ════════════════════════════════════════════════════════════════
    # Права доступа: новые колонки в таблице users
    # ════════════════════════════════════════════════════════════════

    if not _has_column(conn, 'users', 'can_export_full'):
        conn.execute(
            "ALTER TABLE users ADD COLUMN can_export_full INTEGER NOT NULL DEFAULT 0"
        )

    if not _has_column(conn, 'users', 'can_import_full'):
        conn.execute(
            "ALTER TABLE users ADD COLUMN can_import_full INTEGER NOT NULL DEFAULT 0"
        )

    # ════════════════════════════════════════════════════════════════
    # Индексы — fix #6
    # ════════════════════════════════════════════════════════════════
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_req_status ON requests(status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_req_created_by ON requests(created_by)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_req_assigned ON requests(assigned_to)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_req_date ON requests(request_date)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read)"
    )

    conn.commit()


# ─── ПОДКЛЮЧЕНИЕ К БД ────────────────────────────────────────────────────────────────

def get_db():
    """
    Открывает соединение с базой данных SQLite.
    - row_factory = sqlite3.Row — обращение к полям по имени
    - WAL-режим — производительность при параллельных запросах
    - _migrate() — автоматически добавляет новые таблицы/колонки/индексы
    """
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _migrate(conn)
    return conn
