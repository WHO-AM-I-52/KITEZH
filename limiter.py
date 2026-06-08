# ╔══════════════════════════════════════════════════════════════╗
# ║ limiter.py                                                   ║
# ║ Единственный экземпляр Limiter — импортируется из app.py     ║
# ║ и login_routes.py, чтобы избежать циклических импортов.      ║
# ╚══════════════════════════════════════════════════════════════╝

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],          # глобальный лимит не ставим — только точечно
    storage_uri="memory://",    # in-process; при multi-worker заменить на redis://
)
