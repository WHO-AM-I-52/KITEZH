# ╔══════════════════════════════════════════════════════════════
# ║  kitezh_logger.py                                          ║
# ║  Централизованный логгер ошибок приложения.          ║
# ║  Ротация каждые 3 дня, хранит 1 архивный файл.  ║
# ║  Логи сохраняются в logs/kitezh_errors.log            ║
# ║  Импортируйте err_logger из этого модуля              ║
# ║  в любом месте проекта.                              ║
# ╚══════════════════════════════════════════════════════════════

import os
import logging
from logging.handlers import TimedRotatingFileHandler

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)

_LOG_PATH = os.path.join(LOGS_DIR, 'kitezh_errors.log')

err_logger = logging.getLogger('kitezh.errors')

if not err_logger.handlers:
    _h = TimedRotatingFileHandler(
        _LOG_PATH,
        when='D',
        interval=3,
        backupCount=1,
        encoding='utf-8',
    )
    _h.setFormatter(logging.Formatter('%(asctime)s  %(message)s'))
    err_logger.addHandler(_h)
    err_logger.setLevel(logging.ERROR)
