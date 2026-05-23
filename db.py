# ╔══════════════════════════════════════════════════════════════╗
# ║                         db.py                               ║
# ║  Подключение к базе данных и пути к папкам приложения       ║
# ╚══════════════════════════════════════════════════════════════╝

import sqlite3
import os

# ─── ПУТИ ────────────────────────────────────────────────────────────────────

# Корневая папка проекта (там где лежит этот файл)
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))

# Путь к файлу базы данных SQLite
DB_PATH     = os.path.join(BASE_DIR, 'db', 'database.db')

# Папка для загружаемых файлов обращений
UPLOADS_DIR = os.path.join(BASE_DIR, 'uploads')

# Папка для сгенерированных отчётов Excel
REPORTS_DIR = os.path.join(BASE_DIR, 'reports')

# Разрешённые расширения файлов при загрузке
ALLOWED_EXT = {'pdf', 'ppt', 'pptx', 'doc', 'docx', 'xlsx', 'zip'}

# Создаём папки, если они ещё не существуют
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)


# ─── ПОДКЛЮЧЕНИЕ К БД ────────────────────────────────────────────────────────

def get_db():
    """
    Открывает соединение с базой данных SQLite.
    - row_factory = sqlite3.Row позволяет обращаться к полям по имени (row['field'])
    - WAL-режим улучшает производительность при параллельных запросах
    """
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn