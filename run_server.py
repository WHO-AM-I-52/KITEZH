# ╔═══════════════════════════════════════════════════════════════╗
# ║ run_server.py                                                 ║
# ║ Вспомогательный запуск Flask через subprocess.               ║
# ║ Записывает PID дочернего процесса в _server.pid.             ║
# ║ Перед запуском безопасно завершает ранее оставленный app.py.  ║
# ║ Ждёт завершения, затем возвращает:                           ║
# ║   sys.exit(42) — если _restart.flag существует               ║
# ║   sys.exit(0)  — обычная остановка                           ║
# ║ Батник читает код выхода и решает goto :start_server.        ║
# ║ KITEZH_TRAY=1 всегда — иконка в трее во всех режимах.        ║
# ║ KITEZH_HIDE_CONSOLE=1 — скрыть консоль после старта          ║
# ║   (только в режиме 3 — полный трей).                         ║
# ╚═══════════════════════════════════════════════════════════════╝

import os
import sys
import subprocess
import signal
import time
import threading

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
PID_FILE      = os.path.join(BASE_DIR, '_server.pid')
RESTART_FLAG  = os.path.join(BASE_DIR, '_restart.flag')
TRAY_LOCK     = os.path.join(BASE_DIR, '_tray_running.lock')

PYTHON = sys.executable
app_py = os.path.join(BASE_DIR, 'app.py')


# ─── ПРОВЕРКА И ОЧИСТКА СТАРОГО СЕРВЕРА ─────────────────────────────────────
def _normalise_path(value):
    """Сравнивает Windows-пути без учёта регистра, кавычек и слешей."""
    if not value:
        return ''
    return os.path.normcase(
        os.path.normpath(str(value).strip().strip('"'))
    )


def _read_pid_file():
    """Возвращает PID из _server.pid либо None, если файл пустой/повреждён."""
    try:
        with open(PID_FILE, 'r', encoding='utf-8') as file:
            value = file.read().strip()
        pid = int(value)
        return pid if pid > 0 else None
    except (FileNotFoundError, ValueError, OSError):
        return None


def _remove_pid_file():
    try:
        os.remove(PID_FILE)
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f'[WARN] Не удалось удалить {PID_FILE}: {exc}', flush=True)


