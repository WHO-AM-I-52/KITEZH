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
# ║  v4.7: backup_download — GET /admin/backup/download?type=     ║
# ║         full|db|rules → ZIP-архив; log_action backup_download ║
# ║  v4.8: ocr_status — GET /admin/ocr-status (перенос из ai)     ║
# ║  v4.9: refactor — шаблон перенесён в templates/admin/         ║
# ║  v5.0: fix backup_download — tmp-файл вместо BytesIO          ║
# ║         (при type=full uploads/ мог переполнить ОЗУ)          ║
# ╚══════════════════════════════════════════════════════════════╝

import atexit
import os
import tempfile
import zipfile
import json
import time
from datetime import datetime

from flask import (
    Blueprint, current_app, render_template, request, redirect, url_for,
    session, flash, jsonify, send_file,
)

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
        return jsonify({
            'ok': True,
            'visible': visible,
        })
    except Exception as e:
        return jsonify({
            'ok': False,
            'error': str(e),
        })


def _console_command_response(command: str):
    """
    Ставит команду в очередь и ждёт короткое время, пока процесс
    run_server.py применит её к реальному окну cmd.exe.
    """
    from tray import get_console_visible, hide_console, show_console

    previous_visible = get_console_visible()

    if command == 'show':
        queued = show_console()
        expected_visible = True
    else:
        queued = hide_console()
        expected_visible = False

    if not queued:
        return jsonify({
            'ok': False,
            'visible': get_console_visible(),
            'error': 'Не удалось передать команду процессу трея.',
        })

    deadline = time.monotonic() + 2.0
    visible = get_console_visible()

    while time.monotonic() < deadline:
        visible = get_console_visible()
        if visible == expected_visible:
            return jsonify({
                'ok': True,
                'visible': visible,
            })
        time.sleep(0.1)

    return jsonify({
        'ok': False,
        'visible': visible,
        'error': (
            'Команда передана, но состояние окна консоли '
            'не изменилось за 2 секунды.'
        ),
    })


@admin_bp.route('/api/console/show', methods=['POST'])
@login_required
@admin_required
def api_console_show():
    try:
        return _console_command_response('show')
    except Exception as e:
        return jsonify({
            'ok': False,
            'visible': False,
            'error': str(e),
        })


@admin_bp.route('/api/console/hide', methods=['POST'])
@login_required
@admin_required
def api_console_hide():
    try:
        return _console_command_response('hide')
    except Exception as e:
        return jsonify({
            'ok': False,
            'visible': False,
            'error': str(e),
        })

# ─── Бэкап ────────────────────────────────────────────────────────────────────
@admin_bp.route('/admin/backup/download')
@login_required
@admin_required
def backup_download():
    """Формирует ZIP-архив и отдаёт его как файл для скачивания.

    Изменение v5.0: вместо io.BytesIO() используется tempfile.NamedTemporaryFile
    на диске — это предотвращает переполнение ОЗУ при больших uploads/ (type=full).
    Временный файл регистрируется в atexit для гарантированной очистки.
    """
    backup_type = request.args.get('type', 'full')
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    db_path     = os.path.join(base_dir, 'database.db')
    rules_path  = os.path.join(base_dir, 'site_field_rules.json')
    uploads_dir = os.path.join(base_dir, 'uploads')

    date_str = datetime.now().strftime('%Y%m%d')

    if backup_type == 'db':
        zip_name     = f'kitezh_db_{date_str}.zip'
        files_to_zip = [(db_path, 'database.db')]
    elif backup_type == 'rules':
        zip_name     = f'kitezh_rules_{date_str}.zip'
        files_to_zip = [(rules_path, 'site_field_rules.json')]
    else:
        zip_name     = f'kitezh_full_{date_str}.zip'
        files_to_zip = [(db_path, 'database.db'), (rules_path, 'site_field_rules.json')]

    # Пишем в tmp-файл на диске, а не в BytesIO в ОЗУ
    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix='.zip',
        prefix='kitezh_backup_',
        dir=tempfile.gettempdir(),
    )
    tmp_path = tmp.name
    tmp.close()

    # Гарантируем удаление tmp-файла при завершении процесса (на случай исключения)
    atexit.register(lambda p=tmp_path: os.path.exists(p) and os.remove(p))

    try:
        with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for abs_path, arc_name in files_to_zip:
                if os.path.exists(abs_path):
                    zf.write(abs_path, arc_name)

            if backup_type == 'full' and os.path.isdir(uploads_dir):
                for root, _, fnames in os.walk(uploads_dir):
                    for fname in fnames:
                        full = os.path.join(root, fname)
                        arc  = os.path.relpath(full, base_dir)
                        zf.write(full, arc)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise

    conn = get_db()
    try:
        log_action(conn, session['user_id'], 'backup_download', detail=backup_type)
        conn.commit()
    finally:
        conn.close()

    return send_file(
        tmp_path,
        mimetype='application/zip',
        as_attachment=True,
        download_name=zip_name,
        max_age=0,
    )


