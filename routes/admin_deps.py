# ╔══════════════════════════════════════════════════════════════╗
# ║                      admin_deps.py                           ║
# ║  Управление зависимостями и дашборд админки.                 ║
# ║  Выделено из admin_routes.py (декомпозиция, refactor/structure).║
# ║  register(admin_bp) навешивает роуты на общий Blueprint admin, ║
# ║  поэтому endpoint-имена (admin.*) и url_for сохраняются.      ║
# ╚══════════════════════════════════════════════════════════════╝

import os
import sys
import subprocess
import importlib.util

from flask import render_template, request, jsonify

from core.auth_utils import login_required, admin_required
from paths import PROJECT_ROOT

# requirements.txt лежит в КОРНЕ проекта (paths.PROJECT_ROOT).
# Раньше модуль лежал в корне и брал путь от __file__; теперь модуль
# в routes/, поэтому берём корень из единого источника правды.
_REQUIREMENTS = os.path.join(PROJECT_ROOT, 'requirements.txt')

# Маппинг: имя дистрибутива pip (как в requirements.txt)
#          → реальное import-имя модуля
# Нужен для пакетов, у которых имя дистрибутива ≠ import-имени
_IMPORT_NAME = {
    'python-dotenv':  'dotenv',
    'Pillow':         'PIL',
    'python-docx':    'docx',
    'pdfminer.six':   'pdfminer',
    'scikit-learn':   'sklearn',
    'beautifulsoup4': 'bs4',
    'pystray':        'pystray',
}


def register(admin_bp):
    """Навесить роуты дашборда и управления зависимостями на admin_bp."""

    # ─── /admin дашборд ──────────────────────────────────────────
    @admin_bp.route('/admin')
    @login_required
    @admin_required
    def admin_index():
        return render_template('admin/index.html')

    # ─── /admin/deps ─────────────────────────────────────────────
    @admin_bp.route('/admin/deps')
    @login_required
    @admin_required
    def admin_deps():
        return render_template('admin/deps.html')

    # ─── /api/deps/check ─────────────────────────────────────────
    @admin_bp.route('/api/deps/check')
    @login_required
    @admin_required
    def api_deps_check():
        """Read requirements.txt, check which packages are installed."""
        if not os.path.exists(_REQUIREMENTS):
            return jsonify({'error': 'requirements.txt not found', 'path': _REQUIREMENTS}), 404

        import re
        import importlib.metadata as meta

        packages = []
        with open(_REQUIREMENTS, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                m = re.match(r'^([A-Za-z0-9_\-\.]+)([><=!].+)?$', line)
                if not m:
                    continue
                pkg_name = m.group(1)
                req_ver  = (m.group(2) or '').strip()

                import_name = _IMPORT_NAME.get(pkg_name) or pkg_name.replace('-', '_').lower()

                installed     = False
                installed_ver = ''
                try:
                    spec      = importlib.util.find_spec(import_name)
                    installed = spec is not None
                except (ModuleNotFoundError, ValueError):
                    installed = False

                if installed:
                    try:
                        installed_ver = meta.version(pkg_name)
                    except Exception:
                        installed_ver = ''

                packages.append({
                    'name':              pkg_name,
                    'required_version':  req_ver,
                    'installed':         installed,
                    'installed_version': installed_ver,
                })

        return jsonify({'packages': packages, 'path': _REQUIREMENTS})

    # ─── /api/deps/install ───────────────────────────────────────
    @admin_bp.route('/api/deps/install', methods=['POST'])
    @login_required
    @admin_required
    def api_deps_install():
        """Install a single package or all from requirements.txt.
        Body: {"package": "jellyfish"} or {"all": true}
        """
        data = request.get_json(silent=True) or {}

        BASE_FLAGS = [
            '--no-warn-script-location',
            '--disable-pip-version-check',
            '--no-input',
        ]

        if data.get('all'):
            if not os.path.exists(_REQUIREMENTS):
                return jsonify({'ok': False, 'error': 'requirements.txt not found', 'path': _REQUIREMENTS}), 404
            cmd = [sys.executable, '-m', 'pip', 'install', '-r', _REQUIREMENTS] + BASE_FLAGS
        elif data.get('package'):
            pkg = data['package'].strip()
            if not pkg or not all(c.isalnum() or c in '-_.' for c in pkg):
                return jsonify({'ok': False, 'error': 'invalid package name'}), 400
            cmd = [sys.executable, '-m', 'pip', 'install', pkg] + BASE_FLAGS
        else:
            return jsonify({'ok': False, 'error': 'no package specified'}), 400

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                cwd=PROJECT_ROOT,
            )
            output = (result.stdout + result.stderr).strip()
            ok     = result.returncode == 0
            return jsonify({
                'ok':         ok,
                'returncode': result.returncode,
                'python':     sys.executable,
                'output':     output[-4000:],
            })
        except subprocess.TimeoutExpired:
            return jsonify({'ok': False, 'error': 'timeout', 'output': 'Установка превысила 300 сек'})
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)})
