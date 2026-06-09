# ╔══════════════════════════════════════════════════════════════╗
# ║ limiter.py                                                   ║
# ║ Единственный экземпляр Limiter — импортируется из app.py     ║
# ║ и login_routes.py, чтобы избежать циклических импортов.      ║
# ║ Если flask-limiter не установлен — используется no-op заглушка.  ║
# ╚══════════════════════════════════════════════════════════════╝

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address

    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[],          # глобальный лимит не ставим — только точечно
        storage_uri="memory://",    # in-process; при multi-worker заменить на redis://
    )

except ImportError:
    import logging
    logging.getLogger(__name__).warning(
        "flask-limiter не установлен. "
        "Защита от брутфорса отключена. "
        "Установи: pip install flask-limiter"
    )

    class _DummyLimiter:
        """No-op заглушка на случай отсутствия flask-limiter."""

        def init_app(self, app):
            pass

        def limit(self, *args, **kwargs):
            return lambda f: f

        def exempt(self, f):
            return f

    limiter = _DummyLimiter()
