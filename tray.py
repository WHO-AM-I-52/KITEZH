# ╔══════════════════════════════════════════════════════════════╗
# ║  tray.py                                                      ║
# ║  Иконка KITEZH в системном трее Windows.                     ║
# ║  Управление консолью выполняется только в процессе           ║
# ║  run_server.py, которому принадлежит консоль launcher-а.    ║
# ║  Flask app.py передаёт команды show/hide через файловый IPC. ║
# ╚══════════════════════════════════════════════════════════════╝

import os
import sys
import ctypes
import threading
import time
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(BASE_DIR, 'static', 'favicon.ico')
RESTART_FLAG = os.path.join(BASE_DIR, '_restart.flag')
TRAY_LOCK = os.path.join(BASE_DIR, '_tray_running.lock')
LOGS_DIR = os.path.join(BASE_DIR, 'core', 'logs')

CONSOLE_COMMAND = os.path.join(BASE_DIR, '_console.command')
CONSOLE_STATUS = os.path.join(BASE_DIR, '_console.status')

SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_RESTORE = 9

_console_visible = True
_tray_icon = None
_tray_ready = threading.Event()
_pystray_available = None
_tray_process_pid = None


def _check_pystray() -> bool:
    """True, если pystray и Pillow доступны."""
    global _pystray_available
    if _pystray_available is None:
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401
            _pystray_available = True
        except ImportError:
            _pystray_available = False
    return _pystray_available


def _atomic_write(path: str, text: str) -> bool:
    """Атомарно записывает небольшой IPC-файл."""
    tmp = f'{path}.{os.getpid()}.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def _get_console_hwnd() -> int:
    """
    Возвращает HWND стартового консольного окна KITEZH.

    run_server.py сохраняет HWND унаследованной от start KITEZH.bat
    консоли в KITEZH_CONSOLE_HWND. Fallback нужен для прямого запуска
    tray.py/run_server.py без батника.
    """
    if sys.platform != 'win32':
        return 0

    try:
        hwnd = int(os.environ.get('KITEZH_CONSOLE_HWND', '0') or 0)
        if hwnd and ctypes.windll.user32.IsWindow(hwnd):
            return hwnd
    except Exception:
        pass

    try:
        return int(ctypes.windll.kernel32.GetConsoleWindow() or 0)
    except Exception:
        return 0


def _window_is_visible(hwnd: int) -> bool:
    """Проверяет фактическую видимость окна Windows."""
    if not hwnd:
        return False

    try:
        return bool(ctypes.windll.user32.IsWindowVisible(hwnd))
    except Exception:
        return False

def _write_console_status(visible: bool, error: str = '') -> None:
    """Публикует фактический статус для Flask-процесса."""
    lines = [
        f'visible={1 if visible else 0}',
        f'pid={os.getpid()}',
        f'timestamp={time.time():.6f}',
    ]
    if error:
        lines.append(f'error={error.replace(chr(10), " ").replace(chr(13), " ")}')

    _atomic_write(CONSOLE_STATUS, '\n'.join(lines) + '\n')


def _read_console_status() -> tuple[bool, str]:
    """Читает последний статус, опубликованный процессом трея."""
    try:
        values = {}
        with open(CONSOLE_STATUS, 'r', encoding='utf-8') as f:
            for line in f:
                key, separator, value = line.partition('=')
                if separator:
                    values[key.strip()] = value.strip()

        if values.get('visible') == '1':
            return True, values.get('error', '')
        if values.get('visible') == '0':
            return False, values.get('error', '')
    except Exception:
        pass

    return _console_visible, ''


def _send_console_command(command: str) -> bool:
    """
    Передаёт команду процессу run_server.py.

    Используется из Flask app.py: у него нет собственного
    консольного HWND в tray-режиме.
    """
    if command not in ('show', 'hide'):
        return False

    if not os.path.exists(TRAY_LOCK):
        return False

    return _atomic_write(
        CONSOLE_COMMAND,
        f'command={command}\npid={os.getpid()}\ntimestamp={time.time():.6f}\n',
    )


# ─── УРОВЕНЬ УВЕДОМЛЕНИЙ ─────────────────────────────────────────────────────

def get_notify_level() -> str:
    """Читает уровень уведомлений из таблицы classifiers."""
    try:
        from db import get_db
        conn = get_db()
        row = conn.execute(
            'SELECT value FROM classifiers WHERE category=? LIMIT 1',
            ('tray_notify_level',),
        ).fetchone()
        conn.close()
        if row and row['value'] in ('critical', 'extended'):
            return row['value']
    except Exception:
        pass
    return 'critical'


def notify_error(title: str, message: str) -> None:
    """Печатает ошибку, открывает консоль и показывает уведомление трея."""
    try:
        print(f'[ОШИБКА] {title}\n{message}', file=sys.stderr, flush=True)
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


# ─── ПУБЛИЧНОЕ УПРАВЛЕНИЕ КОНСОЛЬЮ ───────────────────────────────────────────

def get_console_visible() -> bool:
    """
    Возвращает фактический статус консоли.

    app.py — дочерний Flask-процесс. В режиме tray он не управляет
    стартовым окном cmd.exe и всегда читает статус, который записывает
    процесс run_server.py с треем.
    """
    global _console_visible

    # Только процесс, где реально создан значок трея, имеет право
    # опрашивать HWND и публиковать состояние.
    if _tray_process_pid == os.getpid():
        hwnd = _get_console_hwnd()
        if hwnd:
            _console_visible = _window_is_visible(hwnd)
            _write_console_status(_console_visible)
            return _console_visible

    # app.py и все остальные процессы используют только IPC-статус.
    _console_visible, _ = _read_console_status()
    return _console_visible


