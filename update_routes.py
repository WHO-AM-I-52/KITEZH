# ╔═══════════════════════════════════════════════════════════════╗
# ║                     update_routes.py                         ║
# ║  Blueprint обновлений KITEZH через GitHub                    ║
# ║  v1.0.0: перенос из info_routes.py                          ║
# ║  v1.1.0: /api/update/schedule — запланированное обновление   ║
# ║  v1.1.1: pre-status — fire_at_ts для точного отсчёта             ║
# ║  v1.2.0: /api/update/apply-force                              ║
# ║  v2.0.0: PID-валидация лока; download-first флоу;         ║
# ║           delay из запроса; rc=2 → запуск .bat;             ║
# ║           pre-status отдаёт phase + download_error              ║
# ║  v2.0.1: алиас /api/update-status → /api/update/status       ║
# ║  v2.1.0: /api/update/stream — SSE-стрим прогресса            ║
# ║           (% скачивания + % установки + итоговый отчёт)      ║
# ║  v2.2.0: /api/update/stream принимает ?delay=N               ║
# ║           (пауза между download и apply); кнопка «Обновить»  ║
# ║           теперь идёт напрямую через SSE, минуя schedule      ║
# ║  v2.2.1: _MIN_DELAY 1→0; delay=0 разрешён                   ║
# ╚═══════════════════════════════════════════════════════════════╝

from flask import Blueprint, jsonify, request as flask_request, session, Response, stream_with_context
from db import BASE_DIR
from activity_log import log_action
from db import get_db
from datetime import datetime
import os
import sys
import subprocess
import json
import threading
import time

update_bp = Blueprint('update', __name__)

_MAINTENANCE_FLAG = os.path.join(BASE_DIR, '.maintenance')
_FLAG_FILE        = os.path.join(BASE_DIR, '_update_available.json')
_LOCK_FILE        = os.path.join(BASE_DIR, '_updating.lock')
_RESTART_FLAG     = os.path.join(BASE_DIR, '_restart.flag')
_UPDATER          = os.path.join(BASE_DIR, '_updater.py')
_COMMIT_FILE      = os.path.join(BASE_DIR, '_last_commit.txt')
_PRE_UPDATE_FILE  = os.path.join(BASE_DIR, '_pre_update.json')
_BAT_NAME         = 'start KITEZH.bat'

_MIN_DELAY = 0
_MAX_DELAY = 3600


# ─── Вспомогательные ────────────────────────────────────────────────────────────────────────────────────────

def _read_local_sha() -> str:
    if os.path.exists(_COMMIT_FILE):
        try:
            return open(_COMMIT_FILE, encoding='utf-8').read().strip()[:12]
        except Exception:
            pass
    return ''


def _clear_pre_update():
    try:
        if os.path.exists(_PRE_UPDATE_FILE):
            os.remove(_PRE_UPDATE_FILE)
    except Exception:
        pass


def _lock_write(phase: str):
    """Записывает JSON-лок с PID текущего процесса и фазой.
    Позволяет _lock_is_stale() инвалидировать лок аварийно упавшего процесса.
    """
    payload = {
        'pid':        os.getpid(),
        'started_at': datetime.now().isoformat(),
        'phase':      phase,
    }
    try:
        with open(_LOCK_FILE, 'w', encoding='utf-8') as f:
            json.dump(payload, f)
    except Exception:
        pass


