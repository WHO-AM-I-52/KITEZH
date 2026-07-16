# ╔═══════════════════════════════════════════════════════════════╗
# ║              update_changelog.py                             ║
# ║  Синхронизация журнала версий из GitHub:              ║
# ║    /api/changelog/sync  — запуск updater/sync_changelog.py  ║
# ║                           (только admin)                          ║
# ╚═══════════════════════════════════════════════════════════════╝

from flask import Blueprint, jsonify, session
from db import get_db, BASE_DIR
from core.activity_log import log_action
import os
import sys
import subprocess

update_changelog_bp = Blueprint('update_changelog', __name__)

_SYNC_CHANGELOG = os.path.join(BASE_DIR, 'updater', 'sync_changelog.py')


@update_changelog_bp.route('/api/changelog/sync', methods=['POST'])
def api_changelog_sync():
    """Запускает updater/sync_changelog.py.

    Обновляет changelog.py (из GitHub-релизов репозитория SONAR)
    и services/roadmap.py (из ROADMAP.md). Доступно только администратору.
    Возвращает JSON: {ok, output, error}.
    """
    if session.get('role') != 'admin':
        return jsonify({'error': 'forbidden'}), 403

    if not os.path.exists(_SYNC_CHANGELOG):
        return jsonify({
            'ok':    False,
            'error': 'sync_changelog.py не найден',
            'output': '',
        }), 500

    try:
        result = subprocess.run(
            [sys.executable, _SYNC_CHANGELOG],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=BASE_DIR,
        )
        ok     = result.returncode == 0
        output = (result.stdout + result.stderr).strip()[-2000:]

        try:
            conn = get_db()
            log_action(
                conn,
                session['user_id'],
                'changelog_sync',
                detail=f'sync_changelog.py: rc={result.returncode}',
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

        return jsonify({'ok': ok, 'output': output, 'error': '' if ok else output})

    except subprocess.TimeoutExpired:
        return jsonify({'ok': False, 'output': '', 'error': 'Таймаут (30 сек)'}), 200
    except Exception as e:
        return jsonify({'ok': False, 'output': '', 'error': str(e)}), 200
