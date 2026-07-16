# ╔══════════════════════════════════════════════════════════════════╗
# ║  requests_app/__init__.py                                        ║
# ║                                                                  ║
# ║  Blueprint «requests» — маршруты обращений KITEZH.              ║
# ║  Регистрируется в app.py через app.register_blueprint(bp).      ║
# ║                                                                  ║
# ║  Субмодули:                                                      ║
# ║    list_routes   — GET /requests, /requests/table               ║
# ║    form_routes   — создание и редактирование анкет              ║
# ║    view_routes   — просмотр карточки, история, откат            ║
# ║    action_routes — смена статусов, решения, файлы, соиспол.     ║
# ║    admin_routes  — подтверждение / отклонение, присвоение №     ║
# ║    misc_routes   — избранное, отдача файлов, редирект с /       ║
# ║                                                                  ║
# ║  url_prefix не задан намеренно: маршруты содержат полные пути.  ║
# ╚══════════════════════════════════════════════════════════════════╝

from flask import Blueprint

requests_bp = Blueprint('requests', __name__)

# Порядок импорта важен: misc последним (содержит catch-all редирект с /)
from . import list_routes, form_routes, view_routes, action_routes, admin_routes, misc_routes  # noqa: E402,F401
