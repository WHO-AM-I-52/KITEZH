# ╔══════════════════════════════════════════════════════════════╗
# ║ app.py                                                        ║
# ║ v3.0: quality_bp подключён                                   ║
# ║      fix #64 — ai_bp подключён                          ║
# ║      fix #61 — rate-limiting                                ║
# ║      fix #63 — _startup() вынесен из __main__           ║
# ╚═════════════════════════════════════════════════════════════╝

import os
from datetime import timedelta, datetime, date

from flask import Flask

from db import BASE_DIR, run_migrations
from migrations import init_db, migrate_db, migrate_districts
from context_processors import inject_globals

app = Flask(__name__)

# ─── SECRET_KEY ────────────────────────────────────────────────────────────────────────────────────
from limiter import limiter
import secrets as _secrets
_KEY_FILE = os.path.join(BASE_DIR, '_secret.key')
_env_key  = os.environ.get('SECRET_KEY')
if _env_key:
    app.secret_key = _env_key
else:
    if os.path.exists(_KEY_FILE):
        app.secret_key = open(_KEY_FILE, 'r').read().strip()
    else:
        _new_key = _secrets.token_hex(32)
        try:
            with open(_KEY_FILE, 'w') as _f:
                _f.write(_new_key)
        except Exception:
            pass
        app.secret_key = _new_key

# ─── Настройки сессий ───────────────────────────────────────────────────────────────────────────────────────
app.config['PERMANENT_SESSION_LIFETIME']   = timedelta(minutes=15)
app.config['SESSION_REFRESH_EACH_REQUEST'] = True

# ─── LIMITER ───────────────────────────────────────────────────────────────────────────────────────────
limiter.init_app(app)

# ─── JINJA ФИЛЬТРЫ ──────────────────────────────────────────────────────────────────────────────────────────
@app.template_filter('todatetime')
def todatetime_filter(value):
    """Преобразует 'YYYY-MM-DD' в datetime.date.
    Используется в view.html для вычисления deadline_diff.
    При ошибке парсинга — возвращает текущую дату (deadline_diff = 0)."""
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.strptime(str(value).strip()[:10], '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return date.today()


# ─── CONTEXT PROCESSOR ──────────────────────────────────────────────────────────────────────────────────────────
app.context_processor(inject_globals)

# ─── BLUEPRINTS ──────────────────────────────────────────────────────────────────────────────────────────
from phonebook_routes  import phonebook_bp
from search_routes     import search_bp
from login_routes      import auth_bp
from requests_app      import requests_bp
from admin_routes      import admin_bp
from export_routes     import report_bp
from info_routes       import misc_bp
from okved_admin       import okved_bp
from okved_api         import okved_api_bp
from egrul_api         import egrul_api_bp
from settings_routes   import settings_bp
from preview_routes    import preview_bp
from phonebook_import  import pb_import_bp
from investmap_routes  import investmap_bp
from api.requests_api  import api_bp
from ai_routes         import ai_bp
from quality_checks    import quality_bp
import backup_scheduler

for bp in [
    phonebook_bp, search_bp,
    okved_bp, okved_api_bp, egrul_api_bp,
    auth_bp, requests_bp, admin_bp,
    report_bp, misc_bp, settings_bp,
    preview_bp, pb_import_bp, investmap_bp,
    api_bp,
    ai_bp,
    quality_bp,
]:
    app.register_blueprint(bp)


# ─── ИНИЦИАЛИЗАЦИЯ БД И ПЛАНИРОВЩИКА ──────────────────────────────────────────────────
def _startup():
    init_db()
    migrate_db()
    migrate_districts()
    run_migrations()
    backup_scheduler.start()


_startup()


# ─── ТОЧКА ВХОДА ─────────────────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app_debug  = os.getenv('APP_DEBUG', '0')
    debug_flag = app_debug == '1'
    print(f"Starting Flask with debug={debug_flag}, FLASK_ENV={os.getenv('FLASK_ENV', '')}")
    app.run(host='0.0.0.0', port=5000, debug=debug_flag, use_reloader=debug_flag)
