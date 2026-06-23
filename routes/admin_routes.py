# ╔══════════════════════════════════════════════════════════════╗
# ║                      admin_routes.py                          ║
# ║  v2.8: уведомление пользователю при изменении прав доступа    ║
# ║  v2.9: /admin дашборд, /admin/deps, /api/deps/check|install   ║
# ║  v3.0: fix deps/install — WinPython-совместимость             ║
# ║  v3.1: fix syntax — убран мусор 'raktika:' в classifiers()    ║
# ║  v3.2: fix deps/check — маппинг import-имён для pip-пакетов    ║
# ║  v3.3: fix _IMPORT_NAME — убран дубль Pillow, добавлен pystray ║
# ║  v3.4: fix impersonate — загрузка perm_* цели; rm manager;    ║
# ║         ADMIN_PERMISSIONS вместо инлайн dict comprehension     ║
# ║  v3.5: audit — log_action('perm_change') в edit_permissions;  ║
# ║         get_perm_audit() передаётся в users.html              ║
# ║  v3.6: action edit_name — редактирование ФИО пользователя     ║
# ║  v3.7: /api/console/show|hide|status — управление             ║
# ║         консолью через браузер (независимо от трея)           ║
# ║  v3.8: #2.2 investmap upload/clear + classifiers() расширен   ║
# ║  v3.9: investmap upload — поддержка CSV (delimiter=';')       ║
# ║  v4.0: fix investmap upload — display_name вместо field_name  ║
# ║  v4.1: fix CSV encoding — автодетект utf-8-sig/cp1251/utf-8   ║
# ║  v4.2: fix g.user → session[user_id] в investmap clear/upload ║
# ║         пропуск строк с признаком 'Удалён' в CSV-парсере      ║
# ║  v4.3: logging — err_logger.exception() в investmap upload;   ║
# ║         раздельные except для UnicodeDecodeError/ValueError   ║
# ║  v4.4: fix field_name=None → fallback 'classifier_{num}'      ║
# ║         при отсутствии записи в investmap_fields              ║
# ║  v4.5: investmap_classifier_upload_ajax — POST /upload/<num>  ║
# ║         AJAX-эндпоинт для массовой загрузки из JS (→ JSON)    ║
# ║  v4.6: ДЕКОМПОЗИЦИЯ — роуты вынесены в admin_deps.py,         ║
# ║         admin_classifiers.py, admin_filters.py.              ║
# ║         Blueprint admin_bp остаётся здесь; submodules        ║
# ║         навешивают роуты через register(admin_bp), поэтому   ║
# ║         endpoint-имена (admin.*) и url_for не меняются.      ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify

from db import get_db
from core.auth_utils import (
    login_required, admin_required, hash_pw,
    ALL_PERMISSIONS, ADMIN_PERMISSIONS, load_permissions_to_session,
)
from core.activity_log import get_activity_log, get_perm_audit, ACTION_LABELS, log_action

# Подмодули декомпозиции
from routes import admin_deps
from routes import admin_classifiers
from routes import admin_filters

admin_bp = Blueprint('admin', __name__)

# Навесить роуты из подмодулей на общий admin_bp.
# Так endpoint-имена (admin.classifiers, admin.saved_filters, …) и url_for сохраняются.
admin_deps.register(admin_bp)
admin_classifiers.register(admin_bp)
admin_filters.register(admin_bp)


# ─── Имперсонация ──────────────────────────────────────────────────────────────────────────────────────────────────
@admin_bp.route('/impersonate/<int:uid>')
@login_required
@admin_required
def impersonate(uid):
    conn = get_db()
    try:
        target = conn.execute(
            'SELECT * FROM users WHERE id=?', (uid,)
        ).fetchone()
    finally:
        conn.close()

    if not target:
        flash('Пользователь не найден', 'error')
        return redirect(url_for('requests.index'))

    if not session.get('_orig_user_id'):
        session['_orig_user_id']   = session['user_id']
        session['_orig_username']  = session.get('username', '')
        session['_orig_full_name'] = session.get('full_name', '')
        session['_orig_role']      = session.get('role', '')
        for key in ALL_PERMISSIONS:
            session[f'_orig_perm_{key}'] = session.get(f'perm_{key}', 0)

    session['user_id']   = target['id']
    session['username']  = target['username']
    session['full_name'] = target['full_name']
    session['role']      = target['role']
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
    for key in ALL_PERMISSIONS:
        session[f'perm_{key}'] = session.pop(f'_orig_perm_{key}', 1)
    session.modified = True

    flash('Вы вернулись в свою учётную запись администратора.', 'success')
    return redirect(url_for('requests.index'))


# ─── Управление пользователями ─────────────────────────────────────────────────────────────────────────────────────
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

            elif action == 'edit_name':
                uid = request.form.get('user_id')
                fn  = request.form.get('full_name', '').strip()
                if uid and fn:
                    conn.execute(
                        'UPDATE users SET full_name=? WHERE id=?',
                        (fn, uid)
                    )
                    conn.commit()
                    flash('ФИО обновлено', 'success')
                else:
                    flash('ФИО не может быть пустым', 'error')

            elif action == 'edit_permissions':
                uid = request.form.get('user_id')
                ro  = request.form.get('role', 'employee')

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

                conn.execute(
                    "INSERT INTO notifications (user_id, message) VALUES (?, ?)",
                    (uid, '🔐 Ваши права доступа были изменены администратором')
                )
                conn.commit()

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


# ─── /api/console ─────────────────────────────────────────────────────────────────────────────────────────────────────────
@admin_bp.route('/api/console/status')
@login_required
@admin_required
def api_console_status():
    try:
        from tray import get_console_visible
        visible = get_console_visible()
        return jsonify({'ok': True, 'visible': visible})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@admin_bp.route('/api/console/show', methods=['POST'])
@login_required
@admin_required
def api_console_show():
    try:
        from tray import show_console
        ok = show_console()
        return jsonify({'ok': ok, 'visible': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


@admin_bp.route('/api/console/hide', methods=['POST'])
@login_required
@admin_required
def api_console_hide():
    try:
        from tray import hide_console
        ok = hide_console()
        return jsonify({'ok': ok, 'visible': False})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})
