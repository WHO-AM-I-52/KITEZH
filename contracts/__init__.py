# ╔════════════════════════════════════════════════════════════════════════╗
# ║                          contracts/__init__.py                            ║
# ║  Раздел «Договоры и услуги». Версия 1: ядро расчётов и workflow.        ║
# ╚══════════════════════════════════════════════════════════════════════╝

from flask import Blueprint

contracts_bp = Blueprint(
    'contracts', __name__,
    url_prefix='/contracts',
    template_folder='templates',
)

from . import workflow, pricing, workdays  # noqa: E402,F401
from . import repository, documents  # noqa: E402,F401
from . import routes  # noqa: E402,F401
