#!/usr/bin/env python3
# ╔══════════════════════════════════════════════════════════════╗
# ║              restore_investmap.py                           ║
# ║  Восстановление таблиц investmap_fields и                   ║
# ║  investmap_classifiers из последнего бэкапа БД.             ║
# ║                                                             ║
# ║  Запуск:  python restore_investmap.py                       ║
# ║  Опционально: python restore_investmap.py <путь_к_бэкапу>   ║
# ╚══════════════════════════════════════════════════════════════╝

import sqlite3
import os
import sys
import glob

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(BASE_DIR, 'db', 'database.db')
BACKUP_DIR = os.path.join(BASE_DIR, 'db', 'backups')

TABLES = ['investmap_fields', 'investmap_classifiers']


def find_latest_backup() -> str | None:
    pattern = os.path.join(BACKUP_DIR, '*.db')
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def get_columns(conn, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows if r[1] != 'id']


def restore_table(src_conn, dst_conn, table: str) -> int:
    if not table_exists(src_conn, table):
        print(f'  [SKIP] {table}: таблица отсутствует в бэкапе')
        return 0

    src_count = src_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    if src_count == 0:
        print(f'  [SKIP] {table}: в бэкапе 0 строк')
        return 0

    cols = get_columns(src_conn, table)
    if not cols:
        print(f'  [SKIP] {table}: не удалось определить колонки')
        return 0

    rows = src_conn.execute(
        f"SELECT {', '.join(cols)} FROM {table}"
    ).fetchall()

    placeholders = ', '.join(['?'] * len(cols))
    col_list     = ', '.join(cols)
    inserted = 0
    skipped  = 0
    for row in rows:
        try:
            dst_conn.execute(
                f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})",
                row
            )
            if dst_conn.execute('SELECT changes()').fetchone()[0]:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            print(f'  [WARN] {table}: ошибка вставки строки {row!r}: {e}')

    dst_conn.commit()
    print(f'  [OK]   {table}: вставлено {inserted}, пропущено дублей {skipped} (всего в бэкапе {src_count})')
    return inserted


def main():
    # Определяем путь к бэкапу
    if len(sys.argv) > 1:
        backup_path = sys.argv[1]
    else:
        backup_path = find_latest_backup()

    if not backup_path or not os.path.exists(backup_path):
        print('ОШИБКА: бэкап не найден.')
        print(f'  Искал в: {BACKUP_DIR}')
        print('  Укажи путь вручную: python restore_investmap.py <путь_к_бэкапу>')
        sys.exit(1)

    if not os.path.exists(DB_PATH):
        print(f'ОШИБКА: рабочая БД не найдена: {DB_PATH}')
        sys.exit(1)

    print(f'Бэкап:       {backup_path}')
    print(f'Рабочая БД:  {DB_PATH}')
    print()

    src_conn = sqlite3.connect(backup_path)
    dst_conn = sqlite3.connect(DB_PATH)
    dst_conn.execute('PRAGMA journal_mode=WAL')

    # Убеждаемся что таблицы существуют в рабочей БД
    # (после обновления _migrate() их создаст, но на всякий случай)
    dst_conn.execute("""
        CREATE TABLE IF NOT EXISTS investmap_fields (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            tech_name          TEXT    NOT NULL UNIQUE,
            display_name       TEXT    NOT NULL,
            is_required        TEXT    NOT NULL DEFAULT 'да',
            required_condition TEXT,
            classifier_num     TEXT
        )
    """)
    dst_conn.execute("""
        CREATE TABLE IF NOT EXISTS investmap_classifiers (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            classifier_num TEXT    NOT NULL,
            field_name     TEXT    NOT NULL,
            sort_order     INTEGER NOT NULL DEFAULT 0,
            value          TEXT    NOT NULL,
            UNIQUE(classifier_num, value)
        )
    """)
    dst_conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_investmap_cls_num "
        "ON investmap_classifiers(classifier_num)"
    )
    dst_conn.commit()

    total = 0
    for table in TABLES:
        total += restore_table(src_conn, dst_conn, table)

    src_conn.close()
    dst_conn.close()

    print()
    print(f'Готово. Всего восстановлено строк: {total}')
    print('Перезапусти КИТЕЖ и проверь вкладку «Инвест. карты».')


if __name__ == '__main__':
    main()
