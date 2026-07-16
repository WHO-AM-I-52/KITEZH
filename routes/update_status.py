# ╔═══════════════════════════════════════════════════════════════╗
# ║              update_status.py                                ║
# ║  Маршруты статуса обновления KITEZH:                      ║
# ║    /api/update/status     — статус текущей операции        ║
# ║    /api/update-status     — алиас v2.0.1                         ║
# ║    /api/update/pre-status — публичный (все авт. пользов.)     ║
# ║    /api/update/result     — итог (одноразовый, только admin)   ║
# ║    /api/update/public-log — публичный лог прогресса        ║
# ╚═══════════════════════════════════════════════════════════════╝

from flask import Blueprint, jsonify, session
from db import get_db
from core.activity_log import log_action
from routes.update_helpers import (
    _LOCK_FILE, _PRE_UPDATE_FILE, _UPDATE_RESULT_FILE, _PUBLIC_LOG_FILE,
    _lock_is_stale, _clear_pre_update,
)
import os
import json
import time

update_status_bp = Blueprint('update_status', __name__)


# ─── Статус текущего обновления ──────────────────────────────────────────────

@update_status_bp.route('/api/update/status')
@update_status_bp.route('/api/update-status')
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


# ─── Статус предобновления (публичный для всех авт.) ──────────────────────

@update_status_bp.route('/api/update/pre-status')
def api_update_pre_status():
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


# ─── Результат применённого обновления (one-shot, только админ) ──────────────

@update_status_bp.route('/api/update/result')
def api_update_result():
    if session.get('role') != 'admin':
        return jsonify({'available': False}), 200

    if not os.path.exists(_UPDATE_RESULT_FILE):
        return jsonify({'available': False}), 200

    try:
        with open(_UPDATE_RESULT_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        try:
            os.remove(_UPDATE_RESULT_FILE)
        except Exception:
            pass
        return jsonify({'available': False}), 200

    try:
        conn = get_db()
        log_action(conn, session['user_id'], 'update_applied',
                   detail=(
                       f"Обновление применено: "
                       f"updated={data.get('updated', 0)} "
                       f"unchanged={data.get('unchanged', 0)} "
                       f"skipped={data.get('skipped', 0)} "
                       f"errors={data.get('errors', 0)} "
                       f"by={data.get('applied_by', '')}"
                   ))
        conn.commit()
        conn.close()
    except Exception:
        pass

    try:
        os.remove(_UPDATE_RESULT_FILE)
    except Exception:
        pass

    return jsonify({
        'available':   True,
        'ok':          data.get('ok', True),
        'updated':     data.get('updated', 0),
        'unchanged':   data.get('unchanged', 0),
        'skipped':     data.get('skipped', 0),
        'errors':      data.get('errors', 0),
        'message':     data.get('message', ''),
        'finished_at': data.get('finished_at', ''),
        'applied_by':  data.get('applied_by', ''),
    })


# ─── Публичный лог прогресса обновления ──────────────────────────────────

@update_status_bp.route('/api/update/public-log')
def api_update_public_log():
    if not os.path.exists(_PUBLIC_LOG_FILE):
        return jsonify({'ok': True, 'entries': []})
    try:
        with open(_PUBLIC_LOG_FILE, 'r', encoding='utf-8') as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        entries = []
        for ln in lines[-50:]:
            try:
                entries.append(json.loads(ln))
            except json.JSONDecodeError:
                pass
        return jsonify({'ok': True, 'entries': entries})
    except Exception:
        return jsonify({'ok': False, 'entries': []})
