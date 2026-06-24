# tasks/routes.py — Blueprint «Задачи» (MVP, карточка #9)
from flask import Blueprint, render_template, request, redirect, url_for, session, abort
from datetime import datetime
from db import get_db
from activity_log import log_action
from functools import wraps

tasks_bp = Blueprint('tasks', __name__, url_prefix='/tasks',
                     template_folder='templates/tasks')

# ─── Допустимые переходы статусов ──────────────────────────────────────
ALLOWED_TRANSITIONS = {
    'new':         ['in_progress', 'cancelled'],
    'in_progress': ['review', 'cancelled'],
    'review':      ['done', 'in_progress', 'cancelled'],
    'done':        [],
    'cancelled':   [],
}

STATUS_LABELS = {
    'new':         'Новая',
    'in_progress': 'В работе',
    'review':      'На проверке',
    'done':        'Выполнено',
    'cancelled':   'Отменено',
}


# ─── Декоратор ──────────────────────────────────────────────────────────────────
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


# ─── РОУТЫ ─────────────────────────────────────────────────────────────────────

@tasks_bp.route('/my')
@login_required
def my_tasks():
    user_id = session['user_id']
    status_filter = request.args.get('status', '')
    db = get_db()
    query = '''
        SELECT t.*,
               u.full_name AS creator_name
        FROM tasks t
        JOIN task_assignees ta ON ta.task_id = t.id
        LEFT JOIN users u ON u.id = t.created_by
        WHERE ta.user_id = ?
    '''
    params = [user_id]
    if status_filter:
        query += ' AND t.status = ?'
        params.append(status_filter)
    query += ' ORDER BY t.deadline ASC, t.created_at DESC'
    tasks = db.execute(query, params).fetchall()

    # Исполнители для каждой задачи
    assignees_map = {}
    for t in tasks:
        rows = db.execute('''
            SELECT u.full_name FROM task_assignees ta
            JOIN users u ON u.id = ta.user_id
            WHERE ta.task_id = ?
        ''', (t['id'],)).fetchall()
        assignees_map[t['id']] = [r['full_name'] for r in rows]

    return render_template(
        'my.html',
        tasks=tasks,
        assignees_map=assignees_map,
        status_filter=status_filter,
        STATUS_LABELS=STATUS_LABELS,
    )


@tasks_bp.route('/assigned-by-me')
@login_required
def assigned_by_me():
    user_id = session['user_id']
    status_filter = request.args.get('status', '')
    db = get_db()
    query = '''
        SELECT t.*,
               u.full_name AS creator_name
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
        rows = db.execute('''
            SELECT u.full_name FROM task_assignees ta
            JOIN users u ON u.id = ta.user_id
            WHERE ta.task_id = ?
        ''', (t['id'],)).fetchall()
        assignees_map[t['id']] = [r['full_name'] for r in rows]

    return render_template(
        'assigned_by.html',
        tasks=tasks,
        assignees_map=assignees_map,
        status_filter=status_filter,
        STATUS_LABELS=STATUS_LABELS,
    )


@tasks_bp.route('/create', methods=['POST'])
@login_required
def create_task():
    user_id = session['user_id']
    title = request.form.get('title', '').strip()
    if not title:
        abort(400)
    description  = request.form.get('description', '').strip()
    source       = request.form.get('source', '').strip()
    deadline     = request.form.get('deadline', '').strip() or None
    assignee_ids = request.form.getlist('assignee_ids[]')
    now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')

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

    db.commit()
    log_action(user_id, 'task_create', task_id)
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
            '''
            UPDATE tasks SET title=?, description=?, deadline=?, source=?
            WHERE id=?
            ''',
            (title, description, deadline, source, id)
        )
        db.commit()
        log_action(user_id, 'task_edit', id)
        return redirect(url_for('tasks.task_detail', id=id))

    # GET
    assignees = db.execute('''
        SELECT u.id, u.full_name FROM task_assignees ta
        JOIN users u ON u.id = ta.user_id
        WHERE ta.task_id = ?
    ''', (id,)).fetchall()
    all_users = db.execute(
        'SELECT id, full_name FROM users ORDER BY full_name'
    ).fetchall()
    creator = db.execute(
        'SELECT full_name FROM users WHERE id = ?', (task['created_by'],)
    ).fetchone()

    can_edit          = _can_edit(task, user_id, role)
    can_change_status = _can_change_status(task, assignee_ids, user_id, role)
    can_assign        = _can_assign(task, user_id, role)
    can_delete        = role == 'admin'
    next_statuses     = ALLOWED_TRANSITIONS.get(task['status'], [])

    return render_template(
        'detail.html',
        task=task,
        assignees=assignees,
        all_users=all_users,
        creator=creator,
        can_edit=can_edit,
        can_change_status=can_change_status,
        can_assign=can_assign,
        can_delete=can_delete,
        next_statuses=next_statuses,
        STATUS_LABELS=STATUS_LABELS,
    )


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
    if new_status not in ALLOWED_TRANSITIONS.get(task['status'], []):
        abort(400)

    db.execute('UPDATE tasks SET status=? WHERE id=?', (new_status, id))
    db.commit()
    log_action(user_id, 'task_status_change', id, detail=new_status)
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

    action    = request.form.get('action', 'done')   # 'done' или 'cancelled'
    new_status = 'done' if action == 'done' else 'cancelled'
    if new_status not in ALLOWED_TRANSITIONS.get(task['status'], []):
        abort(400)

    result   = request.form.get('result', '').strip()
    now      = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')

    db.execute(
        'UPDATE tasks SET status=?, result=?, closed_at=? WHERE id=?',
        (new_status, result, now, id)
    )
    db.commit()
    log_action(user_id, 'task_close', id)
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
    db.commit()
    log_action(user_id, 'task_assign', id)
    return redirect(url_for('tasks.task_detail', id=id))
