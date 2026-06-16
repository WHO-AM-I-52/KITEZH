# ╔══════════════════════════════════════════════════════════════╗
# ║                      admin_routes.py                         ║
# ║  v2.8: уведомление пользователю при изменении прав доступа   ║
# ║  v2.9: /admin дашборд, /admin/deps, /api/deps/check|install  ║
# ║  v3.0: fix deps/install — WinPython-совместимость              ║
# ║  v3.1: fix syntax — убран мусор 'raktika:' в classifiers()    ║
# ║  v3.2: fix deps/check — маппинг import-имён для pip-пакетов    ║
# ║  v3.3: fix _IMPORT_NAME — убран дубль Pillow, добавлен pystray ║
# ║  v3.4: fix impersonate — загрузка perm_* цели; rm manager;    ║
# ║         ADMIN_PERMISSIONS вместо инлайн dict comprehension    ║
# ║  v3.5: audit — log_action('perm_change') в edit_permissions;  ║
# ║         get_perm_audit() передаётся в users.html              ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
import json
import os
import sys
import subprocess
import importlib.util

from db import get_db
from auth_utils import (
    login_required, admin_required, hash_pw,
    ALL_PERMISSIONS, ADMIN_PERMISSIONS, load_permissions_to_session,
)
from activity_log import get_activity_log, get_perm_audit, ACTION_LABELS, log_action

admin_bp = Blueprint('admin', __name__)

# requirements.txt лежит рядом с этим файлом
_REQUIREMENTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'requirements.txt')

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


# ─── /admin дашборд ─────────────────────────────────────────────────────────────────────────────────────
@admin_bp.route('/admin')
@login_required
@admin_required
def admin_index():
    return render_template('admin/index.html')


# ─── /admin/deps ─────────────────────────────────────────────────────────────────────────────────────
@admin_bp.route('/admin/deps')
@login_required
@admin_required
def admin_deps():
    return render_template('admin/deps.html')


# ─── /api/deps/check ────────────────────────────────────────────────────────────────────────────────
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

            # Определяем реальное import-имя:
            # если есть в маппинге — берём его, иначе преобразуем дефисные в подчёрки
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


# ─── /api/deps/install ─────────────────────────────────────────────────────────────────────────────
@admin_bp.route('/api/deps/install', methods=['POST'])
@login_required
@admin_required
def api_deps_install():
    """Install a single package or all from requirements.txt.
    Body: {"package": "jellyfish"} or {"all": true}
    """
    data = request.get_json(silent=True) or {}

    # Базовые флаги: без диалогов, совместимо с WinPython
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
            cwd=os.path.dirname(os.path.abspath(__file__)),
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


# ─── Войти как (Имперсонация) ───────────────────────────────────────────────────────────────────────
@admin_bp.route('/impersonate/<int:uid>')
@login_required
@admin_required
def impersonate(uid):
    conn = get_db()
    try:
        # Читаем все колонки — нужны perm_* для load_permissions_to_session
        target = conn.execute(
            'SELECT * FROM users WHERE id=?', (uid,)
        ).fetchone()
    finally:
        conn.close()

    if not target:
        flash('Пользователь не найден', 'error')
        return redirect(url_for('requests.index'))

    if not session.get('_orig_user_id'):
        # Сохраняем идентификаторы оригинального admin-пользователя
        session['_orig_user_id']   = session['user_id']
        session['_orig_username']  = session.get('username', '')
        session['_orig_full_name'] = session.get('full_name', '')
        session['_orig_role']      = session.get('role', '')
        # Сохраняем все perm_* администратора, чтобы восстановить при выходе
        for key in ALL_PERMISSIONS:
            session[f'_orig_perm_{key}'] = session.get(f'perm_{key}', 0)

    session['user_id']   = target['id']
    session['username']  = target['username']
    session['full_name'] = target['full_name']
    session['role']      = target['role']
    # Загружаем права целевого пользователя — без этого perm_* остались бы от admin
    load_permissions_to_session(target)
    session.modified = True

    flash(f'Вы вошли как: {target["full_name"]}. Для выхода нажмите «Вернуться в admin».', 'info')
    return redirect(url_for('requests.index'))


