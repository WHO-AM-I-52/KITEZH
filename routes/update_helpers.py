# ╔═══════════════════════════════════════════════════════════════╗
# ║  update_helpers.py                                            ║
# ║  Вспомогательные функции для модуля обновления KITEZH.        ║
# ║  Не содержит Flask-маршрутов — только утилиты,               ║
# ║  используемые update_stream, update_control, update_status.   ║
# ╚═══════════════════════════════════════════════════════════════╝

import json
import os
import subprocess
import sys
import time
import threading
from datetime import datetime

from db import BASE_DIR

# ─── Пути к служебным файлам ──────────────────────────────────────────────────
MAINTENANCE_FLAG    = os.path.join(BASE_DIR, '.maintenance')
FLAG_FILE           = os.path.join(BASE_DIR, '_update_available.json')
LOCK_FILE           = os.path.join(BASE_DIR, '_updating.lock')
RESTART_FLAG        = os.path.join(BASE_DIR, '_restart.flag')
UPDATER             = os.path.join(BASE_DIR, 'updater', '_updater.py')
SYNC_CHANGELOG      = os.path.join(BASE_DIR, 'updater', 'sync_changelog.py')
COMMIT_FILE         = os.path.join(BASE_DIR, '_last_commit.txt')
PRE_UPDATE_FILE     = os.path.join(BASE_DIR, '_pre_update.json')
UPDATE_RESULT_FILE  = os.path.join(BASE_DIR, '_update_result.json')
PUBLIC_LOG_FILE     = os.path.join(BASE_DIR, 'logs', '_update_public_log.json')
BAT_NAME            = 'start KITEZH.bat'

MIN_DELAY = 0
MAX_DELAY = 3600


# ─── Чтение локального SHA ────────────────────────────────────────────────────

def read_local_sha() -> str:
    if os.path.exists(COMMIT_FILE):
        try:
            return open(COMMIT_FILE, encoding='utf-8').read().strip()[:12]
        except Exception:
            pass
    return ''


# ─── Управление _pre_update.json ─────────────────────────────────────────────

def clear_pre_update():
    try:
        if os.path.exists(PRE_UPDATE_FILE):
            os.remove(PRE_UPDATE_FILE)
    except Exception:
        pass