# ─── OCR-СТАТУС (перенесён из ai Blueprint) ───────────────────────────────────
_OCR_INSTALL_WHITELIST_ADMIN = {
    'easyocr':    'easyocr',
    'pdfplumber':  'pdfplumber',
    'docx':        'python-docx',
    'pillow':      'Pillow',
}


def _check_ocr_deps() -> dict:
    status = {}
    for key, mod, attr in [
        ('easyocr',    'easyocr',    '__version__'),
        ('pdfplumber', 'pdfplumber', '__version__'),
        ('docx',       'docx',       '__version__'),
        ('pillow',     'PIL',        '__version__'),
    ]:
        try:
            m = __import__(mod)
            status[key] = {'ok': True, 'version': getattr(m, attr, '—')}
        except ImportError:
            pip_name = _OCR_INSTALL_WHITELIST_ADMIN.get(key, key)
            status[key] = {'ok': False, 'error': f'Не установлен (pip install {pip_name})'}
        except Exception as e:
            status[key] = {'ok': False, 'error': str(e)}
    return status


@admin_bp.route('/admin/ocr-status', methods=['GET'])
@login_required
@admin_required
def ocr_status():
    deps = _check_ocr_deps()
    conn = get_db()
    try:
        errors_7d = conn.execute(
            "SELECT COUNT(*) FROM activity_log "
            "WHERE action='ocr_error' AND created_at >= datetime('now','-7 days')"
        ).fetchone()[0]
        recent_errors = conn.execute(
            "SELECT al.created_at, al.detail, u.full_name "
            "FROM activity_log al LEFT JOIN users u ON al.user_id = u.id "
            "WHERE al.action='ocr_error' ORDER BY al.created_at DESC LIMIT 10"
        ).fetchall()
        recent_errors = [dict(r) for r in recent_errors]
        last_ocr = conn.execute(
            "SELECT created_at FROM activity_log "
            "WHERE action IN ('ocr_upload','ocr_preview') "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        last_ocr_dt = last_ocr['created_at'] if last_ocr else None
        ocr_logs = conn.execute(
            """
            SELECT ol.id, ol.created_at, ol.filename, ol.msg, ol.ok,
                   ol.raw_text, ol.fields_json, u.full_name AS user_name
            FROM ocr_log ol LEFT JOIN users u ON ol.user_id = u.id
            ORDER BY ol.created_at DESC LIMIT 20
            """
        ).fetchall()
        ocr_logs = [dict(r) for r in ocr_logs]
    finally:
        conn.close()
    all_ok = all(v['ok'] for v in deps.values())
    return render_template(
        'admin/ocr_status.html',
        deps=deps,
        errors_7d=errors_7d,
        recent_errors=recent_errors,
        last_ocr_dt=last_ocr_dt,
        all_ok=all_ok,
        checked_at=datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        ocr_logs=ocr_logs,
    )
    # ─── Синхронизация Инвесткарты РФ ─────────────────────────────────────────────

def _sync_plan_action_label(action: str) -> str:
    labels = {
        "create": "создание",
        "start": "запуск",
        "resume": "возобновление",
        "pause": "пауза",
        "stop": "остановка",
        "settings": "изменение настроек",
    }
    return labels.get(action, action)


def _parse_positive_int(value: str | None, field_label: str) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(f"{field_label} должен быть целым числом.") from None

    if parsed < 1:
        raise ValueError(f"{field_label} должен быть больше нуля.")

    return parsed


def _get_sync_plan_overview(conn) -> list[dict]:
    from services.investmap_rf_sync_plans import (
        get_latest_sync_batch,
        get_latest_sync_run,
        get_sync_batches_for_run,
        get_sync_plans,
    )

    overview: list[dict] = []

    for plan in get_sync_plans(conn):
        plan_id = int(plan["id"])
        latest_run = get_latest_sync_run(conn, plan_id)
        failed_batches: list[dict] = []

        if latest_run is not None:
            all_batches = get_sync_batches_for_run(
                conn,
                int(latest_run["id"]),
            )
            failed_batches = [
                batch
                for batch in all_batches
                if int(batch["failed_cards_count"] or 0) > 0
                or str(batch["error_message"] or "").strip()
            ]

        latest_retry_job_row = conn.execute(
            """
            SELECT *
            FROM investmap_rf_sync_retry_jobs
            WHERE plan_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (plan_id,),
        ).fetchone()

        overview.append(
            {
                "plan": plan,
                "latest_run": latest_run,
                "latest_batch": get_latest_sync_batch(conn, plan_id),
                "failed_batches": failed_batches,
                "latest_retry_job": (
                    dict(latest_retry_job_row)
                    if latest_retry_job_row is not None
                    else None
                ),
            }
        )

    return overview


@admin_bp.route("/admin/investmap-rf-sync")
@login_required
@admin_required
def investmap_rf_sync():
    conn = get_db()

    try:
        plans = _get_sync_plan_overview(conn)
        active_cards_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM investmap_rf_monitored_cards
            WHERE is_active = 1
            """
        ).fetchone()["count"]

        return render_template(
            "admin/investmap_rf_sync.html",
            plans=plans,
            active_cards_count=active_cards_count,
        )
    finally:
        conn.close()


@admin_bp.route("/admin/investmap-rf-sync/create", methods=["POST"])
@login_required
@admin_required
def investmap_rf_sync_create():
    from services.investmap_rf_sync_plans import create_sync_plan

    conn = get_db()

    try:
        plan = create_sync_plan(
            conn,
            name=request.form.get("name", ""),
            batch_size=_parse_positive_int(
                request.form.get("batch_size"),
                "Размер пакета",
            ),
            interval_minutes=_parse_positive_int(
                request.form.get("interval_minutes"),
                "Интервал",
            ),
            created_by_user_id=session.get("user_id"),
        )
        log_action(
            conn,
            session.get("user_id"),
            "investmap_rf_sync_create",
            detail=(
                f"plan_id={plan['id']}; "
                f"name={plan['name']}; "
                f"batch_size={plan['batch_size']}; "
                f"interval_minutes={plan['interval_minutes']}"
            ),
        )
        conn.commit()
        flash(f"План «{plan['name']}» создан.", "success")
    except ValueError as exc:
        conn.rollback()
        flash(str(exc), "error")
    except Exception:
        conn.rollback()
        current_app.logger.exception(
            "Ошибка создания плана синхронизации Инвесткарты РФ."
        )
        flash("Не удалось создать план синхронизации.", "error")
    finally:
        conn.close()

    return redirect(url_for("admin.investmap_rf_sync"))

@admin_bp.route(
    "/admin/investmap-rf-sync/<int:plan_id>/action",
    methods=["POST"],
)
@login_required
@admin_required
def investmap_rf_sync_action(plan_id: int):
    from services.investmap_rf_sync_plans import (
        pause_sync_plan,
        request_stop_sync_plan,
        resume_sync_plan,
        start_sync_plan,
    )

    action = request.form.get("action", "").strip()
    conn = get_db()

    try:
        user_id = session.get("user_id")

        if action == "start":
            result = start_sync_plan(
                conn,
                plan_id=plan_id,
                started_by_user_id=user_id,
            )
            plan = result["plan"]
            message = (
                f"План «{plan['name']}» запущен. "
                "Первый пакет будет обработан в течение минуты."
            )
        elif action == "resume":
            result = resume_sync_plan(
                conn,
                plan_id=plan_id,
                updated_by_user_id=user_id,
            )
            plan = result["plan"]
            message = (
                f"План «{plan['name']}» возобновлён. "
                "Следующий пакет будет обработан в течение минуты."
            )
        elif action == "pause":
            plan = pause_sync_plan(
                conn,
                plan_id=plan_id,
                updated_by_user_id=user_id,
            )
            message = f"План «{plan['name']}» приостановлен."
        elif action == "stop":
            plan = request_stop_sync_plan(
                conn,
                plan_id=plan_id,
                updated_by_user_id=user_id,
            )
            message = (
                f"Для плана «{plan['name']}» запрошена мягкая остановка."
            )
        else:
            raise ValueError("Неизвестное действие над планом.")

        log_action(
            conn,
            user_id,
            "investmap_rf_sync_action",
            detail=(
                f"plan_id={plan_id}; "
                f"action={_sync_plan_action_label(action)}; "
                f"status={plan['status']}"
            ),
        )
        conn.commit()
        flash(message, "success")
    except ValueError as exc:
        conn.rollback()
        flash(str(exc), "error")
    except Exception:
        conn.rollback()
        current_app.logger.exception(
            "Ошибка действия над планом синхронизации Инвесткарты РФ. "
            "plan_id=%s; action=%s",
            plan_id,
            action,
        )
        flash("Не удалось выполнить действие над планом.", "error")
    finally:
        conn.close()

    return redirect(url_for("admin.investmap_rf_sync"))

@admin_bp.route(
    "/admin/investmap-rf-sync/<int:plan_id>/retry-failed",
    methods=["POST"],
)
@login_required
@admin_required
def investmap_rf_sync_retry_failed(plan_id: int):
    from services.investmap_rf_sync_plans import (
        create_failed_sync_retry_job,
        get_latest_sync_run,
    )

    conn = get_db()

    try:
        latest_run = get_latest_sync_run(conn, plan_id)
        if latest_run is None:
            raise ValueError("Для плана отсутствует цикл синхронизации.")

        retry_job = create_failed_sync_retry_job(
            conn,
            plan_id=plan_id,
            source_run_id=int(latest_run["id"]),
            created_by_user_id=session.get("user_id"),
        )
        log_action(
            conn,
            session.get("user_id"),
            "investmap_rf_sync_retry_failed",
            detail=(
                f"plan_id={plan_id}; "
                f"source_run_id={latest_run['id']}; "
                f"retry_job_id={retry_job['id']}; "
                f"cards_count={retry_job['requested_cards_count']}"
            ),
        )
        conn.commit()
        flash(
            "Создана задача повтора для "
            f"{retry_job['requested_cards_count']} ошибочных площадок. "
            "Она будет запущена в течение минуты.",
            "success",
        )
    except ValueError as exc:
        conn.rollback()
        flash(str(exc), "error")
    except Exception:
        conn.rollback()
        current_app.logger.exception(
            "Ошибка создания задачи повтора синхронизации Инвесткарты РФ. "
            "plan_id=%s",
            plan_id,
        )
        flash("Не удалось создать задачу повтора синхронизации.", "error")
    finally:
        conn.close()

    return redirect(url_for("admin.investmap_rf_sync"))

@admin_bp.route(
    "/admin/investmap-rf-sync/<int:plan_id>/settings",
    methods=["POST"],
)
@login_required
@admin_required
def investmap_rf_sync_settings(plan_id: int):
    from services.investmap_rf_sync_plans import update_sync_plan_settings

    conn = get_db()

    try:
        plan = update_sync_plan_settings(
            conn,
            plan_id=plan_id,
            batch_size=_parse_positive_int(
                request.form.get("batch_size"),
                "Размер пакета",
            ),
            interval_minutes=_parse_positive_int(
                request.form.get("interval_minutes"),
                "Интервал",
            ),
            updated_by_user_id=session.get("user_id"),
        )
        log_action(
            conn,
            session.get("user_id"),
            "investmap_rf_sync_settings",
            detail=(
                f"plan_id={plan_id}; "
                f"batch_size={plan['batch_size']}; "
                f"interval_minutes={plan['interval_minutes']}"
            ),
        )
        conn.commit()
        flash(f"Настройки плана «{plan['name']}» сохранены.", "success")
    except ValueError as exc:
        conn.rollback()
        flash(str(exc), "error")
    except Exception:
        conn.rollback()
        current_app.logger.exception(
            "Ошибка обновления настроек плана синхронизации "
            "Инвесткарты РФ. plan_id=%s",
            plan_id,
        )
        flash("Не удалось сохранить настройки плана.", "error")
    finally:
        conn.close()

    return redirect(url_for("admin.investmap_rf_sync"))

@admin_bp.route(
    "/admin/investmap-rf-sync/<int:plan_id>/delete",
    methods=["POST"],
)
@login_required
@admin_required
def investmap_rf_sync_delete(plan_id: int):
    """Удаляет неактивный план синхронизации и его техническую историю."""
    from services.investmap_rf_sync_plans import delete_sync_plan

    conn = get_db()

    try:
        result = delete_sync_plan(
            conn,
            plan_id=plan_id,
        )

        detail = (
            f"plan_id={result['plan_id']}; "
            f"name={result['plan_name']}; "
            f"status={result['plan_status']}; "
            f"runs={result['deleted_runs_count']}; "
            f"batches={result['deleted_batches_count']}; "
            f"retry_jobs={result['deleted_retry_jobs_count']}; "
            f"daily_runs={result['deleted_daily_runs_count']}"
        )

        if not log_action(
            conn,
            session.get("user_id"),
            "investmap_rf_sync_delete",
            detail=detail,
        ):
            raise RuntimeError(
                "Не удалось записать действие удаления плана."
            )

        conn.commit()

        flash(
            f"План «{result['plan_name']}» удалён. "
            f"Удалено циклов: {result['deleted_runs_count']}, "
            f"пакетов: {result['deleted_batches_count']}, "
            f"задач повтора: {result['deleted_retry_jobs_count']}.",
            "success",
        )

    except ValueError as exc:
        conn.rollback()
        flash(str(exc), "error")

    except Exception:
        conn.rollback()
        current_app.logger.exception(
            "Ошибка удаления плана синхронизации Инвесткарты РФ. "
            "plan_id=%s",
            plan_id,
        )
        flash("Не удалось удалить план синхронизации.", "error")

    finally:
        conn.close()

    return redirect(url_for("admin.investmap_rf_sync"))
    
@admin_bp.route("/admin/investmap-rf-sync/status")
@login_required
@admin_required
def investmap_rf_sync_status():
    conn = get_db()

    try:
        plans = _get_sync_plan_overview(conn)
        active_cards_count = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM investmap_rf_monitored_cards
            WHERE is_active = 1
            """
        ).fetchone()["count"]

        return jsonify(
            {
                "ok": True,
                "active_cards_count": active_cards_count,
                "plans": plans,
                "server_time_utc": datetime.utcnow().isoformat(
                    timespec="seconds"
                ) + "Z",
            }
        )
    except Exception:
        current_app.logger.exception(
            "Ошибка получения статуса синхронизации Инвесткарты РФ."
        )
        return jsonify(
            {
                "ok": False,
                "error": "Не удалось получить статус синхронизации.",
            }
        ), 500
    finally:
        conn.close()
