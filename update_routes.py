# ╔══════════════════════════════════════════════════════════════╗
# ║                     update_routes.py                         ║
# ║  Blueprint обновлений KITEZH через GitHub                    ║
# ║  v1.0.0: перенос из info_routes.py                          ║
# ║  v1.1.0: /api/update/schedule — запланированное обновление   ║
# ║           /api/update/pre-status — статус для глобального    ║
# ║           поллера всех страниц                               ║
# ║  v1.1.1: pre-status добавляет fire_at_ts для точного      ║
# ║           обратного отсчёта в баннере                        ║
# ║  v1.2.0: /api/update/apply-force — принудительное обновление ║
# ║           перезаписывает все файлы вне зависимости от SHA    ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import Blueprint, jsonify, request as flask_request, session
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

_SCHEDULE_DELAY   = 120  # секунд до начала обновления


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


# ─── Проверка обновлений ─────────────────────────────────────────────────────────

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


# ─── Немедленное применение обновления ──────────────────────────────────────────

@update_bp.route('/api/update/apply', methods=['POST'])
def api_update_apply():
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403

    if os.path.exists(_LOCK_FILE):
        return jsonify({'error': 'already_in_progress',
                        'message': 'Обновление уже выполняется'}), 409

    if not os.path.exists(_UPDATER):
        return jsonify({'error': '_updater.py not found'}), 500

    _clear_pre_update()

    try:
        open(_LOCK_FILE, 'w').close()
    except Exception:
        pass

    conn = get_db()
    log_action(conn, session['user_id'], 'update_apply',
               detail='Запущено немедленное обновление KITEZH')
    conn.commit()
    conn.close()

    def _worker():
        try:
            open(_MAINTENANCE_FLAG, 'w').close()
        except Exception:
            pass
        try:
            subprocess.run([sys.executable, _UPDATER], timeout=300)
        except Exception:
            pass
        finally:
            for path in (_FLAG_FILE, _LOCK_FILE):
                try:
                    os.remove(path)
                except Exception:
                    pass
        try:
            open(_RESTART_FLAG, 'w').close()
        except Exception:
            pass
        os._exit(42)

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({'ok': True,
                    'message': 'Обновление запущено. Сервер перезапустится через ~10–30 сек.'})


# ─── Принудительное обновление (force) ──────────────────────────────────────────
# Перезаписывает ВСЕ файлы из GitHub, игнорируя сравнение байт и _last_commit.txt.
# Используется когда локальные файлы расходятся с репозиторием без изменения SHA.

@update_bp.route('/api/update/apply-force', methods=['POST'])
def api_update_apply_force():
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403

    if os.path.exists(_LOCK_FILE):
        return jsonify({'error': 'already_in_progress',
                        'message': 'Обновление уже выполняется'}), 409

    if not os.path.exists(_UPDATER):
        return jsonify({'error': '_updater.py not found'}), 500

    _clear_pre_update()

    try:
        open(_LOCK_FILE, 'w').close()
    except Exception:
        pass

    conn = get_db()
    log_action(conn, session['user_id'], 'update_apply_force',
               detail='Запущено ПРИНУДИТЕЛЬНОЕ обновление KITEZH (--force): все файлы перезаписаны')
    conn.commit()
    conn.close()

    def _worker():
        try:
            open(_MAINTENANCE_FLAG, 'w').close()
        except Exception:
            pass
        try:
            subprocess.run([sys.executable, _UPDATER, '--force'], timeout=300)
        except Exception:
            pass
        finally:
            for path in (_FLAG_FILE, _LOCK_FILE):
                try:
                    os.remove(path)
                except Exception:
                    pass
        try:
            open(_RESTART_FLAG, 'w').close()
        except Exception:
            pass
        os._exit(42)

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({'ok': True,
                    'message': 'Принудительное обновление запущено. Все файлы перезаписываются. Сервер перезапустится через ~10–30 сек.'})


# ─── Статус текущего обновления ────────────────────────────────────────────────

@update_bp.route('/api/update/status')
def api_update_status():
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403
    return jsonify({'in_progress': os.path.exists(_LOCK_FILE)})


# ─── Запланированное обновление с обратным отсчётом ───────────────────────────