def pre_update_write(patch: dict):
    """Создаёт или патчит _pre_update.json.
    Если файл уже есть — обновляет только переданные ключи.
    """
    try:
        data = {}
        if os.path.exists(PRE_UPDATE_FILE):
            with open(PRE_UPDATE_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        data.update(patch)
        with open(PRE_UPDATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


# ─── Управление .maintenance ─────────────────────────────────────────────────

def clear_maintenance():
    """Снимает флаг технического обслуживания (.maintenance).
    Вызывается после успешного применения обновления — перед рестартом
    и при старте сервера (на случай если предыдущий процесс упал не сняв флаг).
    """
    try:
        if os.path.exists(MAINTENANCE_FLAG):
            os.remove(MAINTENANCE_FLAG)
    except Exception:
        pass


# ─── Управление _update_result.json ──────────────────────────────────────────

def write_update_result(stats: dict, applied_by: str = ''):
    """Записывает итог применённого обновления в _update_result.json.
    Файл переживает рестарт сервера и читается один раз роутом
    /api/update/result — после чего удаляется (one-shot).
    """
    payload = {
        'ok':          stats.get('errors', 0) == 0,
        'updated':     stats.get('updated', 0),
        'unchanged':   stats.get('unchanged', 0),
        'skipped':     stats.get('skipped', 0),
        'errors':      stats.get('errors', 0),
        'message':     stats.get('message', ''),
        'finished_at': datetime.now().isoformat(),
        'applied_by':  applied_by,
    }
    try:
        with open(UPDATE_RESULT_FILE, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass


# ─── Управление _updating.lock ───────────────────────────────────────────────

def lock_write(phase: str):
    payload = {
        'pid':        os.getpid(),
        'started_at': datetime.now().isoformat(),
        'phase':      phase,
    }
    try:
        with open(LOCK_FILE, 'w', encoding='utf-8') as f:
            json.dump(payload, f)
    except Exception:
        pass


def lock_update_phase(phase: str):
    try:
        data = {}
        if os.path.exists(LOCK_FILE):
            with open(LOCK_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        data['phase'] = phase
        with open(LOCK_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except Exception:
        pass


def lock_is_stale() -> bool:
    if not os.path.exists(LOCK_FILE):
        return False
    try:
        with open(LOCK_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        pid = int(data.get('pid', 0))
        if pid <= 0:
            return False
        os.kill(pid, 0)
        return False
    except PermissionError:
        return False
    except ProcessLookupError:
        try:
            os.remove(LOCK_FILE)
        except Exception:
            pass
        return True
    except Exception:
        return False


def lock_clear():
    for path in (FLAG_FILE, LOCK_FILE):
        try:
            os.remove(path)
        except Exception:
            pass


# ─── Рестарт через .bat ───────────────────────────────────────────────────────

def run_bat_restart():
    """Снимает ТО, запускает новый .bat, завершает текущий процесс с кодом 42."""
    clear_maintenance()
    bat_path = os.path.join(BASE_DIR, BAT_NAME)
    try:
        subprocess.Popen(
            ['cmd', '/c', 'start', '', bat_path],
            cwd=BASE_DIR,
            shell=False,
        )
    except Exception:
        pass
    time.sleep(3)
    os._exit(42)


# ─── SSE-утилита ──────────────────────────────────────────────────────────────

def sse_format(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ─── Парсинг статистики apply (для schedule-флоу) ────────────────────────────

def parse_apply_stats(stdout: str) -> dict:
    import re
    stats = {'updated': 0, 'unchanged': 0, 'skipped': 0, 'errors': 0}
    patterns = {
        'updated':   r'Обновлено файлов\s*:\s*(\d+)',
        'unchanged': r'Без изменений\s*:\s*(\d+)',
        'skipped':   r'Пропущено[^:]*:\s*(\d+)',
        'errors':    r'Ошибок при записи\s*:\s*(\d+)',
    }
    for key, pat in patterns.items():
        m = re.search(pat, stdout or '')
        if m:
            try:
                stats[key] = int(m.group(1))
            except ValueError:
                pass
    return stats


# ─── Фоновый воркер: download → таймер → apply → рестарт ─────────────────────

def build_timer_worker(delay: int, force: bool, user_id: int, applied_by: str = ''):
    """Флоу: downloading → scheduled → applying → рестарт."""
    def _worker():
        lock_update_phase('downloading')
        cmd_dl = [sys.executable, UPDATER, '--download-only']
        if force:
            cmd_dl.append('--force')
        try:
            res = subprocess.run(cmd_dl, capture_output=True, text=True, timeout=300)
            rc_dl = res.returncode
            dl_output = (res.stdout + res.stderr)[-2000:]
        except subprocess.TimeoutExpired:
            rc_dl     = 1
            dl_output = 'timeout: скачивание превысило 300 сек'
        except Exception as e:
            rc_dl     = 1
            dl_output = str(e)

        if rc_dl != 0:
            try:
                with open(PRE_UPDATE_FILE, 'r', encoding='utf-8') as f:
                    pre = json.load(f)
            except Exception:
                pre = {}
            pre['download_error'] = dl_output
            pre['phase']          = 'download_failed'
            try:
                with open(PRE_UPDATE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(pre, f, ensure_ascii=False)
            except Exception:
                pass
            time.sleep(5)
            clear_pre_update()
            lock_clear()
            return

        fire_at_ts = time.time() + delay
        try:
            with open(PRE_UPDATE_FILE, 'r', encoding='utf-8') as f:
                pre = json.load(f)
        except Exception:
            pre = {}
        pre['phase']      = 'scheduled'
        pre['fire_at_ts'] = fire_at_ts
        try:
            with open(PRE_UPDATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(pre, f, ensure_ascii=False)
        except Exception:
            pass
        lock_update_phase('scheduled')

        while time.time() < fire_at_ts:
            if not os.path.exists(PRE_UPDATE_FILE):
                lock_clear()
                return
            time.sleep(1)

        if not os.path.exists(PRE_UPDATE_FILE):
            lock_clear()
            return

        clear_pre_update()
        lock_update_phase('applying')

        try:
            open(MAINTENANCE_FLAG, 'w').close()
        except Exception:
            pass

        cmd_apply = [sys.executable, UPDATER, '--apply-only']
        if force:
            cmd_apply.append('--force')
        apply_out = ''
        try:
            res_apply = subprocess.run(cmd_apply, capture_output=True, text=True, timeout=300)
            rc_apply  = res_apply.returncode
            apply_out = (res_apply.stdout or '') + (res_apply.stderr or '')
        except Exception:
            rc_apply = 1
        finally:
            lock_clear()
            # FIX v2.3.2: снимаем ТО в finally — гарантированно даже при краше
            clear_maintenance()

        if rc_apply in (0, 2):
            _stats = parse_apply_stats(apply_out)
            _stats['message'] = (
                f"Обновлено: {_stats['updated']} | "
                f"Без изменений: {_stats['unchanged']} | "
                f"Пропущено: {_stats['skipped']}"
                + (f" | Ошибок: {_stats['errors']}" if _stats['errors'] else "")
            )
            write_update_result(_stats, applied_by=applied_by)

        try:
            open(RESTART_FLAG, 'w').close()
        except Exception:
            pass

        if rc_apply == 2:
            run_bat_restart()
        else:
            os._exit(42)

    return _worker
