from flask import Blueprint

requests_bp = Blueprint('requests', __name__)

from . import list_routes, form_routes, view_routes, action_routes, admin_routes, misc_routes  # noqa: E402,F401