@update_bp.route('/api/update/schedule', methods=['POST'])
def api_update_schedule():
    """Запускает таймер на _SCHEDULE_DELAY секунд, затем применяет обновление.
    Все страницы видят обратный отсчёт через /api/update/pre-status.
    Отмена возможна до истечения таймера через /api/update/schedule/cancel.
    """
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403

    if os.path.exists(_LOCK_FILE):
        return jsonify({'error': 'already_in_progress',
                        'message': 'Обновление уже выполняется'}), 409

    if not os.path.exists(_UPDATER):
        return jsonify({'error': '_updater.py not found'}), 500

    if os.path.exists(_PRE_UPDATE_FILE):
        return jsonify({'error': 'already_scheduled',
                        'message': 'Обновление уже запланировано'}), 409

    scheduled_at = datetime.now().isoformat()
    fire_at_ts   = time.time() + _SCHEDULE_DELAY

    payload = {
        'scheduled_at': scheduled_at,
        'fire_at_ts':   fire_at_ts,
        'scheduled_by': session.get('full_name', session.get('username', '')),
    }
    try:
        with open(_PRE_UPDATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    conn = get_db()
    log_action(conn, session['user_id'], 'update_scheduled',
               detail=f'Обновление запланировано через {_SCHEDULE_DELAY} сек.')
    conn.commit()
    conn.close()

    def _timer_worker():
        deadline = fire_at_ts
        while time.time() < deadline:
            if not os.path.exists(_PRE_UPDATE_FILE):
                return  # отменено
            time.sleep(1)

        if not os.path.exists(_PRE_UPDATE_FILE):
            return  # отменено в последнюю секунду

        _clear_pre_update()

        if os.path.exists(_LOCK_FILE):
            return  # другое обновление уже стартовало

        try:
            open(_LOCK_FILE, 'w').close()
        except Exception:
            pass

        try:
            open(_MAINTENANCE_FLAG, 'w').close()
        except Exception:
            pass

        try:
            subprocess.run([sys.executable, _UPDATER], timeout=300)
        except Exception:
            pass
        finally:
            for path in (_FLAG_FILE, _LOCK_FILE):
                try:
                    os.remove(path)
                except Exception:
                    pass

        try:
            open(_RESTART_FLAG, 'w').close()
        except Exception:
            pass

        os._exit(42)

    threading.Thread(target=_timer_worker, daemon=True).start()

    return jsonify({
        'ok':           True,
        'seconds_left': _SCHEDULE_DELAY,
        'fire_at_ts':   fire_at_ts,
        'message':      f'Обновление запланировано через {_SCHEDULE_DELAY} сек. Все пользователи увидят предупреждение.',
    })


# ─── Отмена запланированного обновления ───────────────────────────────────────────

@update_bp.route('/api/update/schedule/cancel', methods=['POST'])
def api_update_schedule_cancel():
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403

    if not os.path.exists(_PRE_UPDATE_FILE):
        return jsonify({'error': 'not_scheduled',
                        'message': 'Нет активного расписания'}), 404

    _clear_pre_update()

    conn = get_db()
    log_action(conn, session['user_id'], 'update_schedule_cancelled',
               detail='Запланированное обновление отменено')
    conn.commit()
    conn.close()

    return jsonify({'ok': True, 'message': 'Обновление отменено'})


# ─── Статус предобновления (публичный для всех авторизованных) ─────────────────

@update_bp.route('/api/update/pre-status')
def api_update_pre_status():
    """Возвращает статус запланированного обновления для глобального поллера.
    Доступен всем авторизованным пользователям — не только admin.
    v1.1.1: добавляет fire_at_ts для точного отсчёта в баннере (update_banner.js).
    """
    if 'user_id' not in session:
        return jsonify({'scheduled': False}), 200

    if not os.path.exists(_PRE_UPDATE_FILE):
        return jsonify({'scheduled': False}), 200

    try:
        with open(_PRE_UPDATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        fire_at_ts   = data['fire_at_ts']
        seconds_left = max(0, int(fire_at_ts - time.time()))
        return jsonify({
            'scheduled':    True,
            'seconds_left': seconds_left,
            'fire_at_ts':   fire_at_ts,          # новое поле — для update_banner.js
            'scheduled_by': data.get('scheduled_by', ''),
            'scheduled_at': data.get('scheduled_at', ''),
        })
    except Exception:
        _clear_pre_update()
        return jsonify({'scheduled': False}), 200
