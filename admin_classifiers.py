# ╔══════════════════════════════════════════════════════════════╗
# ║                   admin_classifiers.py                        ║
# ║  Классификаторы, справочники investmap, типы предметов/итогов ║
# ║  Выделено из admin_routes.py (декомпозиция, refactor/structure).║
# ║  register(admin_bp) навешивает роуты на общий Blueprint admin, ║
# ║  поэтому endpoint-имена (admin.*) и url_for сохраняются.       ║
# ╚══════════════════════════════════════════════════════════════╝

import csv
import io

import openpyxl
from flask import render_template, request, redirect, url_for, session, flash, jsonify

from db import get_db
from auth_utils import login_required, admin_required, permission_required
from activity_log import log_action
from kitezh_logger import err_logger


def _investmap_parse_and_insert(conn, f, fname, num):
    """Общая логика парсинга CSV/XLSX и вставки в investmap_classifiers.
    Возвращает количество вставленных строк."""
    field_row = conn.execute(
        "SELECT display_name FROM investmap_fields WHERE classifier_num=? LIMIT 1",
        (num,)
    ).fetchone()
    field_name = (field_row['display_name'] if field_row else None) or f'classifier_{num}'

    inserted = 0

    if fname.endswith('.xlsx'):
        wb = openpyxl.load_workbook(io.BytesIO(f.read()), read_only=True, data_only=True)
        ws = wb.active
        for i, row in enumerate(ws.iter_rows(min_row=2, values_only=True)):
            if not row or row[0] is None:
                continue
            value = str(row[0]).strip()
            if not value:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO investmap_classifiers "
                "(classifier_num, field_name, sort_order, value) VALUES (?, ?, ?, ?)",
                (num, field_name, i, value)
            )
            inserted += 1

    elif fname.endswith('.csv'):
        content = None
        for encoding in ('utf-8-sig', 'cp1251', 'utf-8'):
            try:
                content = f.read().decode(encoding)
                f.seek(0)
                break
            except (UnicodeDecodeError, AttributeError):
                f.seek(0)
                continue
        if content is None:
            raise UnicodeDecodeError('utf-8', b'', 0, 1, 'Не удалось определить кодировку')

        # Автодетект разделителя: ';' или ','
        delimiter = ';' if content.count(';') >= content.count(',') else ','
        reader = csv.reader(io.StringIO(content), delimiter=delimiter)
        next(reader, None)  # пропустить заголовок
        for row in reader:
            if len(row) < 2:
                continue
            if len(row) >= 3 and row[2].strip().strip('"') == 'Удалён':
                continue
            try:
                sort_order = int(row[0].strip().strip('"'))
            except ValueError:
                continue
            value = row[1].strip().strip('"')
            if not value:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO investmap_classifiers "
                "(classifier_num, field_name, sort_order, value) VALUES (?, ?, ?, ?)",
                (num, field_name, sort_order, value)
            )
            inserted += 1

    return inserted