@admin_bp.route('/impersonate/stop')
@login_required
def impersonate_stop():
    orig_id = session.pop('_orig_user_id', None)
    if not orig_id:
        flash('Имперсонация не активна', 'warning')
        return redirect(url_for('requests.index'))

    session['user_id']   = orig_id
    session['username']  = session.pop('_orig_username',  '')
    session['full_name'] = session.pop('_orig_full_name', '')
    session['role']      = session.pop('_orig_role',      'admin')
    # Восстанавливаем perm_* администратора
    for key in ALL_PERMISSIONS:
        session[f'perm_{key}'] = session.pop(f'_orig_perm_{key}', 1)
    session.modified = True

    flash('Вы вернулись в свою учётную запись администратора.', 'success')
    return redirect(url_for('requests.index'))


# ─── Классификаторы ─────────────────────────────────────────────────────────────────────────────────────
@admin_bp.route('/admin/classifiers', methods=['GET', 'POST'])
@login_required
@admin_required
def classifiers():
    conn = get_db()
    try:
        if request.method == 'POST':
            action = request.form.get('action')

            if action == 'add':
                cat = request.form.get('category', '')
                val = request.form.get('value', '').strip()
                if cat and val:
                    conn.execute(
                        "INSERT INTO classifiers (category,value) VALUES (?,?)",
                        (cat, val)
                    )
                    conn.commit()
                    flash('Значение добавлено', 'success')

            elif action == 'delete':
                cid = request.form.get('cid')
                conn.execute("DELETE FROM classifiers WHERE id=?", (cid,))
                conn.commit()
                flash('Значение удалено', 'success')

            elif action == 'rename':
                cid = request.form.get('cid')
                val = request.form.get('value', '').strip()
                if val:
                    conn.execute("UPDATE classifiers SET value=? WHERE id=?", (val, cid))
                    conn.commit()
                    flash('Значение обновлено', 'success')

        lf  = conn.execute(
            "SELECT * FROM classifiers WHERE category='legal_form'  ORDER BY sort_order,value"
        ).fetchall()
        di  = conn.execute(
            "SELECT * FROM classifiers WHERE category='district'     ORDER BY sort_order,value"
        ).fetchall()
        src = conn.execute(
            "SELECT * FROM classifiers WHERE category='source_type'  ORDER BY sort_order,value"
        ).fetchall()

        okved_total = conn.execute("SELECT COUNT(*) FROM okved").fetchone()[0]
        row = conn.execute("SELECT value FROM settings WHERE key='okved_last_sync'").fetchone()
        okved_last_sync = row['value'] if row else '—'

        subject_types = conn.execute("SELECT * FROM subject_types ORDER BY id").fetchall()
        result_types  = conn.execute("SELECT * FROM result_types ORDER BY id").fetchall()
    finally:
        conn.close()

    return render_template(
        'classifiers.html',
        legal_forms=lf, districts=di, source_types=src,
        okved_total=okved_total, okved_last_sync=okved_last_sync,
        subject_types=subject_types,
        result_types=result_types,
    )


@admin_bp.route('/admin/subject-types', methods=['POST'])
@login_required
@admin_required
def subject_types_write():
    conn = get_db()
    action = request.form.get('action')
    try:
        if action == 'add':
            name   = request.form.get('name', '').strip()
            prefix = request.form.get('reg_prefix', '').strip().upper()
            if name:
                try:
                    conn.execute(
                        "INSERT INTO subject_types (name, reg_prefix) VALUES (?, ?)",
                        (name, prefix or None)
                    )
                    conn.commit()
                    flash(f'Предмет «{name}» добавлен' + (f', префикс: {prefix}' if prefix else ', префикс не задан (будет БП)'), 'success')
                except Exception:
                    conn.rollback()
                    flash('Такой предмет уже есть', 'error')

        elif action == 'rename':
            sid    = request.form.get('sid')
            name   = request.form.get('name', '').strip()
            prefix = request.form.get('reg_prefix', '').strip().upper()
            if name:
                try:
                    conn.execute(
                        "UPDATE subject_types SET name=?, reg_prefix=? WHERE id=?",
                        (name, prefix or None, sid)
                    )
                    conn.commit()
                    flash('Предмет обновлён', 'success')
                except Exception:
                    conn.rollback()
                    flash('Такое название уже существует', 'error')

        elif action == 'delete':
            sid = request.form.get('sid')
            conn.execute(
                "UPDATE requests SET subject_type_id=NULL WHERE subject_type_id=?", (sid,)
            )
            conn.execute("DELETE FROM subject_types WHERE id=?", (sid,))
            conn.commit()
            flash('Предмет удалён', 'success')
    finally:
        conn.close()

    return redirect(url_for('admin.classifiers') + '#tab-subject')


