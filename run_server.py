# ╔═══════════════════════════════════════════════════════════════╗
# ║ run_server.py                                                 ║
# ║ Вспомогательный запуск Flask через subprocess.              ║
# ║ Записывает PID дочернего процесса в _server.pid.            ║
# ║ Ждёт завершения, затем возвращает:                          ║
# ║   sys.exit(42) — если _restart.flag существует              ║
# ║   sys.exit(0)  — обычная остановка                          ║
# ║ Батник читает код выхода и решает goto :start_server.       ║
# ║ KITEZH_TRAY=1 всегда — иконка в трее во всех режимах.     ║
# ║ KITEZH_HIDE_CONSOLE=1 — скрыть консоль после старта            ║
# ║   (только в режиме 3 — польный трей).                       ║
# ║                                                              ║
# ║ FIX: при авторестарте после обновления (os._exit(42))       ║
# ║ батник делает goto :start_server без диалога режима,        ║
# ║ поэтому KITEZH_TRAY сохраняется из предыдущего запуска.    ║
# ║ Чтобы трей не дублировался — проверяем _tray_running.lock.  ║
# ║                                                              ║
# ║ FIX: в tray-режиме app.py запускается с CREATE_NO_WINDOW,  ║
# ║ чтобы окно консоли не появлялось повторно.                  ║
# ╚═══════════════════════════════════════════════════════════════╝

import os
import sys
import subprocess
import signal
import time

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
PID_FILE      = os.path.join(BASE_DIR, '_server.pid')
RESTART_FLAG  = os.path.join(BASE_DIR, '_restart.flag')
TRAY_LOCK     = os.path.join(BASE_DIR, '_tray_running.lock')

PYTHON = sys.executable
app_py = os.path.join(BASE_DIR, 'app.py')

# ─── TRAY ──────────────────────────────────────────────────────────────────────────────────────
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
            with open(TRAY_LOCK, 'w') as _f:
                _f.write(str(os.getpid()))
        except Exception:
            pass
        if HIDE_CONSOLE:
            print('  Tray-режим: консоль свернётся, иконка KITEZH появится в трее')
        else:
            print('  Иконка KITEZH появилась в системном трее')
    except ImportError as e:
        print(f'  [ПРЕДУПРЕЖДЕНИЕ] Трей недоступен: {e}')
        print('  Запуск без иконки трея...')
elif TRAY_MODE and os.path.exists(TRAY_LOCK):
    print('  Трей-режим: иконка уже запущена (авторестарт), повторный запуск пропущен.')

# ─── ЗАПУСК Flask ─────────────────────────────────────────────────────────────────────────────────
creation_flags = 0
if sys.platform == 'win32':
    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP
    if HIDE_CONSOLE:
        # В tray-режиме (консоль скрыта) — дочерний app.py
        # не должен открывать новое окно консоли.
        creation_flags |= subprocess.CREATE_NO_WINDOW

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

# Даём werkzeug время закрыть сокет и дописать последние access-логи.
time.sleep(1.5)

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
