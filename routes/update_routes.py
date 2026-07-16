# ╔═══════════════════════════════════════════════════════════════╗
# ║              update_routes.py  [ФАСАД]                    ║
# ║  Собирает все маршруты обновления в единый                 ║
# ║  update_bp для регистрации в app.py.                   ║
# ║                                                               ║
# ║  Субмодули:                                              ║
# ║    update_helpers.py    — вспомогательные функции        ║
# ║    update_stream.py     — /api/update/stream (SSE)           ║
# ║    update_control.py    — check / schedule / apply / cancel  ║
# ║    update_status.py     — status / pre-status / result / log ║
# ║    update_changelog.py  — /api/changelog/sync               ║
# ╚═══════════════════════════════════════════════════════════════╝

from flask import Blueprint
from routes.update_stream    import update_stream_bp
from routes.update_control   import update_control_bp
from routes.update_status    import update_status_bp
from routes.update_changelog import update_changelog_bp

update_bp = Blueprint('update', __name__)

update_bp.register_blueprint(update_stream_bp)
update_bp.register_blueprint(update_control_bp)
update_bp.register_blueprint(update_status_bp)
update_bp.register_blueprint(update_changelog_bp)
