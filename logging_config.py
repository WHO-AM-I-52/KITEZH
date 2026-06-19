# ╔══════════════════════════════════════════════════════════════╗
# ║                   logging_config.py                          ║
# ║  Настройка системного логирования приложения.                ║
# ║  Подключается один раз в app.py: setup_logging(app)          ║
# ║  Файл логов: logs/app.log (ротация 5 МБ × 3 файла)          ║
# ╚══════════════════════════════════════════════════════════════╝

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging(app):
    """
    Настраивает файловый логгер для Flask-приложения.

    - Уровень: INFO (захватывает INFO, WARNING, ERROR, CRITICAL)
    - Файл: logs/app.log
    - Ротация: 5 МБ, хранить 3 последних файла
    - Формат: [2026-06-19 18:40:00] ERROR in admin_routes: ...

    Использование в модулях:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("upload failed: num=%s file=%s user=%s", num, fname, user)
    """
    os.makedirs("logs", exist_ok=True)

    fmt = logging.Formatter(
        fmt="[%(asctime)s] %(levelname)-8s in %(module)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        filename="logs/app.log",
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.INFO)

    # Корневой логгер — перехватывает все модули проекта
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)

    # Flask app.logger — для совместимости с flask.current_app.logger
    if not app.logger.handlers:
        app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)
