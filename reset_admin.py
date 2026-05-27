# УТИЛИТА: сброс пароля admin
# Запустить: python reset_admin.py
# УДАЛИТЕ ФАЙЛ ПОСЛЕ ИСПОЛЬЗОВАНИЯ!

import sqlite3
import os
import sys

# Добавляем папку проекта в sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from auth_utils import hash_pw

# Путь к БД
# Проверяем оба возможных пути
for db_path in ['db/database.db', 'database.db']:
    if os.path.exists(db_path):
        break
else:
    print('[ОШИБКА] Файл database.db не найден!')
    sys.exit(1)

NEW_PASSWORD = 'admin123'

conn = sqlite3.connect(db_path)
conn.execute(
    "UPDATE users SET password=?, must_change_password=0 WHERE username='admin'",
    (hash_pw(NEW_PASSWORD),)
)
conn.commit()

# Проверяем что записалось
row = conn.execute("SELECT username, must_change_password FROM users WHERE username='admin'").fetchone()
conn.close()

if row:
    print(f'[ОК] Пароль admin сброшен.')
    print(f'     Логин:  admin')
    print(f'     Пароль: {NEW_PASSWORD}')
    print(f'     must_change_password = {row[1]}')
    print()
    print('[ВНИМАНИЕ] Удалите этот файл после входа в систему!')
else:
    print('[ОШИБКА] Пользователь admin не найден в БД!')
