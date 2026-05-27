# ╔══════════════════════════════════════════════════════════════╗
# ║  run_server.py                                                ║
# ║  Вспомогательный запуск Flask через subprocess.         ║
# ║  Записывает PID дочернего процесса в _server.pid.       ║
# ║  Ждёт завершения, затем возвращает:               ║
# ║    sys.exit(42) — если _restart.flag существует         ║
# ║    sys.exit(0)  — обычная остановка                      ║
# ║  Батник читает код выхода и решает goto :start_server. ║
# ╚══════════════════════════════════════════════════════════════╝

import os
import sys
import subprocess
import signal

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
PID_FILE     = os.path.join(BASE_DIR, '_server.pid')
RESTART_FLAG = os.path.join(BASE_DIR, '_restart.flag')

# Определяем путь к python.exe (тот же что использует батник)
PYTHON = sys.executable

app_py = os.path.join(BASE_DIR, 'app.py')

# Запуск Flask в отдельном процессе с изолированной консольной группой
# CREATE_NEW_PROCESS_GROUP = 0x00000200 — новая группа, Ctrl+C/Break не доходит до батника
creation_flags = 0
if sys.platform == 'win32':
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP

proc = subprocess.Popen(
    [PYTHON, app_py],
    cwd=BASE_DIR,
    creationflags=creation_flags,
)

# Сохраняем PID дочернего процесса
try:
    with open(PID_FILE, 'w') as f:
        f.write(str(proc.pid))
except Exception:
    pass

# Передаём Ctrl+C от батника в Flask-процесс
def _relay_signal(signum, frame):
    """Ctrl+C пользователя → передаём в Flask."""
    try:
        if sys.platform == 'win32':
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(signal.SIGTERM)
    except Exception:
        pass

signal.signal(signal.SIGINT,  _relay_signal)
signal.signal(signal.SIGTERM, _relay_signal)

# Ждём завершения Flask
try:
    proc.wait()
except KeyboardInterrupt:
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()

# Убираем PID-файл
try:
    os.remove(PID_FILE)
except Exception:
    pass

# Если _restart.flag существует — батник должен перезапуститься
if os.path.exists(RESTART_FLAG):
    try:
        os.remove(RESTART_FLAG)
    except Exception:
        pass
    sys.exit(42)   # код 42 — сигнал батнику: перезапустить

sys.exit(0)
