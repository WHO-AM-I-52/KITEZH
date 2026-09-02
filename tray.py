# ╔══════════════════════════════════════════════════════════════╗
# ║  tray.py                                                      ║
# ║  Иконка KITEZH в системном трее Windows.                     ║
# ║  Запускается из run_server.py если KITEZH_TRAY=1             ║
# ║  notify_error(title, msg) — печать в консоль + show_console  ║
# ║    + balloon трея (если запущен)                             ║
# ║  get_notify_level() — читает уровень из classifiers          ║
# ║  show_console() / hide_console() — публичные, вызываются   ║
# ║    из /api/console/* в app.py                                ║
# ║  pystray/PIL импортируются лениво — не падает               ║
# ║    если модуль не установлен (сервер без трея)              ║
# ║  FIX: hide_console() проверяет _tray_running.lock на диске  ║
# ║    (межпроцессный сигнал), а не _tray_ready.is_set()        ║
# ║    (_tray_ready — in-process, не виден из app.py subprocess)║
# ╚══════════════════════════════════════════════════════════════╝

import os
import sys
import ctypes
import threading
import webbrowser

# pystray и PIL импортируются лениво в run_tray() —
# это позволяет импортировать tray в app.py без падения
# на серверах где pystray не установлен.

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
ICON_PATH     = os.path.join(BASE_DIR, 'static', 'favicon.ico')
RESTART_FLAG  = os.path.join(BASE_DIR, '_restart.flag')
TRAY_LOCK     = os.path.join(BASE_DIR, '_tray_running.lock')
LOGS_DIR      = os.path.join(BASE_DIR, 'core', 'logs')

_console_visible = True
_tray_icon = None

# Event: используется ТОЛЬКО внутри run_tray() для _delayed_hide.
# Не использовать как межпроцессный сигнал — app.py получает
# свой экземпляр Event, который никогда не будет set().
_tray_ready = threading.Event()

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


# ─── УРОВЕНЬ УВЕДОМЛЕНИЙ ─────────────────────────────────────────────────────

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
    try:
        print(f"[ОШИБКА] {title}\n{message}", file=sys.stderr, flush=True)
    except Exception:
        pass
    try:
        show_console()
    except Exception:
        pass
    if _tray_icon is not None:
        try:
            _tray_icon.notify(message, title)
        except Exception:
            pass


# ─── ПУБЛИЧНЫЕ ФУНКЦИИ КОНСОЛИ ───────────────────────────────────────────────
# Данные функции вызываются из app.py (роуты /api/console/*)
# и из меню трея.

def get_console_visible() -> bool:
    """True если консоль сейчас видима."""
    return _console_visible


def show_console() -> bool:
    """
    Показывает консольное окно.
    Возвращает True если успешно, False если окно не найдено.
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

    Проверяет наличие _tray_running.lock на диске — это
    межпроцессный сигнал от run_server.py что иконка трея
    реально запущена. Работает и из app.py (subprocess), и из
    run_tray() (тот же процесс что и run_server.py).

    Если KITEZH_TRAY=1, но лок не найден — скрытие отменяется:
    иначе консоль исчезнет без возможности вернуться.

    Возвращает True если успешно, False в противном случае.
    """
    global _console_visible

    tray_mode = os.environ.get('KITEZH_TRAY', '0') == '1'

    if tray_mode and not os.path.exists(TRAY_LOCK):
        # Ждём до 5 сек — лок может появиться чуть позже старта трея
        import time
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if os.path.exists(TRAY_LOCK):
                break
            time.sleep(0.1)
        if not os.path.exists(TRAY_LOCK):
            print('[ТРЕЙ] Иконка не готова (_tray_running.lock отсутствует) — '
                  'скрытие консоли отменено.',
                  file=sys.stderr, flush=True)
            return False

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


# ─── ВНУТРЕННИЕ ФУНКЦИИ МЕНЮ ТРЕЯ ────────────────────────────────────────────

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
        os.startfile(LOGS_DIR)  # type: ignore[attr-defined]
    except Exception:
        try:
            webbrowser.open('file://' + LOGS_DIR)
        except Exception:
            pass


def _restart_server(icon, item):
    """Перезапускает сервер: создаёт _restart.flag и выходит."""
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


# ─── ЗАПУСК ──────────────────────────────────────────────────────────────────

def run_tray(hide_on_start: bool = True):
    """Запускает иконку трея. Блокирует поток до остановки.
    Если pystray недоступен — выходит тихо."""
    global _tray_icon

    if not _check_pystray():
        print('[ПРЕДУПРЕЖДЕНИЕ] Трей недоступен: pystray или Pillow не установлены.',
              file=sys.stderr, flush=True)
        # _tray_ready не выставляем — _delayed_hide отменит скрытие.
        return

    import pystray
    from PIL import Image

    try:
        image = Image.open(ICON_PATH)
    except Exception as e:
        print(f'[ТРЕЙ] Не удалось открыть иконку: {e}', file=sys.stderr, flush=True)
        # _tray_ready не выставляем — _delayed_hide отменит скрытие.
        return

    _tray_icon = pystray.Icon(
        name='KITEZH',
        icon=image,
        title='KITEZH — сервер запущен',
        menu=_make_menu(),
    )

    if hide_on_start:
        def _delayed_hide():
            # Ждём _tray_ready — он выставляется сразу после этой функции,
            # до вызова .run(). Таймаут 10 сек — защита от зависания.
            ready = _tray_ready.wait(timeout=10)
            if ready:
                hide_console()
            else:
                print('[ТРЕЙ] _delayed_hide: timeout, скрытие отменено.',
                      file=sys.stderr, flush=True)
        threading.Thread(target=_delayed_hide, daemon=True).start()

    # Выставляем _tray_ready ДО .run() — только для _delayed_hide выше.
    # Для межпроцессной защиты используется _tray_running.lock,
    # который run_server.py уже создал до этого вызова.
    _tray_ready.set()

    _tray_icon.run()


def start_tray_thread(hide_on_start: bool = True):
    """Запускает трей в отдельном потоке (non-blocking)."""
    t = threading.Thread(target=run_tray, args=(hide_on_start,), daemon=True)
    t.start()
    return t