def _lock_update_phase(phase: str):
    """Обновляет только поле phase в существующем локе."""
    try:
        data = {}
        if os.path.exists(_LOCK_FILE):
            with open(_LOCK_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        data['phase'] = phase
        with open(_LOCK_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f)
    except Exception:
        pass


def _lock_is_stale() -> bool:
    """Возвращает True если лок существует, но PID уже мёртв.
    В этом случае автоматически удаляет лок-файл.
    """
    if not os.path.exists(_LOCK_FILE):
        return False
    try:
        with open(_LOCK_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        pid = int(data.get('pid', 0))
        if pid <= 0:
            return False
        os.kill(pid, 0)   # если процесс жив — исключения не будет
        return False      # процесс жив — лок активен
    except (ProcessLookupError, PermissionError):
        # ProcessLookupError: PID не существует — лок устарел
        # PermissionError (на Windows для чужого PID): считаем процесс живым
        if isinstance(locals().get('e') or Exception(), PermissionError):
            return False
        try:
            os.remove(_LOCK_FILE)
        except Exception:
            pass
        return True
    except Exception:
        return False


def _lock_clear():
    for path in (_FLAG_FILE, _LOCK_FILE):
        try:
            os.remove(path)
        except Exception:
            pass


def _run_bat_restart():
    """Запускает 'start KITEZH.bat' в отдельном окне (Windows),
    затем через 10 сек закрывает текущий процесс.
    """
    bat_path = os.path.join(BASE_DIR, _BAT_NAME)
    try:
        subprocess.Popen(
            ['cmd', '/c', 'start', '', bat_path],
            cwd=BASE_DIR,
            shell=False,
        )
    except Exception:
        pass
    time.sleep(10)
    os._exit(0)


# ─── SSE-утилита ──────────────────────────────────────────────────────────────────────────────────────────────

def _sse_format(event: str, data: dict) -> str:
    """Формирует одно SSE-сообщение.
    Формат:
        event: <event_name>
        data: <json>
        (пустая строка)
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ─── SSE-стрим прогресса обновления ──────────────────────────────────────────────────────────────────────────

@update_bp.route('/api/update/stream')
def api_update_stream():
    """SSE-стрим прогресса скачивания и установки обновления.

    Параметры запроса (GET):
      force=1       — принудительная перезапись всех файлов
      delay=N       — пауза (сек, 0–3600) между скачиванием и установкой
                      (позволяет кнопке «Обновить» заменить /api/update/schedule)

    События (event: download_pct | apply_pct | apply_file | done | error | heartbeat):

      download_pct  {pct: 0-100, downloaded_mb: float, total_mb: float}
      apply_pct     {pct: 0-100, current: int, total: int}
      apply_file    {status: 'updated'|'unchanged'|'skipped', path: str}
      done          {updated: int, unchanged: int, skipped: int, errors: int, message: str}
      error         {message: str, phase: 'download'|'apply'|'delay'}
      heartbeat     {} — каждые 15 сек пока ждём subprocess или delay
    """
    if session.get('role') != 'admin':
        def _forbidden():
            yield _sse_format('error', {'message': 'forbidden', 'phase': 'auth'})
        return Response(stream_with_context(_forbidden()),
                        mimetype='text/event-stream',
                        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    force = flask_request.args.get('force') == '1'

    # Параметр delay: пауза между скачиванием и установкой (сек)
    try:
        delay = int(flask_request.args.get('delay', 0))
        delay = max(0, min(_MAX_DELAY, delay))
    except (ValueError, TypeError):
        delay = 0

    def _generate():
        # ── Фаза 1: скачивание ──────────────────────────────────
        cmd_dl = [sys.executable, _UPDATER, '--download-only', '--stream-json']
        if force:
            cmd_dl.append('--force')

        try:
            proc_dl = subprocess.Popen(
                cmd_dl,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=BASE_DIR,
            )
        except Exception as e:
            yield _sse_format('error', {'message': str(e), 'phase': 'download'})
            return

        last_heartbeat = time.time()

        for raw_line in proc_dl.stdout:
            if time.time() - last_heartbeat >= 15:
                yield _sse_format('heartbeat', {})
                last_heartbeat = time.time()

            line = raw_line.strip()
            if not line:
                continue

            if line.startswith('{'):
                try:
                    msg = json.loads(line)
                    t = msg.get('type', '')
                    if t == 'download_pct':
                        yield _sse_format('download_pct', {
                            'pct':           msg.get('pct', 0),
                            'downloaded_mb': msg.get('downloaded_mb', 0),
                            'total_mb':      msg.get('total_mb', 0),
                        })
                except json.JSONDecodeError:
                    pass

        proc_dl.wait()
        rc_dl = proc_dl.returncode

        if rc_dl != 0:
            yield _sse_format('error', {
                'message': f'Ошибка скачивания (rc={rc_dl})',
                'phase': 'download',
            })
            return

        # Гарантированный 100% после завершения скачивания
        yield _sse_format('download_pct', {'pct': 100, 'downloaded_mb': 0, 'total_mb': 0})

        # ── Фаза 1.5: задержка (delay сек) перед установкой ────
        if delay > 0:
            yield _sse_format('delay', {'seconds': delay})
            deadline = time.time() + delay
            while time.time() < deadline:
                remaining = int(deadline - time.time())
                if time.time() - last_heartbeat >= 15:
                    yield _sse_format('heartbeat', {})
                    last_heartbeat = time.time()
                yield _sse_format('delay_tick', {'remaining': remaining})
                time.sleep(1)

        # ── Фаза 2: установка ───────────────────────────────────
        cmd_apply = [sys.executable, _UPDATER, '--apply-only', '--stream-json']
        if force:
            cmd_apply.append('--force')

        try:
            proc_apply = subprocess.Popen(
                cmd_apply,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=BASE_DIR,
            )
        except Exception as e:
            yield _sse_format('error', {'message': str(e), 'phase': 'apply'})
            return

        last_heartbeat = time.time()

        for raw_line in proc_apply.stdout:
            if time.time() - last_heartbeat >= 15:
                yield _sse_format('heartbeat', {})
                last_heartbeat = time.time()

            line = raw_line.strip()
            if not line:
                continue

            if line.startswith('{'):
                try:
                    msg = json.loads(line)
                    t = msg.get('type', '')
                    if t == 'apply_pct':
                        yield _sse_format('apply_pct', {
                            'pct':     msg.get('pct', 0),
                            'current': msg.get('current', 0),
                            'total':   msg.get('total', 0),
                        })
                    elif t == 'apply_file':
                        yield _sse_format('apply_file', {
                            'status': msg.get('status', ''),
                            'path':   msg.get('path', ''),
                        })
                    elif t == 'done':
                        yield _sse_format('done', {
                            'updated':   msg.get('updated', 0),
                            'unchanged': msg.get('unchanged', 0),
                            'skipped':   msg.get('skipped', 0),
                            'errors':    msg.get('errors', 0),
                            'message':   msg.get('message', 'Готово'),
                        })
                except json.JSONDecodeError:
                    pass

        proc_apply.wait()
        rc_apply = proc_apply.returncode

        if rc_apply not in (0, 2):
            yield _sse_format('error', {
                'message': f'Ошибка установки (rc={rc_apply})',
                'phase': 'apply',
            })

    return Response(
        stream_with_context(_generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control':    'no-cache',
            'X-Accel-Buffering': 'no',
        },
    )


# ─── Проверка обновлений ───────────────────────────────────────────────────────────────────────────────────

@update_bp.route('/api/update/check')
def api_update_check():
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403

    if not os.path.exists(_UPDATER):
        return jsonify({'status': 2, 'error': '_updater.py not found',
                        'has_update': False, 'local_sha': _read_local_sha()}), 200

    force = flask_request.args.get('force') == '1'
    if force and os.path.exists(_FLAG_FILE):
        try:
            os.remove(_FLAG_FILE)
        except Exception:
            pass

    if not force and os.path.exists(_FLAG_FILE):
        try:
            with open(_FLAG_FILE, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            code = int(cached.get('code', 2))
            return jsonify({
                'status':     code,
                'has_update': code == 1,
                'local_sha':  _read_local_sha(),
                'output':     cached.get('output', '')[-800:],
                'cached':     True,
                'checked_at': cached.get('checked_at'),
            })
        except Exception:
            pass

    try:
        result = subprocess.run(
            [sys.executable, _UPDATER, '--check'],
            capture_output=True, text=True, timeout=25
        )
        code = result.returncode
        payload = {
            'code':       code,
            'checked_at': datetime.now().isoformat(),
            'output':     result.stdout[-4000:],
        }
        try:
            with open(_FLAG_FILE, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False)
        except Exception:
            pass

        return jsonify({
            'status':     code,
            'has_update': code == 1,
            'local_sha':  _read_local_sha(),
            'output':     result.stdout[-800:],
            'cached':     False,
            'checked_at': payload['checked_at'],
        })
    except subprocess.TimeoutExpired:
        return jsonify({'status': 2, 'error': 'timeout',
                        'has_update': False, 'local_sha': _read_local_sha()}), 200
    except Exception as e:
        return jsonify({'status': 2, 'error': str(e),
                        'has_update': False, 'local_sha': _read_local_sha()}), 200


# ─── Общая логика рабочего потока: download → таймер → apply ─────────────────────────────────

def _build_timer_worker(delay: int, fire_at_ts: float, force: bool, user_id: int):
    """Ретурнирует целевую функцию для threading.Thread.
    Флоу: phase=downloading → --download-only → rc=0 → phase=scheduled →
           таймер delay сек → phase=applying → --apply-only → rc=0/2 → рестарт.
    rc=1 при download: ошибка записывается в pre-update.json, лок удаляется,
    баннер НЕ показывается.
    """
    def _worker():
        # ── Фаза 1: скачиваем архив ──
        _lock_update_phase('downloading')
        cmd_dl = [sys.executable, _UPDATER, '--download-only']
        if force:
            cmd_dl.append('--force')
        try:
            res = subprocess.run(cmd_dl, capture_output=True, text=True, timeout=300)
            rc_dl = res.returncode
            dl_output = (res.stdout + res.stderr)[-2000:]
        except subprocess.TimeoutExpired:
            rc_dl    = 1
            dl_output = 'timeout: скачивание превысило 300 сек'
        except Exception as e:
            rc_dl    = 1
            dl_output = str(e)

        if rc_dl != 0:
            try:
                with open(_PRE_UPDATE_FILE, 'r', encoding='utf-8') as f:
                    pre = json.load(f)
            except Exception:
                pre = {}
            pre['download_error'] = dl_output
            pre['phase']          = 'download_failed'
            try:
                with open(_PRE_UPDATE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(pre, f, ensure_ascii=False)
            except Exception:
                pass
            time.sleep(5)
            _clear_pre_update()
            _lock_clear()
            return

        # ── Фаза 2: обновляем pre-update — теперь знаем delay ──
        try:
            with open(_PRE_UPDATE_FILE, 'r', encoding='utf-8') as f:
                pre = json.load(f)
        except Exception:
            pre = {}
        pre['phase']      = 'scheduled'
        pre['fire_at_ts'] = fire_at_ts
        try:
            with open(_PRE_UPDATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(pre, f, ensure_ascii=False)
        except Exception:
            pass
        _lock_update_phase('scheduled')

        # ── Фаза 3: ждём delay секунд (с момента завершения скачивания) ──
        while time.time() < fire_at_ts:
            if not os.path.exists(_PRE_UPDATE_FILE):
                _lock_clear()
                return
            time.sleep(1)

        if not os.path.exists(_PRE_UPDATE_FILE):
            _lock_clear()
            return

        _clear_pre_update()
        _lock_update_phase('applying')

        # ── Фаза 4: применяем архив ──
        try:
            open(_MAINTENANCE_FLAG, 'w').close()
        except Exception:
            pass

        cmd_apply = [sys.executable, _UPDATER, '--apply-only']
        if force:
            cmd_apply.append('--force')
        try:
            res_apply = subprocess.run(cmd_apply, capture_output=True, text=True, timeout=300)
            rc_apply  = res_apply.returncode
        except Exception:
            rc_apply = 1
        finally:
            _lock_clear()
            try:
                os.remove(_MAINTENANCE_FLAG)
            except Exception:
                pass

        try:
            open(_RESTART_FLAG, 'w').close()
        except Exception:
            pass

        if rc_apply == 2:
            _run_bat_restart()
        else:
            os._exit(42)

    return _worker


# ─── Запланированное обновление (баннер для всех пользователей) ────────────────────────────────────

@update_bp.route('/api/update/schedule', methods=['POST'])
def api_update_schedule():
    """POST {delay: N, force: bool}
    delay: секунд от момента завершения скачивания (0–3600).
    force: перезаписать все файлы.
    Используется когда нужен баннер ожидания для всех пользователей системы.
    Для немедленного обновления с SSE-прогрессом используй /api/update/stream.
    """
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403

    if os.path.exists(_LOCK_FILE):
        if _lock_is_stale():
            pass
        else:
            return jsonify({'error': 'already_in_progress',
                            'message': 'Обновление уже выполняется'}), 409

    if not os.path.exists(_UPDATER):
        return jsonify({'error': '_updater.py not found'}), 500

    if os.path.exists(_PRE_UPDATE_FILE):
        return jsonify({'error': 'already_scheduled',
                        'message': 'Обновление уже запланировано'}), 409

    body       = flask_request.get_json(silent=True) or {}
    delay      = int(body.get('delay', 120))
    force      = bool(body.get('force', False))
    delay      = max(0, min(_MAX_DELAY, delay))

    scheduled_at = datetime.now().isoformat()
    fire_at_ts   = time.time() + delay

    payload = {
        'scheduled_at':  scheduled_at,
        'fire_at_ts':    fire_at_ts,
        'delay':         delay,
        'force':         force,
        'phase':         'downloading',
        'scheduled_by':  session.get('full_name', session.get('username', '')),
        'download_error': None,
    }
    try:
        with open(_PRE_UPDATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    _lock_write('downloading')

    conn = get_db()
    log_action(conn, session['user_id'], 'update_scheduled',
               detail=f'Обновление запланировано: delay={delay}s force={force}')
    conn.commit()
    conn.close()

    worker = _build_timer_worker(
        delay=delay,
        fire_at_ts=fire_at_ts,
        force=force,
        user_id=session['user_id'],
    )
    threading.Thread(target=worker, daemon=True).start()

    return jsonify({
        'ok':           True,
        'delay':        delay,
        'fire_at_ts':   fire_at_ts,
        'message':      f'Скачиваем... После загрузки баннер появится через ~{delay}с после начала загрузки.',
    })


# ─── Обратная совместимость: /apply и /apply-force ───────────────────────────────────────────────

@update_bp.route('/api/update/apply', methods=['POST'])
def api_update_apply():
    """shortcut: delay=1, force=False"""
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403
    return _schedule_internal(delay=1, force=False)


@update_bp.route('/api/update/apply-force', methods=['POST'])
def api_update_apply_force():
    """shortcut: delay=1, force=True"""
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403
    return _schedule_internal(delay=1, force=True)


def _schedule_internal(delay: int, force: bool):
    """core schedule без чтения request body — используется /apply и /apply-force."""
    if os.path.exists(_LOCK_FILE):
        if _lock_is_stale():
            pass
        else:
            return jsonify({'error': 'already_in_progress',
                            'message': 'Обновление уже выполняется'}), 409

    if not os.path.exists(_UPDATER):
        return jsonify({'error': '_updater.py not found'}), 500

    if os.path.exists(_PRE_UPDATE_FILE):
        return jsonify({'error': 'already_scheduled',
                        'message': 'Обновление уже запланировано'}), 409

    scheduled_at = datetime.now().isoformat()
    fire_at_ts   = time.time() + delay
    payload = {
        'scheduled_at':   scheduled_at,
        'fire_at_ts':     fire_at_ts,
        'delay':          delay,
        'force':          force,
        'phase':          'downloading',
        'scheduled_by':   session.get('full_name', session.get('username', '')),
        'download_error': None,
    }
    try:
        with open(_PRE_UPDATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    _lock_write('downloading')

    conn = get_db()
    log_action(conn, session['user_id'], 'update_apply',
               detail=f'Обновление (через apply): delay={delay}s force={force}')
    conn.commit()
    conn.close()

    worker = _build_timer_worker(
        delay=delay,
        fire_at_ts=fire_at_ts,
        force=force,
        user_id=session['user_id'],
    )
    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'ok': True, 'delay': delay, 'fire_at_ts': fire_at_ts,
                    'message': f'Запущено. Скачиваем архив... потом перезапуск через ~{delay}с.'})


# ─── Отмена запланированного обновления ─────────────────────────────────────────────────────────────────────────────────

@update_bp.route('/api/update/schedule/cancel', methods=['POST'])
def api_update_schedule_cancel():
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403

    if not os.path.exists(_PRE_UPDATE_FILE):
        return jsonify({'error': 'not_scheduled',
                        'message': 'Нет активного расписания'}), 404

    try:
        with open(_PRE_UPDATE_FILE, 'r', encoding='utf-8') as f:
            pre = json.load(f)
        if pre.get('phase') in ('applying',):
            return jsonify({'error': 'too_late',
                            'message': 'Уже выполняется установка — отмена невозможна'}), 409
    except Exception:
        pass

    _clear_pre_update()

    conn = get_db()
    log_action(conn, session['user_id'], 'update_schedule_cancelled',
               detail='Запланированное обновление отменено')
    conn.commit()
    conn.close()

    return jsonify({'ok': True, 'message': 'Обновление отменено'})


# ─── Статус текущего обновления ───────────────────────────────────────────────────────────────────────────────

@update_bp.route('/api/update/status')
@update_bp.route('/api/update-status')  # алиас: обратная совместимость с base.html
def api_update_status():
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403
    in_progress = os.path.exists(_LOCK_FILE) and not _lock_is_stale()
    phase = None
    if in_progress:
        try:
            with open(_LOCK_FILE, 'r', encoding='utf-8') as f:
                phase = json.load(f).get('phase')
        except Exception:
            pass
    return jsonify({'in_progress': in_progress, 'phase': phase})


# ─── Статус предобновления (публичный для всех авторизованных) ──────────────────────────

@update_bp.route('/api/update/pre-status')
def api_update_pre_status():
    """v2.0: добавлены phase и download_error.
    phase: downloading | scheduled | applying | download_failed
    """
    if 'user_id' not in session:
        return jsonify({'scheduled': False}), 200

    if not os.path.exists(_PRE_UPDATE_FILE):
        return jsonify({'scheduled': False}), 200

    try:
        with open(_PRE_UPDATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        phase          = data.get('phase', 'scheduled')
        fire_at_ts     = data.get('fire_at_ts', 0)
        seconds_left   = max(0, int(fire_at_ts - time.time()))
        download_error = data.get('download_error')
        return jsonify({
            'scheduled':      True,
            'phase':          phase,
            'seconds_left':   seconds_left,
            'fire_at_ts':     fire_at_ts,
            'scheduled_by':   data.get('scheduled_by', ''),
            'scheduled_at':   data.get('scheduled_at', ''),
            'download_error': download_error,
        })
    except Exception:
        _clear_pre_update()
        return jsonify({'scheduled': False}), 200
