# ╔════════════════════════════════════════════════════════════════════════╗
# ║                              paths.py                                     ║
# ║  Единый источник правды для всех runtime-путей приложения KITEZH.         ║
# ║                                                                          ║
# ║  Назначение: развязать модули от вычисления корня проекта через          ║
# ║  os.path.dirname(os.path.abspath(__file__)). Любой модуль (в том числе   ║
# ║  перенесённый в подпакет, например updater/_updater.py) импортирует      ║
# ║  PROJECT_ROOT отсюда и получает КОРЕНЬ проекта, а не свою папку.          ║
# ║                                                                          ║
# ║  Использование:                                                          ║
# ║      from paths import PROJECT_ROOT as BASE_DIR                          ║
# ║  либо точечно:                                                           ║
# ║      from paths import DB_PATH, UPLOADS_DIR, ROADMAP_PATH                 ║
# ╚════════════════════════════════════════════════════════════════════════╝

import os

# ─── КОРЕНЬ ПРОЕКТА ──────────────────────────────────────────────────────────
# paths.py лежит в корне проекта, поэтому его директория = корень.
# Это работает и для модулей в подпакетах: они импортируют PROJECT_ROOT,
# а не вычисляют путь от своего __file__.
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# Обратносовместимый алиас (часть кода ожидает имя BASE_DIR).
BASE_DIR = PROJECT_ROOT

# ─── КАТАЛОГИ ДАННЫХ ─────────────────────────────────────────────────────────
DB_DIR      = os.path.join(PROJECT_ROOT, 'db')
DB_PATH     = os.path.join(DB_DIR, 'database.db')
UPLOADS_DIR = os.path.join(PROJECT_ROOT, 'uploads')
UPLOADS_TMP = os.path.join(PROJECT_ROOT, 'uploads', 'tmp')
REPORTS_DIR = os.path.join(PROJECT_ROOT, 'reports')
LOGS_DIR    = os.path.join(PROJECT_ROOT, 'logs')

# ─── ФАЙЛЫ КОНФИГУРАЦИИ / КОДА В КОРНЕ ───────────────────────────────────────
ENV_PATH       = os.path.join(PROJECT_ROOT, '.env')
CHANGELOG_PATH = os.path.join(PROJECT_ROOT, 'changelog.py')
# ВАЖНО: roadmap теперь живёт в пакете services (перенесён на этапе B3),
# приложение читает его как `from services.roadmap import ROADMAP`.
# sync_changelog.py должен перегенерировать ИМЕННО этот файл, иначе десинхрон.
ROADMAP_PATH   = os.path.join(PROJECT_ROOT, 'services', 'roadmap.py')

# ─── ФЛАГИ / СЛУЖЕБНЫЕ ФАЙЛЫ ПОДСИСТЕМЫ ОБНОВЛЕНИЯ ───────────────────────────
COMMIT_FILE      = os.path.join(PROJECT_ROOT, '_last_commit.txt')
BRANCH_FILE      = os.path.join(PROJECT_ROOT, '_branch.txt')
ZIP_PATH         = os.path.join(PROJECT_ROOT, '_kitezh_update.zip')
RESTART_FLAG     = os.path.join(PROJECT_ROOT, '_restart.flag')
LOCK_FILE        = os.path.join(PROJECT_ROOT, '_updating.lock')
MAINTENANCE_FLAG = os.path.join(PROJECT_ROOT, '.maintenance')
SECRET_KEY_FILE  = os.path.join(PROJECT_ROOT, '_secret.key')

# ─── ГАРАНТИЯ СУЩЕСТВОВАНИЯ КАТАЛОГОВ ───────────────────────────────────────
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(UPLOADS_TMP, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)
