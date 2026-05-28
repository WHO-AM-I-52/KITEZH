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


def _table_has_cascade(conn, table: str, fk_column: str) -> bool:
    """
    Проверяет, есть ли ON DELETE CASCADE на указанном внешнем ключе таблицы.
    Использует PRAGMA foreign_key_list для чтения метаданных FK.
    """
    rows = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    for row in rows:
        # row: id, seq, table, from, to, on_update, on_delete, match
        if row['from'] == fk_column and row['on_delete'].upper() == 'CASCADE':
            return True
    return False


def _recreate_with_cascade(conn, table: str, create_sql: str, columns: str):
    """
    Безопасное пересоздание таблицы с новым DDL (добавление ON DELETE CASCADE).
    SQLite не поддерживает ALTER TABLE ... ADD CONSTRAINT, поэтому:
      1. Переименовываем старую таблицу во временную
      2. Создаём новую с правильным DDL
      3. Копируем данные
      4. Удаляем временную
    Идемпотентно: вызывается только если CASCADE ещё не установлен.
    """
    conn.execute(f"ALTER TABLE {table} RENAME TO _{table}_old")
    conn.execute(create_sql)
    conn.execute(f"INSERT INTO {table} ({columns}) SELECT {columns} FROM _{table}_old")
    conn.execute(f"DROP TABLE _{table}_old")


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

    # ════════════════════════════════════════════════════════════════
    # fix: таблица счётчиков нумерации обращений по годам
    # ════════════════════════════════════════════════════════════════
    conn.execute("""
        CREATE TABLE IF NOT EXISTS request_counters (
            year     INTEGER PRIMARY KEY,
            last_seq INTEGER NOT NULL DEFAULT 0
        )
    """)
    _init_counters_from_existing(conn)

    # ════════════════════════════════════════════════════════════════
    # fix: ON DELETE CASCADE для favorites и notifications
    #
    # Проблема: при удалении обращения (DELETE FROM requests WHERE id=?)
    # записи в favorites и notifications оставались «висеть» как сироты.
    # SQLite не умеет добавить CASCADE через ALTER TABLE — пересоздаём таблицы.
    # _table_has_cascade() проверяет текущий DDL: повторный запуск безопасен.
    # ════════════════════════════════════════════════════════════════
    conn.execute("PRAGMA foreign_keys = OFF")

    # ─ favorites
    if _table_exists(conn, 'favorites') and not _table_has_cascade(conn, 'favorites', 'request_id'):
        _recreate_with_cascade(
            conn,
            table='favorites',
            create_sql="""
                CREATE TABLE favorites (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE,
                    UNIQUE(user_id, request_id)
                )
            """,
            columns='id, user_id, request_id'
        )

    # ─ notifications
    if _table_exists(conn, 'notifications') and not _table_has_cascade(conn, 'notifications', 'request_id'):
        # notifications может не иметь request_id — проверяем наличие колонки
        if _has_column(conn, 'notifications', 'request_id'):
            _recreate_with_cascade(
                conn,
                table='notifications',
                create_sql="""
                    CREATE TABLE notifications (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        request_id INTEGER REFERENCES requests(id) ON DELETE CASCADE,
                        message    TEXT NOT NULL DEFAULT '',
                        link       TEXT,
                        is_read    INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                """,
                columns='id, user_id, request_id, message, link, is_read, created_at'
            )
        else:
            # notifications без request_id — только CASCADE по user_id
            _recreate_with_cascade(
                conn,
                table='notifications',
                create_sql="""
                    CREATE TABLE notifications (
                        id         INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        message    TEXT NOT NULL DEFAULT '',
                        link       TEXT,
                        is_read    INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                """,
                columns='id, user_id, message, link, is_read, created_at'
            )

    conn.execute("PRAGMA foreign_keys = ON")

    # ─ Индекс на notifications.request_id (для быстрого CASCADE-удаления)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_notif_request ON notifications(request_id)"
    )
    # ─ Индекс на favorites.request_id
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_fav_request ON favorites(request_id)"
    )

    conn.commit()


def _init_counters_from_existing(conn):
    """
    При первой миграции: читает существующие номера вида «ЗУ-YYYY-NNNN»
    и восстанавливает счётчики по годам, чтобы новые номера не конфликтовали.
    Повторный вызов безопасен: INSERT OR IGNORE не перезапишет уже заполненные строки.
    """
    rows = conn.execute(
        "SELECT request_number FROM requests WHERE request_number IS NOT NULL"
    ).fetchall()
    year_max = {}
    for row in rows:
        num = row[0] or ''
        parts = num.split('-')
        if len(parts) == 3:
            try:
                year = int(parts[1])
                seq  = int(parts[2])
                if year_max.get(year, 0) < seq:
                    year_max[year] = seq
            except ValueError:
                pass
    for year, max_seq in year_max.items():
        conn.execute(
            "INSERT OR IGNORE INTO request_counters (year, last_seq) VALUES (?, ?)",
            (year, max_seq)
        )


def next_request_number(conn, year: int) -> str:
    """
    Атомарно увеличивает счётчик для указанного года и возвращает
    готовый номер вида «ЗУ-YYYY-NNNN».

    Использует INSERT OR IGNORE + UPDATE + SELECT для атомарной операции в SQLite.
    Вызывать внутри уже открытой транзакции (conn.commit() делает вызывающий код).
    """
    conn.execute(
        "INSERT OR IGNORE INTO request_counters (year, last_seq) VALUES (?, 0)",
        (year,)
    )
    conn.execute(
        "UPDATE request_counters SET last_seq = last_seq + 1 WHERE year = ?",
        (year,)
    )
    seq = conn.execute(
        "SELECT last_seq FROM request_counters WHERE year = ?",
        (year,)
    ).fetchone()[0]
    return f"ЗУ-{year}-{seq:04d}"


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
