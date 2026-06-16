# ╔══════════════════════════════════════════════════════════════╗
# ║  run_server.py                                               ║
# ║  Вспомогательный запуск Flask через subprocess.              ║
# ║  Записывает PID дочернего процесса в _server.pid.            ║
# ║  Ждёт завершения, затем возвращает:                          ║
# ║    sys.exit(42) — если _restart.flag существует              ║
# ║    sys.exit(0)  — обычная остановка                          ║
# ║  Батник читает код выхода и решает goto :start_server.       ║
# ║  Если KITEZH_TRAY=1 — запускает иконку в системном трее.    ║
# ║                                                              ║
# ║  FIX: при авторестарте после обновления (os._exit(42))       ║
# ║  батник делает goto :start_server без диалога режима,        ║
# ║  поэтому KITEZH_TRAY сохраняется из предыдущего запуска.    ║
# ║  Чтобы трей не дублировался — проверяем _tray_running.lock.  ║
# ╚══════════════════════════════════════════════════════════════╝

import os
import sys
import subprocess
import signal

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
PID_FILE      = os.path.join(BASE_DIR, '_server.pid')
RESTART_FLAG  = os.path.join(BASE_DIR, '_restart.flag')
TRAY_LOCK     = os.path.join(BASE_DIR, '_tray_running.lock')

PYTHON = sys.executable
app_py = os.path.join(BASE_DIR, 'app.py')

# ─── TRAY ─────────────────────────────────────────────────────────────────────
TRAY_MODE = os.environ.get('KITEZH_TRAY', '0') == '1'

# FIX: чистим лок при каждом старте батника.
# Если предыдущий сеанс завершился нештатно — лок мог остаться,
# и трей не запустился бы, оставив консоль скрытой без возможности вернуть.
# При авторестарте (goto :start_server) дублирования не будет —
# старый процесс и его трей уже мертвы.
if TRAY_MODE:
    try:
        os.remove(TRAY_LOCK)
    except FileNotFoundError:
        pass

# Запускаем трей только если:
#   1. Выбран tray-режим (KITEZH_TRAY=1)
#   2. Трей ещё не запущен в этом процессе (нет _tray_running.lock)
# Это защищает от двойной иконки при авторестарте после обновления,
# т.к. батник делает goto :start_server снова вызывая run_server.py
# с теми же переменными окружения.

_tray_started = False

if TRAY_MODE and not os.path.exists(TRAY_LOCK):
    try:
        from tray import start_tray_thread
        start_tray_thread(hide_on_start=True)
        _tray_started = True
        # Создаём лок — следующий run_server.py в том же сеансе батника
        # увидит его и пропустит повторный старт трея
        try:
            with open(TRAY_LOCK, 'w') as _f:
                _f.write(str(os.getpid()))
        except Exception:
            pass
        print("  Трей-режим: иконка KITEZH появится в системном трее")
    except ImportError as e:
        print(f"  [ПРЕДУПРЕЖДЕНИЕ] Трей недоступен: {e}")
        print("  Запуск в обычном режиме...")
elif TRAY_MODE and os.path.exists(TRAY_LOCK):
    print("  Трей-режим: иконка уже запущена (авторестарт), повторный запуск пропущен.")

# ─── ЗАПУСК Flask ─────────────────────────────────────────────────────────────
creation_flags = 0
if sys.platform == 'win32':
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

proc = subprocess.Popen(
    [PYTHON, app_py],
    cwd=BASE_DIR,
    creationflags=creation_flags,
)

try:
    with open(PID_FILE, 'w') as f:
        f.write(str(proc.pid))
except Exception:
    pass


def _relay_signal(signum, frame):
    """Передаём Ctrl+C/SIGTERM в Flask-процесс."""
    try:
        if sys.platform == 'win32':
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(signal.SIGTERM)
    except Exception:
        pass


signal.signal(signal.SIGINT,  _relay_signal)
signal.signal(signal.SIGTERM, _relay_signal)

try:
    proc.wait()
except KeyboardInterrupt:
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()

try:
    os.remove(PID_FILE)
except Exception:
    pass

# При завершении сессии (не рестарт) — чистим лок трея,
# чтобы следующий ручной запуск батника снова показал иконку.
if not os.path.exists(RESTART_FLAG):
    try:
        os.remove(TRAY_LOCK)
    except Exception:
        pass

if os.path.exists(RESTART_FLAG):
    try:
        os.remove(RESTART_FLAG)
    except Exception:
        pass
    sys.exit(42)

sys.exit(0)