def _get_windows_process(pid):
    """
    Возвращает сведения Win32_Process по PID.

    CommandLine критичен: старый процесс допустимо закрывать
    только если он подтверждён как app.py из этой копии KITEZH.
    """
    script = (
        '$p = Get-CimInstance Win32_Process '
        f'-Filter "ProcessId = {pid}" -ErrorAction SilentlyContinue; '
        'if ($null -ne $p) { '
        '$p | Select-Object ProcessId,ParentProcessId,Name,CommandLine '
        '| ConvertTo-Json -Compress '
        '}'
    )
    try:
        result = subprocess.run(
            [
                'powershell',
                '-NoProfile',
                '-NonInteractive',
                '-ExecutionPolicy',
                'Bypass',
                '-Command',
                script,
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f'[WARN] Не удалось проверить PID {pid} через PowerShell: {exc}', flush=True)
        return None

    if result.returncode != 0:
        message = (result.stderr or result.stdout or 'неизвестная ошибка').strip()
        print(f'[WARN] PowerShell не проверил PID {pid}: {message}', flush=True)
        return None

    output = result.stdout.strip()
    if not output:
        return None

    try:
        import json
        process = json.loads(output)
    except (ValueError, TypeError) as exc:
        print(f'[WARN] Не удалось разобрать сведения PID {pid}: {exc}', flush=True)
        return None

    if not isinstance(process, dict):
        return None
    return process


def _is_current_kitezh_app(process_info):
    """
    Проверяет, что PID принадлежит Python, запущенному с текущим app.py.

    Сравнение по абсолютному нормализованному пути не позволяет закрыть
    произвольный python.exe или другую копию KITEZH.
    """
    if not process_info:
        return False

    name = str(process_info.get('Name') or '').casefold()
    command_line = str(process_info.get('CommandLine') or '')
    expected_app = _normalise_path(app_py)
    normalised_command = _normalise_path(command_line)

    return (
        name in {'python.exe', 'pythonw.exe'}
        and bool(expected_app)
        and expected_app in normalised_command
    )


def _pid_is_alive(pid):
    """Проверяет существование PID без исключений для обычного отсутствия."""
    if sys.platform == 'win32':
        try:
            result = subprocess.run(
                ['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return True

        output = result.stdout.casefold()
        return (
            result.returncode == 0
            and 'no tasks are running' not in output
            and str(pid) in output
        )

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    else:
        return True


def _wait_for_exit(pid, timeout_seconds=5):
    """Ожидает завершения PID не более указанного времени."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _pid_is_alive(pid):
            return True
        time.sleep(0.25)
    return not _pid_is_alive(pid)


def _taskkill(pid, force=False):
    """Закрывает подтверждённое дерево процесса Windows."""
    command = ['taskkill', '/PID', str(pid), '/T']
    if force:
        command.append('/F')

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f'[WARN] Не удалось выполнить {" ".join(command)}: {exc}', flush=True)
        return False

    if result.returncode != 0:
        message = (result.stderr or result.stdout or 'неизвестная ошибка').strip()
        print(f'[WARN] taskkill для PID {pid} завершился с ошибкой: {message}', flush=True)
        return False

    return True


def _cleanup_stale_server():
    """
    До запуска нового app.py обрабатывает PID из предыдущего сеанса.

    Возвращает True, если запуск нового сервера безопасен.
    Возвращает False, если PID выглядит чужим/непроверяемым либо не завершился.
    """
    old_pid = _read_pid_file()
    if old_pid is None:
        return True

    if not _pid_is_alive(old_pid):
        print(f'[INFO] Удалён устаревший PID-файл: процесс {old_pid} уже не запущен.', flush=True)
        _remove_pid_file()
        return True

    if sys.platform != 'win32':
        print(
            f'[ERROR] Найден активный PID {old_pid} из {PID_FILE}, '
            'но безопасная проверка command line реализована только для Windows. '
            'Новый сервер не запущен.',
            flush=True,
        )
        return False

    process_info = _get_windows_process(old_pid)
    if not _is_current_kitezh_app(process_info):
        name = (process_info or {}).get('Name', 'не удалось определить')
        parent = (process_info or {}).get('ParentProcessId', 'не удалось определить')
        command_line = (process_info or {}).get('CommandLine', 'не удалось определить')
        print(
            '[ERROR] Найден активный PID из _server.pid, но он не подтверждён '
            'как текущий KITEZH app.py. Автоматическое завершение отменено.',
            flush=True,
        )
        print(f'        PID: {old_pid}', flush=True)
        print(f'        Name: {name}', flush=True)
        print(f'        ParentProcessId: {parent}', flush=True)
        print(f'        CommandLine: {command_line}', flush=True)
        print('        Закройте процесс вручную, затем запустите KITEZH снова.', flush=True)
        return False

    command_line = process_info.get('CommandLine', '')
    parent = process_info.get('ParentProcessId', '')
    print(
        f'[INFO] Найден прежний KITEZH app.py: PID {old_pid}; '
        f'ParentProcessId={parent}.',
        flush=True,
    )
    print(f'       CommandLine: {command_line}', flush=True)
    print('[INFO] Запрашиваю штатное завершение дерева процесса...', flush=True)

    _taskkill(old_pid, force=False)
    if _wait_for_exit(old_pid, timeout_seconds=5):
        print(f'[INFO] Прежний KITEZH app.py (PID {old_pid}) завершён штатно.', flush=True)
        _remove_pid_file()
        return True

    print(
        f'[WARN] PID {old_pid} не завершился за 5 секунд. '
        'Применяю taskkill /F /T к подтверждённому KITEZH-процессу.',
        flush=True,
    )
    _taskkill(old_pid, force=True)

    if _wait_for_exit(old_pid, timeout_seconds=5):
        print(f'[INFO] Прежний KITEZH app.py (PID {old_pid}) завершён принудительно.', flush=True)
        _remove_pid_file()
        return True

    print(
        f'[ERROR] PID {old_pid} всё ещё активен после taskkill /F /T. '
        'Новый сервер не запущен, чтобы не создавать конфликт на порту 5000.',
        flush=True,
    )
    return False


if not _cleanup_stale_server():
    sys.exit(1)


# ─── TRAY ─────────────────────────────────────────────────────────────────────
# KITEZH_TRAY=1 во всех режимах (батник выставляет всегда).
# KITEZH_HIDE_CONSOLE=1 — только в режиме 3 (полный трей).
TRAY_MODE    = os.environ.get('KITEZH_TRAY', '0') == '1'
HIDE_CONSOLE = os.environ.get('KITEZH_HIDE_CONSOLE', '0') == '1'

# Чистим лок при каждом старте — защита от зависшего лока
# после нештатного завершения предыдущего сеанса.
if TRAY_MODE:
    try:
        os.remove(TRAY_LOCK)
    except FileNotFoundError:
        pass

# Запускаем трей если:
#   1. KITEZH_TRAY=1 (tray-режим)
#   2. Трей ещё не запущен в этом процессе (нет _tray_running.lock)
# HIDE_CONSOLE передаётся в hide_on_start — иконка есть всегда,
# а скрытие консоли — только в режиме 3.

_tray_started = False

if TRAY_MODE and not os.path.exists(TRAY_LOCK):
    try:
        from tray import start_tray_thread
        start_tray_thread(hide_on_start=HIDE_CONSOLE)
        _tray_started = True
        try:
            with open(TRAY_LOCK, 'w') as file:
                file.write(str(os.getpid()))
        except Exception:
            pass
        if HIDE_CONSOLE:
            print('  Tray-режим: консоль свернётся, иконка KITEZH появится в трее')
        else:
            print('  Иконка KITEZH появилась в системном трее')
    except ImportError as exc:
        print(f'  [ПРЕДУПРЕЖДЕНИЕ] Трей недоступен: {exc}')
        print('  Запуск без иконки трея...')
elif TRAY_MODE and os.path.exists(TRAY_LOCK):
    print('  Трей-режим: иконка уже запущена (авторестарт), повторный запуск пропущен.')

# ─── ЗАПУСК Flask ─────────────────────────────────────────────────────────────
creation_flags = 0
if sys.platform == 'win32':
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
    if HIDE_CONSOLE:
        # В tray-режиме (консоль скрыта) — дочерний app.py
        # не должен открывать новое окно консоли.
        creation_flags |= subprocess.CREATE_NO_WINDOW

print(
    f'[LAUNCH] APP_DEBUG={os.environ.get("APP_DEBUG")!r}; '
    f'FLASK_ENV={os.environ.get("FLASK_ENV")!r}; '
    f'KITEZH_HIDE_CONSOLE={os.environ.get("KITEZH_HIDE_CONSOLE")!r}',
    flush=True,
)

popen_kwargs = {
    'cwd': BASE_DIR,
    'creationflags': creation_flags,
}

if HIDE_CONSOLE:
    popen_kwargs.update(
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding='utf-8',
        errors='replace',
        bufsize=1,
    )
else:
    popen_kwargs.update(
        stdin=sys.stdin,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

proc = subprocess.Popen(
    [PYTHON, app_py],
    **popen_kwargs,
)


def _relay_stream(stream, target):
    """Передаёт построчный вывод дочернего app.py в консоль launcher-а."""
    try:
        for line in iter(stream.readline, ''):
            target.write(line)
            target.flush()
    except Exception:
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


if HIDE_CONSOLE:
    threading.Thread(
        target=_relay_stream,
        args=(proc.stdout, sys.stdout),
        daemon=True,
    ).start()

    threading.Thread(
        target=_relay_stream,
        args=(proc.stderr, sys.stderr),
        daemon=True,
    ).start()

try:
    with open(PID_FILE, 'w', encoding='utf-8') as file:
        file.write(str(proc.pid))
except OSError as exc:
    print(f'[WARN] Не удалось записать PID-файл: {exc}', flush=True)


def _relay_signal(signum, frame):
    """Передаёт Ctrl+C/SIGTERM в Flask-процесс."""
    try:
        if sys.platform == 'win32':
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(signal.SIGTERM)
    except Exception:
        pass


signal.signal(signal.SIGINT, _relay_signal)
signal.signal(signal.SIGTERM, _relay_signal)

try:
    proc.wait()
except KeyboardInterrupt:
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
finally:
    # PID-файл принадлежит только этому launcher-у: удаляем его,
    # если соответствующий дочерний процесс уже остановлен.
    if not _pid_is_alive(proc.pid):
        _remove_pid_file()

# Даём серверу время закрыть сокет и дописать последние access-логи.
time.sleep(1.5)

# При завершении сессии (не рестарт) — чистим лок трея,
# чтобы следующий ручной запуск батника снова показал иконку.
if not os.path.exists(RESTART_FLAG):
    try:
        os.remove(TRAY_LOCK)
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f'[WARN] Не удалось удалить tray-lock: {exc}', flush=True)

if os.path.exists(RESTART_FLAG):
    try:
        os.remove(RESTART_FLAG)
    except OSError as exc:
        print(f'[WARN] Не удалось удалить restart-флаг: {exc}', flush=True)
    sys.exit(42)

sys.exit(0)
