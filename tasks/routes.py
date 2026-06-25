# tasks/routes.py — Blueprint «Задачи»
import os
import uuid
from flask import Blueprint, render_template, request, redirect, url_for, session, abort, jsonify
from datetime import datetime, date
from db import get_db
from core.activity_log import log_action
from functools import wraps
from paths import UPLOADS_DIR

tasks_bp = Blueprint('tasks', __name__, url_prefix='/tasks',
                     template_folder='templates/tasks')

# Бейджи статусов (для отображения текущего состояния)
STATUS_LABELS = {
    'new':         'Новая',
    'in_progress': 'В работе',
    'review':      'На проверке',
    'done':        'Выполнено',
    'cancelled':   'Отменено',
}

# Метки кнопок перехода (действие, а не текущее состояние)
STATUS_TRANSITION_LABELS = {
    'in_progress': 'В работу',
    'review':      'На проверку',
    'done':        'Выполнить',
    'cancelled':   'Отменить',
    'new':         'Вернуть в новые',
}

REQUEST_STATUS_LABELS = {
    'draft':     'Черновик',
    'new':       'Новое',
    'in_work':   'В работе',
    'pending':   'Ожидает',
    'done':      'Выполнено',
    'closed':    'Закрыто',
    'cancelled': 'Отменено',
}

# Папка для вложений задач
TASKS_UPLOAD_DIR = os.path.join(UPLOADS_DIR, 'tasks')
os.makedirs(TASKS_UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'txt',
    'jpg', 'jpeg', 'png', 'zip', 'rar',
}


