# ╔══════════════════════════════════════════════════════════════╗
# ║  run_server.py                                                ║
# ║  Вспомогательный запуск Flask через subprocess.         ║
# ║  Записывает PID дочернего процесса в _server.pid.       ║
# ║  Ждёт завершения, затем возвращает:               ║
# ║    sys.exit(42) — если _restart.flag существует         ║
# ║    sys.exit(0)  — обычная остановка                      ║
# ║  Батник читает код выхода и решает goto :start_server. ║
# ║  Если KITEZH_TRAY=1 — запускает иконку в системном трее.  ║
# ╚══════════════════════════════════════════════════════════════╝

import os
import sys
import subprocess
import signal

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
PID_FILE     = os.path.join(BASE_DIR, '_server.pid')
RESTART_FLAG = os.path.join(BASE_DIR, '_restart.flag')

PYTHON = sys.executable
app_py = os.path.join(BASE_DIR, 'app.py')

# ─── TRAY ────────────────────────────────────────────────────────────────────────────
TRAY_MODE = os.environ.get('KITEZH_TRAY', '0') == '1'

if TRAY_MODE:
    try:
        from tray import start_tray_thread
        start_tray_thread(hide_on_start=True)
        print("  Трей-режим: иконка KITEZH появится в системном трее")
    except ImportError as e:
        print(f"  [ПРЕДУПРЕЖДЕНИЕ] Трей недоступен: {e}")
        print("  Запуск в обычном режиме...")

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
    """Стражем Ctrl+C/SIGTERM в Flask-процесс."""
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

if os.path.exists(RESTART_FLAG):
    try:
        os.remove(RESTART_FLAG)
    except Exception:
        pass
    sys.exit(42)

sys.exit(0)
