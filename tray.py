# ╔══════════════════════════════════════════════════════════════╗
# ║  tray.py                                                      ║
# ║  Иконка KITEZH в системном трее Windows.                     ║
# ║  Запускается из run_server.py если KITEZH_TRAY=1             ║
# ╚══════════════════════════════════════════════════════════════╝

import os
import sys
import ctypes
import threading
import webbrowser

import pystray
from PIL import Image

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(BASE_DIR, 'static', 'favicon.ico')

_console_visible = True
_tray_icon = None


def _get_console_hwnd():
    return ctypes.windll.kernel32.GetConsoleWindow()


def _show_console():
    global _console_visible
    hwnd = _get_console_hwnd()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    _console_visible = True
    if _tray_icon:
        _tray_icon.update_menu()


def _hide_console():
    global _console_visible
    hwnd = _get_console_hwnd()
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    _console_visible = False
    if _tray_icon:
        _tray_icon.update_menu()


def _open_browser(icon, item):
    webbrowser.open('http://127.0.0.1:5000')


def _toggle_console(icon, item):
    if _console_visible:
        _hide_console()
    else:
        _show_console()


def _stop_server(icon, item):
    icon.stop()
    _show_console()
    os._exit(0)


def _make_menu():
    return pystray.Menu(
        pystray.MenuItem('Открыть браузер', _open_browser, default=True),
        pystray.MenuItem(
            lambda item: 'Скрыть консоль' if _console_visible else 'Показать консоль',
            _toggle_console,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Остановить KITEZH', _stop_server),
    )


def run_tray(hide_on_start: bool = True):
    """Запускает иконку трея. Блокирует поток до остановки."""
    global _tray_icon

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
            _hide_console()
        threading.Thread(target=_delayed_hide, daemon=True).start()

    _tray_icon.run()


def start_tray_thread(hide_on_start: bool = True):
    """Запускает трей в отдельном потоке (non-blocking)."""
    t = threading.Thread(target=run_tray, args=(hide_on_start,), daemon=True)
    t.start()
    return t