def register(admin_bp):
    """Навесить роуты классификаторов и справочников на admin_bp."""

    # ─── Классификаторы ──────────────────────────────────────────
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

            investmap_cls = conn.execute(
                "SELECT classifier_num, COUNT(*) AS cnt "
                "FROM investmap_classifiers "
                "GROUP BY classifier_num ORDER BY classifier_num"
            ).fetchall()
        finally:
            conn.close()

        return render_template(
            'classifiers.html',
            legal_forms=lf, districts=di, source_types=src,
            okved_total=okved_total, okved_last_sync=okved_last_sync,
            subject_types=subject_types,
            result_types=result_types,
            investmap_classifiers=investmap_cls,
        )

    # ─── Investmap: загрузка справочника (форма, redirect) ───────
    @admin_bp.route('/admin/classifiers/investmap/upload', methods=['POST'])
    @login_required
    @permission_required('can_investmap_rules')
    def investmap_classifier_upload():
        num = request.form.get('classifier_num', '')
        if not num.isdigit():
            flash('Некорректный номер справочника', 'error')
            return redirect(url_for('admin.classifiers') + '#tab-investmap')

        f = request.files.get('file')
        fname = f.filename.lower() if f else ''
        if not f or not (fname.endswith('.xlsx') or fname.endswith('.csv')):
            flash('Необходимо загрузить файл в формате .xlsx или .csv', 'error')
            return redirect(url_for('admin.classifiers') + '#tab-investmap')

        user = session.get('username', f'id={session.get("user_id")}')
        conn = get_db()
        try:
            inserted = _investmap_parse_and_insert(conn, f, fname, str(num))
            conn.commit()
            log_action(conn, session['user_id'], 'investmap_classifier_upload',
                       detail=f'Справочник №{num}: загружено {inserted} значений')
            conn.commit()
            flash(f'Справочник №{num}: загружено {inserted} значений', 'success')
        except UnicodeDecodeError:
            conn.rollback()
            err_logger.exception('investmap upload: encoding error | num=%s file=%s user=%s', num, fname, user)
            flash('Ошибка при загрузке: не удалось распознать кодировку файла.', 'error')
        except ValueError:
            conn.rollback()
            err_logger.exception('investmap upload: ValueError | num=%s file=%s user=%s', num, fname, user)
            flash('Ошибка при загрузке: некорректные данные в файле.', 'error')
        except Exception:
            conn.rollback()
            err_logger.exception('investmap upload: unexpected error | num=%s file=%s user=%s', num, fname, user)
            flash('Ошибка при загрузке: внутренний сбой.', 'error')
        finally:
            conn.close()

        return redirect(url_for('admin.classifiers') + '#tab-investmap')

    # ─── Investmap: AJAX-загрузка справочника (num в URL → JSON) ──
    @admin_bp.route('/admin/classifiers/investmap/upload/<int:num>', methods=['POST'])
    @login_required
    @permission_required('can_investmap_rules')
    def investmap_classifier_upload_ajax(num):
        """AJAX-вариант загрузки справочника: POST /admin/classifiers/investmap/upload/<num>
        Принимает multipart/form-data с полем 'file' (CSV).
        Возвращает JSON: {count: N, error: null} или {count: 0, error: '...'}
        """
        f = request.files.get('file')
        fname = f.filename.lower() if f else ''
        if not f or not (fname.endswith('.xlsx') or fname.endswith('.csv')):
            return jsonify({'count': 0, 'error': 'Поддерживаются только .csv и .xlsx'}), 400

        user = session.get('username', f'id={session.get("user_id")}')
        conn = get_db()
        try:
            inserted = _investmap_parse_and_insert(conn, f, fname, str(num))
            conn.commit()
            log_action(conn, session['user_id'], 'investmap_classifier_upload',
                       detail=f'Справочник №{num} (AJAX): загружено {inserted} значений')
            conn.commit()
            return jsonify({'count': inserted, 'error': None})
        except UnicodeDecodeError:
            conn.rollback()
            err_logger.exception('investmap upload ajax: encoding error | num=%s file=%s user=%s', num, fname, user)
            return jsonify({'count': 0, 'error': 'Не удалось распознать кодировку файла'}), 400
        except ValueError:
            conn.rollback()
            err_logger.exception('investmap upload ajax: ValueError | num=%s file=%s user=%s', num, fname, user)
            return jsonify({'count': 0, 'error': 'Некорректные данные в файле'}), 400
        except Exception:
            conn.rollback()
            err_logger.exception('investmap upload ajax: unexpected error | num=%s file=%s user=%s', num, fname, user)
            return jsonify({'count': 0, 'error': 'Внутренняя ошибка сервера'}), 500
        finally:
            conn.close()

    # ─── Investmap: очистка справочника ──────────────────────────
    @admin_bp.route('/admin/classifiers/investmap/clear/<int:num>', methods=['POST'])
    @login_required
    @permission_required('can_investmap_rules')
    def investmap_classifier_clear(num):
        conn = get_db()
        try:
            conn.execute(
                "DELETE FROM investmap_classifiers WHERE classifier_num=?",
                (num,)
            )
            conn.commit()
            log_action(conn, session['user_id'], 'investmap_classifier_clear',
                       detail=f'Справочник №{num}: все записи удалены')
            conn.commit()
            flash(f'Справочник №{num}: все записи удалены', 'success')
        finally:
            conn.close()

        return redirect(url_for('admin.classifiers') + '#tab-investmap')

    # ─── Предметы обращений ──────────────────────────────────────
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

    # ─── Типы итогов ─────────────────────────────────────────────
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

    # ─── Типы итогов: inline-редактирование (JSON) ───────────────
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
