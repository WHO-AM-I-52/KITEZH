# ╔══════════════════════════════════════════════════════════════╗
# ║  tray.py                                                      ║
# ║  Иконка KITEZH в системном трее Windows.                     ║
# ║  Запускается из run_server.py если KITEZH_TRAY=1             ║
# ║  notify_error(title, msg) — печать в консоль + show_console  ║
# ║    + balloon трея (если запущен)                             ║
# ║  get_notify_level() — читает уровень из classifiers          ║
# ║  show_console() / hide_console() — публичные, вызываются   ║
# ║    из /api/console/* в admin_routes.py                       ║
# ║  pystray/PIL импортируются лениво — не падает               ║
# ║  если модуль не установлен (сервер без трея)              ║
# ╚══════════════════════════════════════════════════════════════╝

import os
import sys
import ctypes
import threading
import webbrowser

# pystray и PIL импортируются лениво в run_tray() —
# это позволяет импортировать tray в app.py без падения
# на серверах где pystray не установлен.

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(BASE_DIR, 'static', 'favicon.ico')
# Флаг перезапуска — то же имя, что ждёт run_server.py (sys.exit(42) → .bat).
RESTART_FLAG = os.path.join(BASE_DIR, '_restart.flag')
# Папка логов — совпадает с core/kitezh_logger.py (logs внутри core/).
LOGS_DIR = os.path.join(BASE_DIR, 'core', 'logs')

_console_visible = True
_tray_icon = None

# Флаг: pystray доступен (заполняется при первом вызове)
_pystray_available = None


def _check_pystray() -> bool:
    """True если pystray и PIL доступны."""
    global _pystray_available
    if _pystray_available is None:
        try:
            import pystray      # noqa: F401
            from PIL import Image  # noqa: F401
            _pystray_available = True
        except ImportError:
            _pystray_available = False
    return _pystray_available


# ─── УРОВЕНЬ УВЕДОМЛЕНИЙ ──────────────────────────────────────────────────────────────────────────────

def get_notify_level() -> str:
    """
    Читает уровень уведомлений из таблицы classifiers.
    Возвращает 'critical' или 'extended'.
    При любой ошибке — возвращает 'critical' (безопасно).
    """
    try:
        from db import get_db
        conn = get_db()
        row = conn.execute(
            "SELECT value FROM classifiers WHERE category=? LIMIT 1",
            ('tray_notify_level',)
        ).fetchone()
        conn.close()
        if row and row['value'] in ('critical', 'extended'):
            return row['value']
    except Exception:
        pass
    return 'critical'


def notify_error(title: str, message: str) -> None:
    """
    Сообщает админу об ошибке.

    Поведение (всегда, независимо от наличия трея):
      1. Печатает заголовок и текст ошибки в консоль (stderr).
      2. Показывает консольное окно через show_console() —
         чтобы скрытое окно появилось и админ увидел ошибку.
      3. Дополнительно, если трей доступен — показывает balloon-
         уведомление трея (как раньше).
    Все шаги обёрнуты в try/except — функция никогда не падает.
    """
    # 1. Печать в консоль — гарантированный канал.
    try:
        print(f"[ОШИБКА] {title}\n{message}", file=sys.stderr, flush=True)
    except Exception:
        pass
    # 2. Показать консоль (если она была скрыта).
    try:
        show_console()
    except Exception:
        pass
    # 3. Дополнительно — balloon трея, если трей запущен.
    if _tray_icon is not None:
        try:
            _tray_icon.notify(message, title)
        except Exception:
            pass


# ─── ПУБЛИЧНЫЕ ФУНКЦИИ КОНСОЛИ ─────────────────────────────────────────────────────────────────
# Данные функции вызываются из admin_routes.py (роуты /api/console/*)
# и из меню трея.

def get_console_visible() -> bool:
    """True если консоль сейчас видима."""
    return _console_visible


def show_console() -> bool:
    """
    Показывает консольное окно.
    Возвращает True если успешно, False если окно не найден
    (например запущен без консоли или не Windows).
    """
    global _console_visible
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            _console_visible = True
            if _tray_icon:
                _tray_icon.update_menu()
            return True
        return False
    except Exception:
        return False


def hide_console() -> bool:
    """
    Скрывает консольное окно.
    Возвращает True если успешно, False если окно не найден.
    """
    global _console_visible
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
            _console_visible = False
            if _tray_icon:
                _tray_icon.update_menu()
            return True
        return False
    except Exception:
        return False


# ─── ВНУТРЕННИЕ ФУНКЦИИ МЕНЮ ТРЕЯ ──────────────────────────────────────────────────────────
# Внутренние функции меню трея проксируют через публичные.

def _open_browser(icon, item):
    webbrowser.open('http://127.0.0.1:5000')


def _toggle_console(icon, item):
    if _console_visible:
        hide_console()
    else:
        show_console()


def _stop_server(icon, item):
    icon.stop()
    show_console()
    os._exit(0)


def _open_logs(icon, item):
    """Открывает папку логов (core/logs) в проводнике."""
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
    except Exception:
        pass
    try:
        # Windows: открывает папку в Проводнике.
        os.startfile(LOGS_DIR)  # type: ignore[attr-defined]
    except Exception:
        # Fallback для не-Windows / отсутствия startfile.
        try:
            webbrowser.open('file://' + LOGS_DIR)
        except Exception:
            pass


def _restart_server(icon, item):
    """Перезапускает сервер: создаёт _restart.flag и выходит.
    run_server.py обнаружит флаг после завершения процесса
    и сделает sys.exit(42) → .bat снова запустит сервер."""
    try:
        with open(RESTART_FLAG, 'w', encoding='utf-8') as f:
            f.write('tray')
    except Exception:
        pass
    try:
        icon.stop()
    except Exception:
        pass
    show_console()
    os._exit(0)


def _make_menu():
    import pystray
    return pystray.Menu(
        pystray.MenuItem('Открыть браузер', _open_browser, default=True),
        pystray.MenuItem(
            lambda item: 'Скрыть консоль' if _console_visible else 'Показать консоль',
            _toggle_console,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Открыть папку логов', _open_logs),
        pystray.MenuItem('Перезапустить сервер', _restart_server),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Остановить KITEZH', _stop_server),
    )


# ─── ЗАПУСК ─────────────────────────────────────────────────────────────────────────────────────

def run_tray(hide_on_start: bool = True):
    """Запускает иконку трея. Блокирует поток до остановки.
    Если pystray недоступен — выходит тихо."""
    global _tray_icon

    if not _check_pystray():
        print('[\u041fРЕДУПРЕЖДЕНИЕ] Трей недоступен: pystray или Pillow не установлены.')
        return

    import pystray
    from PIL import Image

    image = Image.open(ICON_PATH)
    _tray_icon = pystray.Icon(
        name='KITEZH',
        icon=image,
        title='KITEZH — сервер запущен',
        menu=_make_menu(),
    )

    if hide_on_start:
        def _delayed_hide():
            import time
            time.sleep(2)
            hide_console()
        threading.Thread(target=_delayed_hide, daemon=True).start()

    _tray_icon.run()


def start_tray_thread(hide_on_start: bool = True):
    """Запускает трей в отдельном потоке (non-blocking)."""
    t = threading.Thread(target=run_tray, args=(hide_on_start,), daemon=True)
    t.start()
    return t