@admin_bp.route('/admin/result-types', methods=['POST'])
@login_required
@admin_required
def result_types_write():
    conn   = get_db()
    action = request.form.get('action')
    try:
        if action == 'add':
            name  = request.form.get('name', '').strip()
            color = request.form.get('color_hex', 'FFFFFF').strip().lstrip('#').upper()
            if name:
                try:
                    conn.execute(
                        "INSERT INTO result_types (name, color_hex) VALUES (?, ?)",
                        (name, color)
                    )
                    conn.commit()
                    flash(f'Итог «{name}» добавлен', 'success')
                except Exception:
                    conn.rollback()
                    flash('Такой итог уже есть', 'error')

        elif action == 'edit':
            rid   = request.form.get('rid')
            name  = request.form.get('name', '').strip()
            color = request.form.get('color_hex', 'FFFFFF').strip().lstrip('#').upper()
            if name:
                conn.execute(
                    "UPDATE result_types SET name=?, color_hex=? WHERE id=?",
                    (name, color, rid)
                )
                conn.commit()
                flash('Итог обновлён', 'success')

        elif action == 'delete':
            rid = request.form.get('rid')
            conn.execute(
                "UPDATE requests SET result_type_id=NULL WHERE result_type_id=?", (rid,)
            )
            cur = conn.execute("DELETE FROM result_types WHERE id=?", (rid,))
            conn.commit()
            if cur.rowcount:
                flash('Итог удалён', 'success')
            else:
                flash('Итог не найден или уже удалён', 'warning')

        elif action == 'bulk_delete':
            raw_ids = (request.form.get('selected_ids') or '').strip()
            ids = [x for x in raw_ids.split(',') if x.isdigit()]
            if ids:
                placeholders = ','.join(['?'] * len(ids))
                conn.execute(
                    f"UPDATE requests SET result_type_id=NULL WHERE result_type_id IN ({placeholders})",
                    ids
                )
                cur = conn.execute(
                    f"DELETE FROM result_types WHERE id IN ({placeholders})",
                    ids
                )
                conn.commit()
                flash(f'Удалено итогов: {cur.rowcount}', 'success')
            else:
                flash('Не выбраны итоги для удаления', 'warning')
    finally:
        conn.close()

    return redirect(url_for('admin.classifiers') + '#tab-result')


@admin_bp.route('/admin/result-types/inline', methods=['GET', 'POST'])
@login_required
@admin_required
def result_types_inline():
    conn = get_db()
    try:
        if request.method == 'GET':
            rows = conn.execute(
                "SELECT id, name, color_hex FROM result_types ORDER BY id"
            ).fetchall()
            return jsonify([dict(r) for r in rows])

        data   = request.get_json(silent=True) or {}
        action = data.get('action')
        rid    = data.get('id')

        if action == 'rename':
            name = (data.get('name') or '').strip()
            if not name:
                return jsonify({'error': 'Название не может быть пустым'}), 400
            try:
                conn.execute("UPDATE result_types SET name=? WHERE id=?", (name, rid))
                conn.commit()
            except Exception:
                conn.rollback()
                return jsonify({'error': 'Такое название уже существует'}), 409
            row = conn.execute(
                "SELECT id, name, color_hex FROM result_types WHERE id=?", (rid,)
            ).fetchone()
            return jsonify({'ok': True, 'item': dict(row)})

        if action == 'edit_color':
            color = (data.get('color_hex') or 'FFFFFF').strip().lstrip('#').upper()
            if len(color) not in (6, 8):
                return jsonify({'error': 'Некорректный цвет'}), 400
            conn.execute("UPDATE result_types SET color_hex=? WHERE id=?", (color, rid))
            conn.commit()
            row = conn.execute(
                "SELECT id, name, color_hex FROM result_types WHERE id=?", (rid,)
            ).fetchone()
            return jsonify({'ok': True, 'item': dict(row)})

        return jsonify({'error': 'Неизвестное действие'}), 400
    finally:
        conn.close()


