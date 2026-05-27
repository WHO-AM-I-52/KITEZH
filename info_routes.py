# ╔══════════════════════════════════════════════════════════════╗
# ║                      info_routes.py                          ║
# ║  Сервисные страницы: уведомления, журнал изменений, онлайн   ║
# ║  v2.2.0: /ping фиксирует присутствие, /api/online — счётчик  ║
# ║  v2.3.6: /api/update/check и /api/update/apply               ║
# ║  fix: ?force=1 сбрасывает серверный кэш               ║
# ║  fix: _restart.flag + sys.exit(42) → run_server.py          ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import Blueprint, render_template, session, jsonify, request as flask_request
from db import get_db, BASE_DIR
from auth_utils import login_required
from changelog import CHANGELOG, ROADMAP
from datetime import datetime
import os
import sys
import subprocess
import json
import threading
import signal

misc_bp = Blueprint('misc', __name__)


@misc_bp.route('/notifications')
@login_required
def notifications():
    conn = get_db()
    items = conn.execute(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC",
        (session['user_id'],)
    ).fetchall()
    conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (session['user_id'],))
    conn.commit()
    conn.close()
    return render_template('notifications.html', items=items)


@misc_bp.route('/changelog')
@login_required
def changelog():
    current_version = CHANGELOG[0]['version'] if CHANGELOG else ''
    session['seen_version'] = current_version
    return render_template('changelog.html', changelog=CHANGELOG,
                           version=current_version, roadmap=ROADMAP)


@misc_bp.route('/ping')
def ping():
    """Heartbeat: обновляет online_presence если пользователь авторизован."""
    uid = session.get('user_id')
    if uid:
        try:
            conn = get_db()
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            conn.execute(
                """
                INSERT INTO online_presence (user_id, last_seen)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET last_seen=excluded.last_seen
                """,
                (uid, now)
            )
            conn.commit()
            conn.close()
        except Exception:
            pass
    return '', 204


@misc_bp.route('/api/online')
@login_required
def api_online():
    """Возвращает число уникальных пользователей активных за последние 5 минут."""
    conn = get_db()
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT user_id) AS cnt
        FROM online_presence
        WHERE last_seen >= datetime('now', '-5 minutes')
        """
    ).fetchone()
    conn.close()
    count = row['cnt'] if row else 0
    return jsonify({'online': count})


@misc_bp.route('/api/search')
@login_required
def api_search():
    """Глобальный поиск по обращениям: номер или имя заявителя."""
    q = flask_request.args.get('q', '').strip()
    if not q or len(q) < 2:
        return jsonify({'results': []})
    conn = get_db()
    like = f'%{q}%'
    rows = conn.execute(
        """
        SELECT id, request_number, applicant_short_name, applicant_full_name,
               status, project_name
        FROM requests
        WHERE request_number LIKE ?
           OR applicant_short_name LIKE ?
           OR applicant_full_name  LIKE ?
           OR project_name         LIKE ?
        ORDER BY id DESC
        LIMIT 20
        """,
        (like, like, like, like)
    ).fetchall()
    conn.close()
    results = [
        {
            'id': r['id'],
            'request_number': r['request_number'],
            'applicant': r['applicant_short_name'] or r['applicant_full_name'] or '',
            'status': r['status'],
            'project_name': r['project_name'] or ''
        }
        for r in rows
    ]
    return jsonify({'results': results})


# ─── Обновления SONAR через GitHub ──────────────────────────────────────────
# Маршруты доступны только администратору (role == 'admin').
# Используют _updater.py для проверки и применения обновлений.
# Результат проверки кэшируется в _update_available.json до следующего запуска.
# ?force=1 — принудительно сбрасывает серверный кэш и делает живую проверку.
# Применение обновления: _worker создаёт _restart.flag, затем
# вызывает os._exit(42) — Flask завершается с кодом 42.
# run_server.py видит код 42, читает флаг и возвращает sys.exit(42) батнику.
# Батник видит EXIT_CODE=42 и идёт goto :start_server.
# ────────────────────────────────────────────────────────────────────────────

_FLAG_FILE    = os.path.join(BASE_DIR, '_update_available.json')
_LOCK_FILE    = os.path.join(BASE_DIR, '_updating.lock')
_RESTART_FLAG = os.path.join(BASE_DIR, '_restart.flag')
_UPDATER      = os.path.join(BASE_DIR, '_updater.py')
_COMMIT_FILE  = os.path.join(BASE_DIR, '_last_commit.txt')


def _read_local_sha() -> str:
    if os.path.exists(_COMMIT_FILE):
        try:
            return open(_COMMIT_FILE, encoding='utf-8').read().strip()[:12]
        except Exception:
            pass
    return ''


@misc_bp.route('/api/update/check')
def api_update_check():
    """Проверяет наличие обновлений на GitHub.

    Параметры запроса:
      ?force=1  — сбросить серверный кэш и сделать живую проверку через GitHub API

    Возвращает JSON:
      status     — 0 = актуально, 1 = есть обновления, 2 = ошибка / нет сети
      has_update — bool
      local_sha  — первые 12 символов SHA установленной версии
      output     — последние строки вывода _updater.py --check
      cached     — True если результат взят из кэша, False если свежая проверка

    Доступно только администратору.
    """
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403

    if not os.path.exists(_UPDATER):
        return jsonify({'status': 2, 'error': '_updater.py not found',
                        'has_update': False, 'local_sha': _read_local_sha()}), 200

    # ── Принудительный сброс серверного кэша ──────────────────────────────
    force = flask_request.args.get('force') == '1'
    if force and os.path.exists(_FLAG_FILE):
        try:
            os.remove(_FLAG_FILE)
        except Exception:
            pass

    # ── Быстрый путь: отдаём кэш (если не force) ──────────────────────────
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

    # ── Медленный путь: запускаем _updater.py --check ─────────────────────
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


@misc_bp.route('/api/update/apply', methods=['POST'])
def api_update_apply():
    """Скачивает и применяет обновление с GitHub, затем тихо перезапускает сервер.

    _worker создаёт _restart.flag, затем вызывает os._exit(42).
    run_server.py видит код 42, читает флаг и возвращает sys.exit(42) батнику.
    Батник видит EXIT_CODE=42 и идёт goto :start_server без любых вопросов.
    """
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403

    if os.path.exists(_LOCK_FILE):
        return jsonify({'error': 'already_in_progress',
                        'message': 'Обновление уже выполняется'}), 409

    if not os.path.exists(_UPDATER):
        return jsonify({'error': '_updater.py not found'}), 500

    try:
        open(_LOCK_FILE, 'w').close()
    except Exception:
        pass

    def _worker():
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

        # Создаём флаг ДО завершения
        try:
            open(_RESTART_FLAG, 'w').close()
        except Exception:
            pass

        # os._exit(42) — мгновенно завершает весь процесс Flask
        # без каких-либо сигналов на родительский батник
        os._exit(42)

    threading.Thread(target=_worker, daemon=True).start()
    return jsonify({'ok': True,
                    'message': 'Обновление запущено. Сервер перезапустится через ~10–30 сек.'})


@misc_bp.route('/api/update/status')
def api_update_status():
    """Возвращает текущий статус процесса обновления."""
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403
    return jsonify({'in_progress': os.path.exists(_LOCK_FILE)})