def show_console() -> bool:
    """
    Показывает консоль.

    В run_server.py выполняет WinAPI-вызов.
    В app.py ставит команду в очередь для процесса run_server.py.
    """
    global _console_visible

    hwnd = _get_console_hwnd()
    if not hwnd:
        return _send_console_command('show')

    try:
        ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
        ctypes.windll.user32.ShowWindow(hwnd, SW_SHOWNORMAL)

        _console_visible = _window_is_visible(hwnd)
        _write_console_status(_console_visible)

        if _tray_icon is not None:
            _tray_icon.update_menu()

        return _console_visible
    except Exception as exc:
        _write_console_status(_console_visible, str(exc))
        return False


def hide_console() -> bool:
    """
    Скрывает консоль.

    В Flask-процессе создаёт IPC-команду. В процессе run_server.py
    вызывает SW_HIDE для унаследованного консольного окна.
    """
    global _console_visible

    tray_mode = os.environ.get('KITEZH_TRAY', '0') == '1'

    if tray_mode and not os.path.exists(TRAY_LOCK):
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if os.path.exists(TRAY_LOCK):
                break
            time.sleep(0.1)

        if not os.path.exists(TRAY_LOCK):
            message = (
                'Иконка трея не готова (_tray_running.lock отсутствует); '
                'скрытие консоли отменено.'
            )
            print(f'[ТРЕЙ] {message}', file=sys.stderr, flush=True)
            _write_console_status(get_console_visible(), message)
            return False

    hwnd = _get_console_hwnd()
    if not hwnd:
        return _send_console_command('hide')

    try:
        ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)

        _console_visible = _window_is_visible(hwnd)
        _write_console_status(_console_visible)

        if _tray_icon is not None:
            _tray_icon.update_menu()

        return not _console_visible
    except Exception as exc:
        _write_console_status(_console_visible, str(exc))
        return False


def _consume_console_command() -> str:
    """Читает и удаляет одну команду из Flask-процесса."""
    try:
        with open(CONSOLE_COMMAND, 'r', encoding='utf-8') as f:
            lines = f.readlines()

        try:
            os.remove(CONSOLE_COMMAND)
        except FileNotFoundError:
            pass

        for line in lines:
            key, separator, value = line.partition('=')
            if separator and key.strip() == 'command':
                command = value.strip().lower()
                if command in ('show', 'hide'):
                    return command
    except FileNotFoundError:
        pass
    except Exception as exc:
        _write_console_status(get_console_visible(), str(exc))

    return ''


def _watch_console_commands() -> None:
    """Выполняет команды Flask в процессе run_server.py с консольным HWND."""
    while True:
        command = _consume_console_command()

        if command == 'show':
            show_console()
        elif command == 'hide':
            hide_console()

        time.sleep(0.15)


# ─── ВНУТРЕННИЕ ФУНКЦИИ МЕНЮ ТРЕЯ ────────────────────────────────────────────

def _open_browser(icon, item):
    webbrowser.open('http://127.0.0.1:5000')


def _toggle_console(icon, item):
    if get_console_visible():
        hide_console()
    else:
        show_console()


def _stop_server(icon, item):
    icon.stop()
    show_console()
    os._exit(0)


def _open_logs(icon, item):
    """Открывает папку логов core/logs в Проводнике."""
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
    """Перезапускает сервер через _restart.flag."""
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
            lambda item: (
                'Скрыть консоль'
                if get_console_visible()
                else 'Показать консоль'
            ),
            _toggle_console,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Открыть папку логов', _open_logs),
        pystray.MenuItem('Перезапустить сервер', _restart_server),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem('Остановить KITEZH', _stop_server),
    )


# ─── ЗАПУСК ТРЕЯ ─────────────────────────────────────────────────────────────

def run_tray(hide_on_start: bool = True):
    """Запускает иконку трея и обработчик команд консоли."""
    global _tray_icon, _tray_process_pid

    _tray_process_pid = os.getpid()

    if not _check_pystray():
        print(
            '[ПРЕДУПРЕЖДЕНИЕ] Трей недоступен: pystray или Pillow не установлены.',
            file=sys.stderr,
            flush=True,
        )
        return

    import pystray
    from PIL import Image

    try:
        image = Image.open(ICON_PATH)
    except Exception as exc:
        print(
            f'[ТРЕЙ] Не удалось открыть иконку: {exc}',
            file=sys.stderr,
            flush=True,
        )
        return

    _tray_icon = pystray.Icon(
        name='KITEZH',
        icon=image,
        title='KITEZH — сервер запущен',
        menu=_make_menu(),
    )

    _write_console_status(get_console_visible())
    threading.Thread(target=_watch_console_commands, daemon=True).start()

    if hide_on_start:
        def _delayed_hide():
            ready = _tray_ready.wait(timeout=10)
            if ready:
                hide_console()
            else:
                print(
                    '[ТРЕЙ] _delayed_hide: timeout, скрытие отменено.',
                    file=sys.stderr,
                    flush=True,
                )

        threading.Thread(target=_delayed_hide, daemon=True).start()

    _tray_ready.set()
    _tray_icon.run()


def start_tray_thread(hide_on_start: bool = True):
    """Запускает трей в отдельном неблокирующем потоке."""
    thread = threading.Thread(
        target=run_tray,
        args=(hide_on_start,),
        daemon=True,
    )
    thread.start()
    return thread