@admin_bp.route('/admin/users', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_users():
    conn = get_db()
    try:
        if request.method == 'POST':
            action = request.form.get('action')

            if action == 'add':
                un  = request.form.get('username', '').strip()
                pw2 = request.form.get('password', '').strip()
                fn  = request.form.get('full_name', '').strip()
                ro  = request.form.get('role', 'employee')
                mcp = 1 if request.form.get('must_change_password') else 0

                if un and pw2 and fn:
                    perms = {k: (1 if request.form.get(k) else 0) for k in ALL_PERMISSIONS}
                    if ro == 'admin':
                        perms = ADMIN_PERMISSIONS.copy()
                    try:
                        conn.execute(
                            f"INSERT INTO users "
                            f"(username,password,full_name,role,must_change_password,"
                            f"{','.join(ALL_PERMISSIONS)}) "
                            f"VALUES (?,?,?,?,?,{','.join(['?']*len(ALL_PERMISSIONS))})",
                            [un, hash_pw(pw2), fn, ro, mcp] + [perms[k] for k in ALL_PERMISSIONS]
                        )
                        conn.commit()
                        flash(f'Пользователь {un} добавлен', 'success')
                    except Exception:
                        conn.rollback()
                        flash('Логин уже занят', 'error')

            elif action == 'edit_permissions':
                uid = request.form.get('user_id')
                ro  = request.form.get('role', 'employee')

                # Читаем текущие права ДО изменения — для формирования diff
                old = conn.execute(
                    f"SELECT role, {','.join(ALL_PERMISSIONS)} FROM users WHERE id=?", (uid,)
                ).fetchone()

                new_perms = {k: (1 if request.form.get(k) else 0) for k in ALL_PERMISSIONS}
                if ro == 'admin':
                    new_perms = ADMIN_PERMISSIONS.copy()

                sets = ', '.join([f"{k}=?" for k in ALL_PERMISSIONS])
                conn.execute(
                    f"UPDATE users SET role=?, {sets} WHERE id=?",
                    [ro] + [new_perms[k] for k in ALL_PERMISSIONS] + [uid]
                )
                conn.commit()

                # Уведомление пользователю
                conn.execute(
                    "INSERT INTO notifications (user_id, message) VALUES (?, ?)",
                    (uid, '🔐 Ваши права доступа были изменены администратором')
                )
                conn.commit()

                # Аудит: формируем строку изменений «было → стало»
                target_row = conn.execute(
                    "SELECT full_name FROM users WHERE id=?", (uid,)
                ).fetchone()
                target_name = target_row['full_name'] if target_row else f'id={uid}'

                diff_parts = []
                if old['role'] != ro:
                    diff_parts.append(f"роль: {old['role']}→{ro}")
                for k in ALL_PERMISSIONS:
                    was = int(old[k] or 0)
                    now = new_perms[k]
                    if was != now:
                        diff_parts.append(f"{'+ ' if now else '- '}{k}")

                detail = f"[{target_name}] " + ('; '.join(diff_parts) if diff_parts else 'без изменений')
                log_action(conn, session['user_id'], 'perm_change', detail=detail)
                conn.commit()

                flash('Права обновлены', 'success')

            elif action == 'delete':
                uid = request.form.get('user_id')
                if str(uid) != str(session['user_id']):
                    conn.execute("DELETE FROM users WHERE id=?", (uid,))
                    conn.commit()
                    flash('Пользователь удалён', 'success')
                else:
                    flash('Нельзя удалить себя', 'error')

            elif action == 'change_password':
                uid = request.form.get('user_id')
                np2 = request.form.get('new_password', '').strip()
                mcp = 1 if request.form.get('must_change_password') else 0
                if np2:
                    conn.execute(
                        "UPDATE users SET password=?, must_change_password=? WHERE id=?",
                        (hash_pw(np2), mcp, uid)
                    )
                    conn.commit()
                    flash('Пароль изменён', 'success')

        users = conn.execute(
            "SELECT * FROM users ORDER BY role, full_name"
        ).fetchall()

        login_log = conn.execute(
            "SELECT * FROM login_log ORDER BY id DESC LIMIT 50"
        ).fetchall()

        af_user   = request.args.get('af_user', '')
        af_action = request.args.get('af_action', '')
        af_date   = request.args.get('af_date', '')

        activity = get_activity_log(
            limit=200,
            user_id=int(af_user) if af_user else None,
            action=af_action or None,
            date_from=af_date or None,
        )

        perm_audit = get_perm_audit(limit=200)

        return render_template(
            'users.html',
            users=users,
            login_log=login_log,
            activity=activity,
            perm_audit=perm_audit,
            action_labels=ACTION_LABELS,
            af_user=af_user,
            af_action=af_action,
            af_date=af_date,
        )
    finally:
        conn.close()


def _build_filter_query(p):
    q = "SELECT COUNT(*) FROM requests r WHERE 1=1"
    params = []
    has_like = False

    if p.get('status'):
        q += " AND r.status=?"
        params.append(p['status'])
    if p.get('date_from'):
        q += " AND r.request_date>=?"
        params.append(p['date_from'])
    if p.get('date_to'):
        q += " AND r.request_date<=?"
        params.append(p['date_to'])
    if p.get('employee'):
        q += " AND r.assigned_to=?"
        params.append(p['employee'])
    if p.get('site_type_free') == '1':
        q += " AND r.site_type_free=1"
    if p.get('site_type_existing') == '1':
        q += " AND r.site_type_existing=1"
    if p.get('area_min'):
        q += " AND r.site_area_ha>=?"
        params.append(float(p['area_min']))
    if p.get('area_max'):
        q += " AND r.site_area_ha<=?"
        params.append(float(p['area_max']))
    if p.get('build_min'):
        q += " AND r.site_build_area_m2>=?"
        params.append(float(p['build_min']))
    if p.get('build_max'):
        q += " AND r.site_build_area_m2<=?"
        params.append(float(p['build_max']))
    if p.get('inv_min'):
        q += " AND r.investment_total>=?"
        params.append(float(p['inv_min']))
    if p.get('inv_max'):
        q += " AND r.investment_total<=?"
        params.append(float(p['inv_max']))

    if p.get('applicant'):
        q += " AND (r.applicant_full_name LIKE ? OR r.applicant_short_name LIKE ?)"
        params += [f"%{p['applicant']}%"] * 2
        has_like = True
    if p.get('district'):
        q += " AND r.preferred_districts LIKE ?"
        params.append(f"%{p['district']}%")
        has_like = True

    return q, params, has_like


@admin_bp.route('/saved-filters', methods=['GET', 'POST'])
@login_required
def saved_filters():
    conn = get_db()
    try:
        if request.method == 'POST':
            action = request.form.get('action')

            def get_params():
                return {
                    'status':             request.form.get('f_status', ''),
                    'date_from':          request.form.get('f_date_from', ''),
                    'date_to':            request.form.get('f_date_to', ''),
                    'applicant':          request.form.get('f_applicant', ''),
                    'employee':           request.form.get('f_employee', ''),
                    'period':             request.form.get('f_period', 'all'),
                    'site_type_free':     request.form.get('f_site_type_free', ''),
                    'site_type_existing': request.form.get('f_site_type_existing', ''),
                    'area_min':           request.form.get('f_area_min', ''),
                    'area_max':           request.form.get('f_area_max', ''),
                    'build_min':          request.form.get('f_build_min', ''),
                    'build_max':          request.form.get('f_build_max', ''),
                    'inv_min':            request.form.get('f_inv_min', ''),
                    'inv_max':            request.form.get('f_inv_max', ''),
                    'district':           request.form.get('f_district', ''),
                }

            if action == 'add':
                name = request.form.get('name', '').strip()
                desc = request.form.get('description', '').strip()
                if name:
                    conn.execute(
                        "INSERT INTO saved_filters (name,description,params,created_by) "
                        "VALUES (?,?,?,?)",
                        (name, desc, json.dumps(get_params(), ensure_ascii=False), session['user_id'])
                    )
                    conn.commit()
                    flash(f'Фильтр «{name}» сохранён', 'success')

            elif action == 'delete':
                conn.execute(
                    "DELETE FROM saved_filters WHERE id=?",
                    (request.form.get('fid'),)
                )
                conn.commit()
                flash('Фильтр удалён', 'success')

            elif action == 'edit':
                fid  = request.form.get('fid')
                name = request.form.get('name', '').strip()
                desc = request.form.get('description', '').strip()
                conn.execute(
                    "UPDATE saved_filters SET name=?,description=?,params=? WHERE id=?",
                    (name, desc, json.dumps(get_params(), ensure_ascii=False), fid)
                )
                conn.commit()
                flash('Фильтр обновлён', 'success')

            return redirect(url_for('admin.saved_filters'))

        rows = conn.execute(
            "SELECT sf.*,u.full_name FROM saved_filters sf "
            "LEFT JOIN users u ON sf.created_by=u.id "
            "ORDER BY sf.sort_order,sf.id"
        ).fetchall()
        # manager убран — роль не определена в системе прав
        employees = conn.execute(
            "SELECT id,full_name FROM users WHERE role IN ('employee','admin') "
            "ORDER BY full_name"
        ).fetchall()
        districts = [
            r['value'] for r in conn.execute(
                "SELECT value FROM classifiers WHERE category='district' ORDER BY value"
            ).fetchall()
        ]

        parsed = {}
        for row in rows:
            try:
                parsed[row['id']] = json.loads(row['params'])
            except Exception:
                parsed[row['id']] = {}

        batch_ids  = []
        single_ids = []
        for row in rows:
            _, _, has_like = _build_filter_query(parsed[row['id']])
            if has_like:
                single_ids.append(row['id'])
            else:
                batch_ids.append(row['id'])

        counts = {}

        if batch_ids:
            union_parts  = []
            union_params = []
            for fid in batch_ids:
                q, params, _ = _build_filter_query(parsed[fid])
                q_with_fid = q.replace(
                    "SELECT COUNT(*) FROM requests r WHERE 1=1",
                    f"SELECT {fid} AS fid, COUNT(*) AS cnt FROM requests r WHERE 1=1",
                    1
                )
                union_parts.append(q_with_fid)
                union_params.extend(params)
            union_sql = " UNION ALL ".join(union_parts)
            for batch_row in conn.execute(union_sql, union_params).fetchall():
                counts[batch_row[0]] = batch_row[1]

        for fid in single_ids:
            q, params, _ = _build_filter_query(parsed[fid])
            try:
                counts[fid] = conn.execute(q, params).fetchone()[0]
            except Exception:
                counts[fid] = 0

        fwc = []
        for row in rows:
            fwc.append({
                'row':    row,
                'params': parsed[row['id']],
                'count':  counts.get(row['id'], 0),
                'qs':     '',
            })

        return render_template(
            'saved_filters.html',
            items=fwc, employees=employees, districts=districts
        )
    finally:
        conn.close()


@admin_bp.route('/saved-filters/<int:fid>/apply')
@login_required
def apply_saved_filter(fid):
    from urllib.parse import urlencode
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM saved_filters WHERE id=?", (fid,)).fetchone()
    finally:
        conn.close()
    if not row:
        flash('Фильтр не найден', 'error')
        return redirect(url_for('requests.index'))
    try:
        p = json.loads(row['params'])
    except Exception:
        p = {}
    qs = {k: v for k, v in p.items() if v}
    qs['active_filter'] = fid
    return redirect(url_for('requests.index') + '?' + urlencode(qs))