def _allowed_file(filename):
    return (
        '.' in filename
        and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def _save_upload(file_obj):
    """Cохраняет файл в uploads/tasks/, возвращает относительный путь для хранения в БД."""
    if not file_obj or not file_obj.filename:
        return None
    if not _allowed_file(file_obj.filename):
        return None
    ext = file_obj.filename.rsplit('.', 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex}.{ext}"
    save_path = os.path.join(TASKS_UPLOAD_DIR, unique_name)
    file_obj.save(save_path)
    return os.path.join('tasks', unique_name)  # относительно от uploads/


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def _get_assignee_ids(task_id, db):
    rows = db.execute(
        'SELECT user_id FROM task_assignees WHERE task_id = ?', (task_id,)
    ).fetchall()
    return [r['user_id'] for r in rows]


def _can_edit(task, user_id, role):
    return task['created_by'] == user_id or role in ('admin', 'manager')


def _can_change_status(task, assignee_ids, user_id, role):
    return (
        user_id in assignee_ids
        or task['created_by'] == user_id
        or role in ('admin', 'manager')
    )


def _can_assign(task, user_id, role):
    return task['created_by'] == user_id or role in ('admin', 'manager')


def _is_self_task(task, assignee_ids, user_id):
    return task['created_by'] == user_id and user_id in assignee_ids


def _get_allowed_transitions(task_status, user_id, role, task_created_by,
                              assignee_ids):
    """
    Динамическая матрица допустимых переходов статусов.

      new         → in_progress  (assignee / creator / admin / manager)
      new         → cancelled    (creator / admin / manager)
      in_progress → review       (assignee / creator / admin / manager)
      in_progress → cancelled    (creator / admin / manager)
      review      → done         (creator / admin / manager)
      review      → in_progress  (assignee / creator / admin / manager)
      review      → cancelled    (creator / admin / manager)
      done        → in_progress  (admin / manager only)
      cancelled   → in_progress  (admin / manager only)
    """
    is_admin_or_manager = role in ('admin', 'manager')
    is_creator          = task_created_by == user_id
    is_assignee         = user_id in assignee_ids
    is_privileged       = is_admin_or_manager or is_creator

    transitions = {
        'new': (
            ['in_progress', 'cancelled'] if is_privileged
            else ['in_progress'] if is_assignee
            else []
        ),
        'in_progress': (
            ['review', 'cancelled'] if is_privileged
            else ['review'] if is_assignee
            else []
        ),
        'review': (
            ['done', 'in_progress', 'cancelled'] if is_privileged
            else ['in_progress'] if is_assignee
            else []
        ),
        'done': (
            ['in_progress'] if is_admin_or_manager
            else []
        ),
        'cancelled': (
            ['in_progress'] if is_admin_or_manager
            else []
        ),
    }
    return transitions.get(task_status, [])


def _add_comment(db, task_id, user_id, event_type, body=None, file_path=None):
    """Helper: записывает событие в task_comments."""
    now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
    db.execute(
        '''
        INSERT INTO task_comments (task_id, user_id, event_type, body, file_path, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ''',
        (task_id, user_id, event_type, body, file_path, now)
    )


# ─── РОУТЫ ───────────────────────────────────────────────────────────────

@tasks_bp.route('/my')
@login_required
def my_tasks():
    user_id       = session['user_id']
    status_filter = request.args.get('status', '')
    type_filter   = request.args.get('type', '')   # 'task' | 'request' | ''
    search        = request.args.get('q', '').strip()
    db = get_db()

    task_query = '''
        SELECT t.id, t.title, t.source, t.deadline, t.status,
               'task' AS item_type, t.created_at
        FROM tasks t
        JOIN task_assignees ta ON ta.task_id = t.id
        WHERE ta.user_id = ?
    '''
    task_params = [user_id]
    if status_filter and type_filter in ('task', ''):
        task_query += ' AND t.status = ?'
        task_params.append(status_filter)
    if search:
        task_query += ' AND (t.title LIKE ? OR t.source LIKE ?)'
        task_params += [f'%{search}%', f'%{search}%']

    req_query = '''
        SELECT r.id,
               COALESCE(r.project_name, r.applicant_short_name,
                        r.applicant_full_name, 'Обращение #' || r.id) AS title,
               r.request_number AS source,
               r.review_deadline AS deadline,
               r.status,
               'request' AS item_type,
               r.created_at
        FROM requests r
        WHERE r.status NOT IN ('done','closed','cancelled')
          AND (r.responsible_id = ? OR r.reviewer_id = ? OR r.assigned_to = ?)
    '''
    req_params = [user_id, user_id, user_id]
    if status_filter and type_filter in ('request', ''):
        req_query += ' AND r.status = ?'
        req_params.append(status_filter)
    if search:
        req_query += ''' AND (
            r.project_name LIKE ? OR r.applicant_short_name LIKE ?
            OR r.applicant_full_name LIKE ? OR r.request_number LIKE ?
        )'''
        req_params += [f'%{search}%'] * 4

    rows = []
    if type_filter in ('task', ''):
        rows += db.execute(task_query, task_params).fetchall()
    if type_filter in ('request', ''):
        rows += db.execute(req_query, req_params).fetchall()

    today_s = date.today().isoformat()

    def sort_key(r):
        dl = r['deadline'] or '9999-99-99'
        overdue = dl < today_s and r['status'] not in ('done', 'cancelled', 'closed')
        return (0 if overdue else 1, dl)

    rows.sort(key=sort_key)

    task_ids = [r['id'] for r in rows if r['item_type'] == 'task']
    assignees_map = {}
    for tid in task_ids:
        arows = db.execute('''
            SELECT u.full_name FROM task_assignees ta
            JOIN users u ON u.id = ta.user_id WHERE ta.task_id = ?
        ''', (tid,)).fetchall()
        assignees_map[tid] = [a['full_name'] for a in arows]

    all_users = db.execute('SELECT id, full_name FROM users ORDER BY full_name').fetchall()

    return render_template(
        'my.html',
        rows=rows,
        assignees_map=assignees_map,
        all_users=all_users,
        status_filter=status_filter,
        type_filter=type_filter,
        search=search,
        STATUS_LABELS=STATUS_LABELS,
        REQUEST_STATUS_LABELS=REQUEST_STATUS_LABELS,
        today=today_s,
    )


@tasks_bp.route('/api/my')
@login_required
def api_my_tasks():
    user_id = session['user_id']
    db = get_db()
    today_s = date.today().isoformat()

    task_rows = db.execute('''
        SELECT t.id, t.title, t.source, t.deadline, t.status, t.created_at
        FROM tasks t
        JOIN task_assignees ta ON ta.task_id = t.id
        WHERE ta.user_id = ?
        ORDER BY t.deadline ASC
    ''', (user_id,)).fetchall()

    req_rows = db.execute('''
        SELECT r.id,
               COALESCE(r.project_name, r.applicant_short_name,
                        r.applicant_full_name, 'Обращение #' || r.id) AS title,
               r.request_number AS source,
               r.review_deadline AS deadline,
               r.status,
               r.created_at
        FROM requests r
        WHERE r.status NOT IN ('done','closed','cancelled')
          AND (r.responsible_id = ? OR r.reviewer_id = ? OR r.assigned_to = ?)
        ORDER BY r.review_deadline ASC
    ''', (user_id, user_id, user_id)).fetchall()

    task_ids = [r['id'] for r in task_rows]
    assignees_map = {}
    for tid in task_ids:
        arows = db.execute('''
            SELECT u.full_name FROM task_assignees ta
            JOIN users u ON u.id = ta.user_id WHERE ta.task_id = ?
        ''', (tid,)).fetchall()
        assignees_map[tid] = [a['full_name'] for a in arows]

    result = []
    for r in task_rows:
        dl = r['deadline'] or ''
        overdue = bool(dl and dl < today_s)
        result.append({
            'id':           r['id'],
            'type':         'task',
            'type_label':   'Задача',
            'title':        r['title'],
            'source':       r['source'] or '',
            'number':       '',
            'deadline':     dl,
            'status':       r['status'],
            'status_label': STATUS_LABELS.get(r['status'], r['status']),
            'executors':    assignees_map.get(r['id'], []),
            'can_open':     True,
            'overdue':      overdue,
        })

    for r in req_rows:
        dl = r['deadline'] or ''
        overdue = bool(dl and dl < today_s)
        result.append({
            'id':           r['id'],
            'type':         'request',
            'type_label':   'Обращение',
            'title':        r['title'],
            'source':       r['source'] or '',
            'number':       r['source'] or '',
            'deadline':     dl,
            'status':       r['status'],
            'status_label': REQUEST_STATUS_LABELS.get(r['status'], r['status']),
            'executors':    [],
            'can_open':     True,
            'overdue':      overdue,
        })

    result.sort(key=lambda x: (0 if x['overdue'] else 1, x['deadline'] or '9999-99-99'))
    return jsonify(result)


@tasks_bp.route('/assigned-by-me')
@login_required
def assigned_by_me():
    user_id       = session['user_id']
    status_filter = request.args.get('status', '')
    db = get_db()
    query = '''
        SELECT t.*, u.full_name AS creator_name
        FROM tasks t
        LEFT JOIN users u ON u.id = t.created_by
        WHERE t.created_by = ?
    '''
    params = [user_id]
    if status_filter:
        query += ' AND t.status = ?'
        params.append(status_filter)
    query += ' ORDER BY t.deadline ASC, t.created_at DESC'
    tasks = db.execute(query, params).fetchall()

    assignees_map = {}
    for t in tasks:
        arows = db.execute('''
            SELECT u.full_name FROM task_assignees ta
            JOIN users u ON u.id = ta.user_id WHERE ta.task_id = ?
        ''', (t['id'],)).fetchall()
        assignees_map[t['id']] = [a['full_name'] for a in arows]

    return render_template(
        'assigned_by.html',
        tasks=tasks,
        assignees_map=assignees_map,
        status_filter=status_filter,
        STATUS_LABELS=STATUS_LABELS,
        today=date.today().isoformat(),
    )


@tasks_bp.route('/create', methods=['POST'])
@login_required
def create_task():
    user_id      = session['user_id']
    title        = request.form.get('title', '').strip()
    if not title:
        abort(400)
    description  = request.form.get('description', '').strip()
    source       = request.form.get('source', '').strip()
    deadline     = request.form.get('deadline', '').strip() or None
    assignee_ids = request.form.getlist('assignee_ids[]')
    now          = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')

    db = get_db()
    cur = db.execute(
        '''
        INSERT INTO tasks (title, description, source, deadline, status,
                           created_by, created_at)
        VALUES (?, ?, ?, ?, 'new', ?, ?)
        ''',
        (title, description, source, deadline, user_id, now)
    )
    task_id = cur.lastrowid

    for uid in assignee_ids:
        try:
            uid = int(uid)
        except (ValueError, TypeError):
            continue
        db.execute(
            '''
            INSERT OR IGNORE INTO task_assignees
                (task_id, user_id, assigned_at, assigned_by)
            VALUES (?, ?, ?, ?)
            ''',
            (task_id, uid, now, user_id)
        )

    # Событие создания в историю
    _add_comment(db, task_id, user_id, 'status_change',
                 body='Задача создана')

    log_action(db, user_id, 'task_create', task_id)
    db.commit()
    return redirect(url_for('tasks.task_detail', id=task_id))


@tasks_bp.route('/<int:id>', methods=['GET', 'POST'])
@login_required
def task_detail(id):
    user_id = session['user_id']
    role    = session.get('role', 'user')
    db = get_db()
    task = db.execute('SELECT * FROM tasks WHERE id = ?', (id,)).fetchone()
    if task is None:
        abort(404)

    assignee_ids = _get_assignee_ids(id, db)

    if request.method == 'POST':
        if not _can_edit(task, user_id, role):
            abort(403)
        title       = request.form.get('title', '').strip() or task['title']
        description = request.form.get('description', task['description'] or '')
        deadline    = request.form.get('deadline', '').strip() or None
        source      = request.form.get('source', task['source'] or '')
        db.execute(
            'UPDATE tasks SET title=?, description=?, deadline=?, source=? WHERE id=?',
            (title, description, deadline, source, id)
        )
        log_action(db, user_id, 'task_edit', id)
        db.commit()
        return redirect(url_for('tasks.task_detail', id=id))

    assignees = db.execute('''
        SELECT u.id, u.full_name FROM task_assignees ta
        JOIN users u ON u.id = ta.user_id WHERE ta.task_id = ?
    ''', (id,)).fetchall()
    all_users = db.execute('SELECT id, full_name FROM users ORDER BY full_name').fetchall()
    creator   = db.execute('SELECT full_name FROM users WHERE id = ?', (task['created_by'],)).fetchone()

    # Лента истории + комментариев
    comments = db.execute('''
        SELECT tc.id, tc.event_type, tc.body, tc.file_path, tc.created_at,
               u.full_name AS author_name, u.id AS author_id
        FROM task_comments tc
        JOIN users u ON u.id = tc.user_id
        WHERE tc.task_id = ?
        ORDER BY tc.created_at ASC
    ''', (id,)).fetchall()

    can_edit          = _can_edit(task, user_id, role)
    can_change_status = _can_change_status(task, assignee_ids, user_id, role)
    can_assign        = _can_assign(task, user_id, role)
    can_delete        = role == 'admin'
    next_statuses     = _get_allowed_transitions(
        task['status'], user_id, role, task['created_by'], assignee_ids
    )
    is_self_task = _is_self_task(task, assignee_ids, user_id)
    can_revise = (
        task['status'] == 'review'
        and (task['created_by'] == user_id or role in ('admin', 'manager'))
    )
    # Можно ли оставлять комментарий — все участники
    can_comment = (
        user_id in assignee_ids
        or task['created_by'] == user_id
        or role in ('admin', 'manager')
    )

    return render_template(
        'detail.html',
        task=task,
        assignees=assignees,
        all_users=all_users,
        creator=creator,
        comments=comments,
        can_edit=can_edit,
        can_change_status=can_change_status,
        can_assign=can_assign,
        can_delete=can_delete,
        can_comment=can_comment,
        next_statuses=next_statuses,
        is_self_task=is_self_task,
        can_revise=can_revise,
        STATUS_LABELS=STATUS_LABELS,
        STATUS_TRANSITION_LABELS=STATUS_TRANSITION_LABELS,
        today=date.today().isoformat(),
    )


@tasks_bp.route('/<int:id>/comment', methods=['POST'])
@login_required
def add_comment(id):
    """POST — добавить комментарий к задаче."""
    user_id = session['user_id']
    role    = session.get('role', 'user')
    db = get_db()
    task = db.execute('SELECT * FROM tasks WHERE id = ?', (id,)).fetchone()
    if task is None:
        abort(404)

    assignee_ids = _get_assignee_ids(id, db)
    can_comment = (
        user_id in assignee_ids
        or task['created_by'] == user_id
        or role in ('admin', 'manager')
    )
    if not can_comment:
        abort(403)

    body = request.form.get('body', '').strip()
    file_path = None

    uploaded = request.files.get('file')
    if uploaded and uploaded.filename:
        file_path = _save_upload(uploaded)

    if not body and not file_path:
        # пустой комментарий без файла — игнорируем
        return redirect(url_for('tasks.task_detail', id=id))

    _add_comment(db, id, user_id, 'comment', body=body, file_path=file_path)
    log_action(db, user_id, 'task_comment', id)
    db.commit()
    return redirect(url_for('tasks.task_detail', id=id))


@tasks_bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete_task(id):
    user_id = session['user_id']
    role    = session.get('role', 'user')
    if role != 'admin':
        abort(403)
    db = get_db()
    task = db.execute('SELECT id FROM tasks WHERE id = ?', (id,)).fetchone()
    if task is None:
        abort(404)
    db.execute('DELETE FROM task_assignees WHERE task_id = ?', (id,))
    db.execute('DELETE FROM task_comments  WHERE task_id = ?', (id,))
    db.execute('DELETE FROM tasks          WHERE id = ?',      (id,))
    log_action(db, user_id, 'task_delete', id)
    db.commit()
    return redirect(url_for('tasks.my_tasks'))


@tasks_bp.route('/<int:id>/status', methods=['POST'])
@login_required
def change_status(id):
    user_id = session['user_id']
    role    = session.get('role', 'user')
    db = get_db()
    task = db.execute('SELECT * FROM tasks WHERE id = ?', (id,)).fetchone()
    if task is None:
        abort(404)

    assignee_ids = _get_assignee_ids(id, db)
    if not _can_change_status(task, assignee_ids, user_id, role):
        abort(403)

    new_status = request.form.get('status', '').strip()
    allowed = _get_allowed_transitions(
        task['status'], user_id, role, task['created_by'], assignee_ids
    )
    if new_status not in allowed:
        abort(400)

    # При переходе in_progress → review через этот роут
    # (review_submit вынесен в reviewModal в шаблоне и роут /review_submit)
    # Здесь простая смена без модалки
    db.execute('UPDATE tasks SET status=? WHERE id=?', (new_status, id))
    _add_comment(db, id, user_id, 'status_change',
                 body=f'Статус изменён: {STATUS_LABELS.get(task["status"])} → {STATUS_LABELS.get(new_status, new_status)}')
    log_action(db, user_id, 'task_status_change', id, detail=new_status)
    db.commit()
    return redirect(url_for('tasks.task_detail', id=id))


@tasks_bp.route('/<int:id>/review-submit', methods=['POST'])
@login_required
def review_submit(id):
    """Перевод задачи in_progress → review с итоговым комментарием + файлом."""
    user_id = session['user_id']
    role    = session.get('role', 'user')
    db = get_db()
    task = db.execute('SELECT * FROM tasks WHERE id = ?', (id,)).fetchone()
    if task is None:
        abort(404)

    assignee_ids = _get_assignee_ids(id, db)
    if not _can_change_status(task, assignee_ids, user_id, role):
        abort(403)

    allowed = _get_allowed_transitions(
        task['status'], user_id, role, task['created_by'], assignee_ids
    )
    if 'review' not in allowed:
        abort(400)

    body = request.form.get('body', '').strip()
    file_path = None
    uploaded = request.files.get('file')
    if uploaded and uploaded.filename:
        file_path = _save_upload(uploaded)

    db.execute('UPDATE tasks SET status=? WHERE id=?', ('review', id))
    _add_comment(db, id, user_id, 'review_submit',
                 body=body or 'Задача передана на проверку',
                 file_path=file_path)
    log_action(db, user_id, 'task_review_submit', id)
    db.commit()
    return redirect(url_for('tasks.task_detail', id=id))


@tasks_bp.route('/<int:id>/close', methods=['POST'])
@login_required
def close_task(id):
    user_id = session['user_id']
    role    = session.get('role', 'user')
    db = get_db()
    task = db.execute('SELECT * FROM tasks WHERE id = ?', (id,)).fetchone()
    if task is None:
        abort(404)

    assignee_ids = _get_assignee_ids(id, db)
    if not _can_change_status(task, assignee_ids, user_id, role):
        abort(403)

    action     = request.form.get('action', 'done')
    new_status = 'done' if action == 'done' else 'cancelled'
    allowed    = _get_allowed_transitions(
        task['status'], user_id, role, task['created_by'], assignee_ids
    )
    if new_status not in allowed:
        abort(400)

    result    = request.form.get('result', '').strip()
    now       = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
    closed_at = now if new_status == 'done' else None
    db.execute(
        'UPDATE tasks SET status=?, result=?, closed_at=? WHERE id=?',
        (new_status, result, closed_at, id)
    )
    body = f'Задача выполнена' if new_status == 'done' else 'Задача отменена'
    if result:
        body += f'. Результат: {result}'
    _add_comment(db, id, user_id, 'status_change', body=body)
    log_action(db, user_id, 'task_close', id)
    db.commit()
    return redirect(url_for('tasks.task_detail', id=id))


@tasks_bp.route('/<int:id>/revise', methods=['POST'])
@login_required
def revise_task(id):
    """Отправить задачу на доработку (review → in_progress) с обязательным комментарием."""
    user_id = session['user_id']
    role    = session.get('role', 'user')
    db = get_db()
    task = db.execute('SELECT * FROM tasks WHERE id = ?', (id,)).fetchone()
    if task is None:
        abort(404)

    if task['status'] != 'review':
        abort(400)
    if not (task['created_by'] == user_id or role in ('admin', 'manager')):
        abort(403)

    comment = request.form.get('revision_comment', '').strip()
    if not comment:
        abort(400)

    db.execute(
        'UPDATE tasks SET status=?, revision_comment=? WHERE id=?',
        ('in_progress', comment, id)
    )
    _add_comment(db, id, user_id, 'revision', body=comment)
    log_action(db, user_id, 'task_revise', id, detail=comment)
    db.commit()
    return redirect(url_for('tasks.task_detail', id=id))


@tasks_bp.route('/<int:id>/assign', methods=['POST'])
@login_required
def assign_user(id):
    user_id = session['user_id']
    role    = session.get('role', 'user')
    db = get_db()
    task = db.execute('SELECT * FROM tasks WHERE id = ?', (id,)).fetchone()
    if task is None:
        abort(404)

    if not _can_assign(task, user_id, role):
        abort(403)

    try:
        new_uid = int(request.form.get('user_id', 0))
    except (ValueError, TypeError):
        abort(400)
    if new_uid <= 0:
        abort(400)

    now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
    db.execute(
        '''
        INSERT OR IGNORE INTO task_assignees
            (task_id, user_id, assigned_at, assigned_by)
        VALUES (?, ?, ?, ?)
        ''',
        (id, new_uid, now, user_id)
    )
    new_user = db.execute('SELECT full_name FROM users WHERE id=?', (new_uid,)).fetchone()
    if new_user:
        _add_comment(db, id, user_id, 'status_change',
                     body=f'Назначен исполнитель: {new_user["full_name"]}')
    log_action(db, user_id, 'task_assign', id)
    db.commit()
    return redirect(url_for('tasks.task_detail', id=id))
