# ╔═══════════════════════════════════════════════════════════════╗
# ║              update_control.py                               ║
# ║  Маршруты управления обновлением KITEZH:                     ║
# ║    /api/update/check          — проверка наличия обновления  ║
# ║    /api/update/schedule       — запланировать обновление     ║
# ║    /api/update/apply          — немедленный запуск (legacy)  ║
# ║    /api/update/apply-force    — принудительный запуск        ║
# ║    /api/update/schedule/cancel — отмена запланированного     ║
# ╚═══════════════════════════════════════════════════════════════╝

from flask import Blueprint, jsonify, request as flask_request, session
from db import get_db, BASE_DIR
from core.activity_log import log_action
from routes.update_helpers import (
    FLAG_FILE, LOCK_FILE, PRE_UPDATE_FILE, UPDATER,
    MIN_DELAY, MAX_DELAY,
    read_local_sha, clear_pre_update,
    lock_write, lock_is_stale,
    build_timer_worker,
)
from datetime import datetime
import os
import sys
import subprocess
import json
import threading
import time

update_control_bp = Blueprint('update_control', __name__)


# ─── Проверка обновлений ──────────────────────────────────────────────────────

@update_control_bp.route('/api/update/check')
def api_update_check():
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403

    if not os.path.exists(UPDATER):
        return jsonify({'status': 2, 'error': '_updater.py not found',
                        'has_update': False, 'local_sha': read_local_sha()}), 200

    force = flask_request.args.get('force') == '1'
    if force and os.path.exists(FLAG_FILE):
        try:
            os.remove(FLAG_FILE)
        except Exception:
            pass

    if not force and os.path.exists(FLAG_FILE):
        try:
            with open(FLAG_FILE, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            code = int(cached.get('code', 2))
            return jsonify({
                'status':     code,
                'has_update': code == 1,
                'local_sha':  read_local_sha(),
                'output':     cached.get('output', '')[-800:],
                'cached':     True,
                'checked_at': cached.get('checked_at'),
            })
        except Exception:
            pass

    try:
        result = subprocess.run(
            [sys.executable, UPDATER, '--check'],
            capture_output=True, text=True, timeout=25
        )
        code = result.returncode
        payload = {
            'code':       code,
            'checked_at': datetime.now().isoformat(),
            'output':     result.stdout[-4000:],
        }
        try:
            with open(FLAG_FILE, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False)
        except Exception:
            pass

        return jsonify({
            'status':     code,
            'has_update': code == 1,
            'local_sha':  read_local_sha(),
            'output':     result.stdout[-800:],
            'cached':     False,
            'checked_at': payload['checked_at'],
        })
    except subprocess.TimeoutExpired:
        return jsonify({'status': 2, 'error': 'timeout',
                        'has_update': False, 'local_sha': read_local_sha()}), 200
    except Exception as e:
        return jsonify({'status': 2, 'error': str(e),
                        'has_update': False, 'local_sha': read_local_sha()}), 200


# ─── Запланированное обновление ──────────────────────────────────────────────

@update_control_bp.route('/api/update/schedule', methods=['POST'])
def api_update_schedule():
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403

    if os.path.exists(LOCK_FILE):
        if not lock_is_stale():
            return jsonify({'error': 'already_in_progress',
                            'message': 'Обновление уже выполняется'}), 409

    if not os.path.exists(UPDATER):
        return jsonify({'error': '_updater.py not found'}), 500

    if os.path.exists(PRE_UPDATE_FILE):
        return jsonify({'error': 'already_scheduled',
                        'message': 'Обновление уже запланировано'}), 409

    body  = flask_request.get_json(silent=True) or {}
    delay = int(body.get('delay', 120))
    force = bool(body.get('force', False))
    delay = max(0, min(MAX_DELAY, delay))

    scheduled_at        = datetime.now().isoformat()
    fire_at_ts_estimate = time.time() + delay

    payload = {
        'scheduled_at':   scheduled_at,
        'fire_at_ts':     fire_at_ts_estimate,
        'delay':          delay,
        'force':          force,
        'phase':          'downloading',
        'scheduled_by':   session.get('full_name', session.get('username', '')),
        'download_error': None,
    }
    try:
        with open(PRE_UPDATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    lock_write('downloading')

    conn = get_db()
    log_action(conn, session['user_id'], 'update_scheduled',
               detail=f'Обновление запланировано: delay={delay}s force={force}')
    conn.commit()
    conn.close()

    worker = build_timer_worker(
        delay=delay,
        force=force,
        user_id=session['user_id'],
        applied_by=session.get('full_name', session.get('username', '')),
    )
    threading.Thread(target=worker, daemon=True).start()

    return jsonify({
        'ok':         True,
        'delay':      delay,
        'fire_at_ts': fire_at_ts_estimate,
        'message':    f'Скачиваем... После загрузки баннер появится через ~{delay}с после начала загрузки.',
    })


# ─── Обратная совместимость: /apply и /apply-force ───────────────────────────

def _schedule_internal(delay: int, force: bool):
    if os.path.exists(LOCK_FILE):
        if not lock_is_stale():
            return jsonify({'error': 'already_in_progress',
                            'message': 'Обновление уже выполняется'}), 409

    if not os.path.exists(UPDATER):
        return jsonify({'error': '_updater.py not found'}), 500

    if os.path.exists(PRE_UPDATE_FILE):
        return jsonify({'error': 'already_scheduled',
                        'message': 'Обновление уже запланировано'}), 409

    scheduled_at        = datetime.now().isoformat()
    fire_at_ts_estimate = time.time() + delay
    payload = {
        'scheduled_at':   scheduled_at,
        'fire_at_ts':     fire_at_ts_estimate,
        'delay':          delay,
        'force':          force,
        'phase':          'downloading',
        'scheduled_by':   session.get('full_name', session.get('username', '')),
        'download_error': None,
    }
    try:
        with open(PRE_UPDATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    lock_write('downloading')

    conn = get_db()
    log_action(conn, session['user_id'], 'update_apply',
               detail=f'Обновление (через apply): delay={delay}s force={force}')
    conn.commit()
    conn.close()

    worker = build_timer_worker(
        delay=delay,
        force=force,
        user_id=session['user_id'],
        applied_by=session.get('full_name', session.get('username', '')),
    )
    threading.Thread(target=worker, daemon=True).start()
    return jsonify({'ok': True, 'delay': delay, 'fire_at_ts': fire_at_ts_estimate,
                    'message': f'Запущено. Скачиваем архив... потом перезапуск через ~{delay}с.'})


@update_control_bp.route('/api/update/apply', methods=['POST'])
def api_update_apply():
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403
    return _schedule_internal(delay=1, force=False)


@update_control_bp.route('/api/update/apply-force', methods=['POST'])
def api_update_apply_force():
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403
    return _schedule_internal(delay=1, force=True)


# ─── Отмена запланированного обновления ──────────────────────────────────────

@update_control_bp.route('/api/update/schedule/cancel', methods=['POST'])
def api_update_schedule_cancel():
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403

    if not os.path.exists(PRE_UPDATE_FILE):
        return jsonify({'error': 'not_scheduled',
                        'message': 'Нет активного расписания'}), 404

    try:
        with open(PRE_UPDATE_FILE, 'r', encoding='utf-8') as f:
            pre = json.load(f)
        if pre.get('phase') in ('applying',):
            return jsonify({'error': 'too_late',
                            'message': 'Уже выполняется установка — отмена невозможна'}), 409
    except Exception:
        pass

    clear_pre_update()

    conn = get_db()
    log_action(conn, session['user_id'], 'update_schedule_cancelled',
               detail='Запланированное обновление отменено')
    conn.commit()
    conn.close()

    return jsonify({'ok': True, 'message': 'Обновление отменено'})
