# ╔═══════════════════════════════════════════════════════════════╗
# ║  update_stream.py                                              ║
# ║  SSE-стрим прогресса скачивания и установки обновления.    ║
# ║  Маршрут: GET /api/update/stream                             ║
# ╚═══════════════════════════════════════════════════════════════╝

import json
import os
import subprocess
import sys
import time
import threading
from datetime import datetime

from flask import Blueprint, Response, request as flask_request, session, stream_with_context

from routes.update_helpers import (
    MAINTENANCE_FLAG, PRE_UPDATE_FILE, RESTART_FLAG, UPDATER,
    MAX_DELAY,
    clear_pre_update, pre_update_write, clear_maintenance,
    write_update_result, run_bat_restart, sse_format,
    BASE_DIR,
)

update_stream_bp = Blueprint('update_stream', __name__)


@update_stream_bp.route('/api/update/stream')
def api_update_stream():
    """SSE-стрим прогресса скачивания и установки обновления.

    Параметры запроса (GET):
      force=1       — принудительная перезапись всех файлов
      delay=N       — пауза (сек, 0–3600) между скачиванием и установкой

    События: download_pct | apply_pct | apply_file | done | error | heartbeat
    """
    if session.get('role') != 'admin':
        def _forbidden():
            yield sse_format('error', {'message': 'forbidden', 'phase': 'auth'})
        return Response(
            stream_with_context(_forbidden()),
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
        )

    force = flask_request.args.get('force') == '1'

    try:
        delay = int(flask_request.args.get('delay', 0))
        delay = max(0, min(MAX_DELAY, delay))
    except (ValueError, TypeError):
        delay = 0

    _scheduled_by = session.get('full_name', session.get('username', ''))
    _applied_by   = _scheduled_by

    # Сразу помечаем что идёт обновление (для не-админов)
    try:
        with open(PRE_UPDATE_FILE, 'w', encoding='utf-8') as _f:
            json.dump({
                'started_at': datetime.utcnow().isoformat(),
                'started_by': session.get('username', 'admin'),
            }, _f, ensure_ascii=False)
    except Exception:
        pass

    def _generate():
        pre_update_write({
            'phase':          'downloading',
            'scheduled_by':   _scheduled_by,
            'scheduled_at':   datetime.now().isoformat(),
            'fire_at_ts':     time.time(),
            'delay':          delay,
            'force':          force,
            'download_error': None,
        })

        # ── Фаза 1: скачивание ─────────────────────────────────────────────────────
        cmd_dl = [sys.executable, UPDATER, '--download-only', '--stream-json']
        if force:
            cmd_dl.append('--force')

        try:
            proc_dl = subprocess.Popen(
                cmd_dl,
                stdout=subprocess.PIPE,
                stderr=None,
                text=True,
                bufsize=1,
                cwd=BASE_DIR,
            )
        except Exception as e:
            clear_pre_update()
            yield sse_format('error', {'message': str(e), 'phase': 'download'})
            return

        last_heartbeat = time.time()

        for raw_line in proc_dl.stdout:
            if time.time() - last_heartbeat >= 15:
                yield sse_format('heartbeat', {})
                last_heartbeat = time.time()

            line = raw_line.strip()
            if not line:
                continue

            if line.startswith('{'):
                try:
                    msg = json.loads(line)
                    t = msg.get('type', '')
                    if t == 'download_pct':
                        yield sse_format('download_pct', {
                            'pct':           msg.get('pct', 0),
                            'downloaded_mb': msg.get('downloaded_mb', 0),
                            'total_mb':      msg.get('total_mb', 0),
                        })
                except json.JSONDecodeError:
                    pass

        proc_dl.wait()
        rc_dl = proc_dl.returncode

        if rc_dl != 0:
            pre_update_write({
                'phase':          'download_failed',
                'download_error': f'Ошибка скачивания (rc={rc_dl})',
            })
            time.sleep(5)
            clear_pre_update()
            yield sse_format('error', {
                'message': f'Ошибка скачивания (rc={rc_dl})',
                'phase': 'download',
            })
            return

        yield sse_format('download_pct', {'pct': 100, 'downloaded_mb': 0, 'total_mb': 0})

        if delay > 0:
            fire_at_ts = time.time() + delay
            pre_update_write({'phase': 'scheduled', 'fire_at_ts': fire_at_ts})
        else:
            pre_update_write({'phase': 'applying'})

        if delay > 0:
            yield sse_format('delay', {'seconds': delay})
            deadline = time.time() + delay
            while time.time() < deadline:
                remaining = int(deadline - time.time())
                if time.time() - last_heartbeat >= 15:
                    yield sse_format('heartbeat', {})
                    last_heartbeat = time.time()
                yield sse_format('delay_tick', {'remaining': remaining})
                time.sleep(1)

        pre_update_write({'phase': 'applying'})

        # Ставим флаг ТО — не-админы попадают на maintenance.html
        try:
            open(MAINTENANCE_FLAG, 'w').close()
        except Exception:
            pass

        # ── Фаза 2: установка ─────────────────────────────────────────────────────
        cmd_apply = [sys.executable, UPDATER, '--apply-only', '--stream-json']
        if force:
            cmd_apply.append('--force')

        try:
            proc_apply = subprocess.Popen(
                cmd_apply,
                stdout=subprocess.PIPE,
                stderr=None,
                text=True,
                bufsize=1,
                cwd=BASE_DIR,
            )
        except Exception as e:
            clear_pre_update()
            clear_maintenance()
            yield sse_format('error', {'message': str(e), 'phase': 'apply'})
            return

        last_heartbeat = time.time()
        done_received  = False
        apply_stats    = {}

        for raw_line in proc_apply.stdout:
            if time.time() - last_heartbeat >= 15:
                yield sse_format('heartbeat', {})
                last_heartbeat = time.time()

            line = raw_line.strip()
            if not line:
                continue

            if line.startswith('{'):
                try:
                    msg = json.loads(line)
                    t = msg.get('type', '')
                    if t == 'apply_pct':
                        yield sse_format('apply_pct', {
                            'pct':     msg.get('pct', 0),
                            'current': msg.get('current', 0),
                            'total':   msg.get('total', 0),
                        })
                    elif t == 'apply_file':
                        yield sse_format('apply_file', {
                            'status': msg.get('status', ''),
                            'path':   msg.get('path', ''),
                        })
                    elif t == 'done':
                        done_received = True
                        apply_stats = {
                            'updated':   msg.get('updated', 0),
                            'unchanged': msg.get('unchanged', 0),
                            'skipped':   msg.get('skipped', 0),
                            'errors':    msg.get('errors', 0),
                            'message':   msg.get('message', 'Готово'),
                        }
                        yield sse_format('done', apply_stats)
                except json.JSONDecodeError:
                    pass

        proc_apply.wait()
        rc_apply = proc_apply.returncode

        clear_pre_update()

        if rc_apply not in (0, 2):
            clear_maintenance()
            yield sse_format('error', {
                'message': f'Ошибка установки (rc={rc_apply})',
                'phase': 'apply',
            })
        elif not done_received:
            apply_stats = {
                'updated':   0,
                'unchanged': 0,
                'skipped':   0,
                'errors':    0,
                'message':   'Установка завершена (отчёт недоступен)',
            }
            yield sse_format('done', apply_stats)

        if rc_apply in (0, 2):
            write_update_result(apply_stats, applied_by=_applied_by)

            def _shutdown(rc):
                time.sleep(2)
                try:
                    open(RESTART_FLAG, 'w').close()
                except Exception:
                    pass
                # FIX v2.3.2: снимаем .maintenance ПЕРЕД выходом
                clear_maintenance()
                if rc == 2:
                    run_bat_restart()
                else:
                    os._exit(42)
            threading.Thread(target=_shutdown, args=(rc_apply,), daemon=False).start()

    return Response(
        stream_with_context(_generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control':     'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )
