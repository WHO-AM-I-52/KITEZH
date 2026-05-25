# ╔══════════════════════════════════════════════════════════════════════════╗
# ║                      make_db_template.py                                ║
# ║  Создаёт облегчённый шаблон БД (db_template.db) из боевой database.db   ║
# ║                                                                          ║
# ║  Что делает:                                                             ║
# ║    1. Копирует database.db → db_template.db                             ║
# ║    2. Очищает боевые данные (заявки, уведомления, история и т.п.)       ║
# ║    3. Оставляет структуру таблиц + справочники + admin-пользователя     ║
# ║                                                                          ║
# ║  Использование:                                                          ║
# ║    python make_db_template.py                                            ║
# ║                                                                          ║
# ║  ВАЖНО: запускай только когда в database.db нужная схема и справочники  ║
# ╚══════════════════════════════════════════════════════════════════════════╝

import sqlite3
import shutil
import os

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
DB_DIR       = os.path.join(BASE_DIR, "db")
SRC_DB       = os.path.join(DB_DIR, "database.db")
TEMPLATE_DB  = os.path.join(DB_DIR, "db_template.db")

# ─── Таблицы с боевыми данными — будут очищены в шаблоне ────────────────
# Добавь сюда любые таблицы, которые содержат пользовательские записи
TABLES_TO_CLEAR = [
    "requests",          # Обращения
    "notifications",     # Уведомления
    "savedfilters",      # Сохранённые фильтры
    "favorites",         # Избранное
    "activity_log",      # Лог активности (если есть)
    "request_history",   # История изменений обращений (если есть)
]

# ─── Таблицы, которые ОСТАЮТСЯ нетронутыми (справочники, пользователи) ───
# users          → admin-аккаунт остаётся
# classifiers    → районы, правовые формы, типы источников
# okved          → справочник ОКВЭД
# subject_types  → Предмет обращения (подбор зу, мер поддержки и т.п.) — НЕ ОЧИЩАЕТСЯ
# result_types   → Итоги работы / легенда цветов для МинЭК — НЕ ОЧИщАЕТСЯ


def get_tables(conn):
    """Возвращает список всех таблиц в БД."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def main():
    if not os.path.exists(SRC_DB):
        raise SystemExit(f"[ОШИБКА] Исходная БД не найдена: {SRC_DB}")

    os.makedirs(DB_DIR, exist_ok=True)

    # 1. Копируем боевую БД в шаблон
    print(f"Копируем {SRC_DB}")
    print(f"      -> {TEMPLATE_DB}")
    shutil.copy2(SRC_DB, TEMPLATE_DB)
    print("Копия создана.\n")

    # 2. Подключаемся к шаблону
    conn = sqlite3.connect(TEMPLATE_DB)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    existing_tables = get_tables(conn)
    print(f"Таблиц в БД: {len(existing_tables)}")
    print(f"Из них будут очищены: {TABLES_TO_CLEAR}\n")

    # 3. Чистим боевые таблицы
    cleared = []
    skipped = []
    for table in TABLES_TO_CLEAR:
        if table not in existing_tables:
            print(f"  [--] {table} — таблица не найдена, пропускаем")
            skipped.append(table)
            continue
        try:
            count_before = cur.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            cur.execute(f"DELETE FROM {table}")
            # Сбрасываем автоинкремент
            cur.execute(
                "DELETE FROM sqlite_sequence WHERE name=?", (table,)
            )
            print(f"  [OK] {table} — удалено {count_before} записей")
            cleared.append(table)
        except sqlite3.Error as e:
            print(f"  [!]  {table} — ошибка: {e}")

    # 4. Делаем VACUUM чтобы уменьшить размер файла
    print("\nСжимаем шаблон (VACUUM)...")
    conn.commit()
    conn.execute("PRAGMA journal_mode=DELETE")  # VACUUM не работает в WAL
    conn.execute("VACUUM")
    conn.close()

    # 5. Итоговый размер
    size_kb = os.path.getsize(TEMPLATE_DB) // 1024
    print(f"\nГотово!")
    print(f"  Файл:   {TEMPLATE_DB}")
    print(f"  Размер: {size_kb} КБ")
    print(f"  Очищено таблиц: {len(cleared)}")
    print(f"  Пропущено: {len(skipped)}")
    print()
    print("Справочники subject_types и result_types — НЕ ОЧИЩАЮТСЯ.")
    print("Они переносятся в шаблон вместе со структурой (предзаполнены начальными значениями).")
    print()
    print("Теперь можно закоммитить db/db_template.db в GitHub.")
    print("Боевая db/database.db осталась нетронутой.")


if __name__ == "__main__":
    main()
