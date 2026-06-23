# ╔═══════════════════════════════════════════════════════════════╗
# ║ app.py                                                        ║
# ║ v3.0: quality_bp подключён                                   ║
# ║      fix #64 — ai_bp подключён                          ║
# ║      fix #61 — rate-limiting                                ║
# ║      fix #63 — _startup() вынесен из __main__           ║
# ║      feat: maintenance mode (.maintenance флаг)              ║
# ║      feat: errorhandler(500) → tray.notify_error()          ║
# ║      feat: admin_sql_bp — SQL-консоль админа               ║
# ║      fix #15 — админ проходит сквозь режим ТО              ║
# ║      fix #15 — /login и /change-password доступны в ТО     ║
# ║      feat #15 — update_bp: планировщик обновлений          ║
# ║      feat: kitezh_logger — централизованный логгер         ║
# ║      feat: portal_analysis_bp — анализ заполняемости       ║
# ║      fix: threaded=True — SSE-стрим обновления работает     ║
# ╚═══════════════════════════════════════════════════════════════╝

import os
import traceback
import warnings
from datetime import timedelta, datetime, date

warnings.filterwarnings(
    'ignore',
    message='Workbook contains no default style',
    category=UserWarning,
    module='openpyxl',
)

from flask import Flask, jsonify, render_template, request as flask_request, session

from db import BASE_DIR, run_migrations
from migrations import init_db, migrate_db, migrate_districts
from core.context_processors import inject_globals
from core.kitezh_logger import err_logger

app = Flask(__name__)

# ─── MAINTENANCE FLAG ─────────────────────────────────────────────────────────────────────────────
_MAINTENANCE_FLAG = os.path.join(BASE_DIR, '.maintenance')

# ─── SECRET_KEY ─────────────────────────────────────────────────────────────────────────────
from core.limiter import limiter
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

# ─── Настройки сессий ───────────────────────────────────────────────────────────────────────────
app.config['PERMANENT_SESSION_LIFETIME']   = timedelta(minutes=15)
app.config['SESSION_REFRESH_EACH_REQUEST'] = True

# ─── LIMITER ────────────────────────────────────────────────────────────────────────────────────
limiter.init_app(app)

# ─── JINJA ФИЛЬТРЫ ───────────────────────────────────────────────────────────────────────────────
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


# ─── CONTEXT PROCESSOR ────────────────────────────────────────────────────────────────────────────
app.context_processor(inject_globals)

# ─── MAINTENANCE MODE ────────────────────────────────────────────────────────────────────────────
@app.route('/health')
def health():
    """Эндпойнт для проверки доступности сервера.
    Используется JS-пуллером на странице maintenance.html.
    Не требует авторизации, не блокируется лимитером."""
    return jsonify({'status': 'ok'})


@app.before_request
def check_maintenance():
    """Если .maintenance существует — отдаём страницу ТО для всех запросов.
    Исключения:
      role == admin  — админ всегда проходит (управляет режимом ТО)
      /health        — JS-пуллер на странице ТО
      /static/       — статика (CSS, JS, шрифты)
      /maintenance/  — роуты управления ТО (авторизация внутри)
      /ping          — хеартбит онлайн-присутствия
      /login         — точка входа (форма авторизации)
      /change-password — смена пароля при первом входе
    """
    if not os.path.exists(_MAINTENANCE_FLAG):
        return
    if session.get('role') == 'admin':
        return
    path = flask_request.path
    if (path == '/health'
            or path.startswith('/static/')
            or path.startswith('/maintenance/')
            or path == '/ping'
            or path == '/login'
            or path == '/change-password'):
        return
    return render_template('maintenance.html'), 503


# ─── BLUEPRINTS ────────────────────────────────────────────────────────────────────────────────────
from routes.phonebook_routes import phonebook_bp
from routes.search_routes    import search_bp
from routes.login_routes     import auth_bp
from requests_app            import requests_bp
from routes.admin_routes     import admin_bp
from routes.export_routes    import report_bp
from routes.info_routes      import misc_bp
from routes.update_routes    import update_bp
from routes.okved_admin      import okved_bp
from routes.okved_api        import okved_api_bp
from routes.egrul_api        import egrul_api_bp
from routes.settings_routes  import settings_bp
from routes.preview_routes   import preview_bp
from routes.phonebook_import import pb_import_bp
from routes.investmap_routes import investmap_bp
from api.requests_api        import api_bp
from routes.ai_routes        import ai_bp
from routes.quality_checks   import quality_bp
from routes.admin_sql_routes import admin_sql_bp
from portal_analysis.portal_analysis_routes import portal_analysis_bp
import services.backup_scheduler as backup_scheduler

for bp in [
    phonebook_bp, search_bp,
    okved_bp, okved_api_bp, egrul_api_bp,
    auth_bp, requests_bp, admin_bp,
    report_bp, misc_bp, update_bp, settings_bp,
    preview_bp, pb_import_bp, investmap_bp,
    api_bp,
    ai_bp,
    quality_bp,
    admin_sql_bp,
    portal_analysis_bp,
]:
    app.register_blueprint(bp)


# ─── ОБРАБОТЧИК ОШИБОК ────────────────────────────────────────────────────────────────────────────
@app.errorhandler(500)
def handle_500(exc):
    """
    Глобальный обработчик необработанных исключений Flask.
    Пишет traceback в logs/kitezh_errors.log.
    При Tray-режиме показывает Windows-уведомление.
    Уровень 'critical' — только 500 (по умолчанию).
    Уровень 'extended' — + полный traceback в сообщении.
    """
    _tb = traceback.format_exc()
    try:
        err_logger.error('HTTP 500  %s %s\n%s',
                         flask_request.method,
                         flask_request.path,
                         _tb)
    except Exception:
        pass
    try:
        from tray import notify_error, get_notify_level
        level = get_notify_level()
        if level == 'extended':
            msg = f"{flask_request.method} {flask_request.path}\n{_tb[-300:]}"
        else:
            msg = f"{flask_request.method} {flask_request.path} — {type(exc).__name__}"
        notify_error('⚠️ Ошибка KITEZH (500)', msg)
    except Exception:
        pass
    return render_template('500.html'), 500


# ─── ИНИЦИАЛИЗАЦИЯ БД И ПЛАНИРОВЩИКА ────────────────────────────────────────────────────────
def _startup():
    if os.path.exists(_MAINTENANCE_FLAG):
        try:
            os.remove(_MAINTENANCE_FLAG)
        except Exception:
            pass
    init_db()
    migrate_db()
    migrate_districts()
    run_migrations()
    backup_scheduler.start()


_startup()


# ─── ТОЧКА ВХОДА ──────────────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app_debug  = os.getenv('APP_DEBUG', '0')
    debug_flag = app_debug == '1'
    print(f"Starting Flask with debug={debug_flag}, threaded=True, FLASK_ENV={os.getenv('FLASK_ENV', '')}")
    app.run(host='0.0.0.0', port=5000, debug=debug_flag, use_reloader=debug_flag, threaded=True)
