# suggestions/routes.py — Blueprint «Предложения по улучшению»
import os
import uuid
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, abort, flash,
)
from db import get_db
from core.activity_log import log_action
from core.auth_utils import login_required, admin_required
from paths import UPLOADS_DIR

suggestions_bp = Blueprint(
    'suggestions', __name__, url_prefix='/suggestions',
    template_folder='templates/suggestions',
)

STATUS_LABELS = {
    'new':          'Новое',
    'in_progress':  'В работе',
    'implemented':  'Внедрено в код',
    'rejected':     'Отклонено',
}

SUGGESTIONS_UPLOAD_DIR = os.path.join(UPLOADS_DIR, 'suggestions')
os.makedirs(SUGGESTIONS_UPLOAD_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'txt',
    'jpg', 'jpeg', 'png', 'gif', 'zip', 'rar',
}
MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 МБ


def _allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _save_upload(file_obj):
    if not file_obj or not file_obj.filename:
        return None
    if not _allowed_file(file_obj.filename):
        return None
    ext = file_obj.filename.rsplit('.', 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex}.{ext}"
    save_path = os.path.join(SUGGESTIONS_UPLOAD_DIR, unique_name)
    file_obj.save(save_path)
    return os.path.join('suggestions', unique_name)


def _ensure_commit_url_column():
    """Автоматическая миграция: добавить колонку commit_url, если её нет."""
    db = get_db()
    cols = [row[1] for row in db.execute('PRAGMA table_info(suggestions)').fetchall()]
    if 'commit_url' not in cols:
        db.execute('ALTER TABLE suggestions ADD COLUMN commit_url TEXT')
        db.commit()


@suggestions_bp.route('/submit', methods=['POST'])
@login_required
def submit():
    """Пользователь отправляет предложение по улучшению (комментарий + файл)."""
    user_id = session['user_id']
    comment = (request.form.get('comment') or '').strip()
    if not comment:
        flash('Комментарий не может быть пустым.', 'error')
        return redirect(request.referrer or url_for('requests.index'))

    file_path = None
    uploaded = request.files.get('file')
    if uploaded and uploaded.filename:
        if not _allowed_file(uploaded.filename):
            flash('Недопустимый тип файла.', 'error')
            return redirect(request.referrer or url_for('requests.index'))
        file_path = _save_upload(uploaded)

    now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
    db = get_db()
    _ensure_commit_url_column()
    cur = db.execute(
        'INSERT INTO suggestions (user_id, comment, file_path, status, created_at) '
        'VALUES (?, ?, ?, ?, ?)',
        (user_id, comment, file_path, 'new', now)
    )
    log_action(db, user_id, 'suggestion_create', cur.lastrowid)
    db.commit()
    flash('Спасибо! Ваше предложение отправлено администратору.', 'success')
    return redirect(request.referrer or url_for('requests.index'))


@suggestions_bp.route('/')
@admin_required
def index():
    """Админ-список всех предложений."""
    _ensure_commit_url_column()
    status_filter = request.args.get('status', '')
    db = get_db()
    query = '''
        SELECT s.id, s.comment, s.file_path, s.status, s.created_at,
               s.reviewed_at, s.commit_url,
               u.full_name  AS author_name,
               ru.full_name AS reviewer_name
        FROM suggestions s
        LEFT JOIN users u  ON u.id  = s.user_id
        LEFT JOIN users ru ON ru.id = s.reviewed_by
    '''
    params = []
    if status_filter in STATUS_LABELS:
        query += ' WHERE s.status = ?'
        params.append(status_filter)
    query += ' ORDER BY s.created_at DESC'
    rows = db.execute(query, params).fetchall()
    return render_template(
        'list.html',
        rows=rows,
        status_filter=status_filter,
        STATUS_LABELS=STATUS_LABELS,
    )


@suggestions_bp.route('/<int:id>/set-status', methods=['POST'])
@admin_required
def set_status(id):
    """Универсальный маршрут смены статуса предложения."""
    new_status = request.form.get('status', '').strip()
    if new_status not in ('in_progress', 'implemented', 'rejected'):
        abort(400)

    commit_url = request.form.get('commit_url', '').strip() or None
    # commit_url обязателен только для статуса implemented
    if new_status == 'implemented' and not commit_url:
        flash('Укажите ссылку на коммит для пометки «Внедрено в код».', 'error')
        return redirect(url_for('suggestions.index'))

    user_id = session['user_id']
    now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
    db = get_db()
    _ensure_commit_url_column()
    row = db.execute('SELECT id FROM suggestions WHERE id = ?', (id,)).fetchone()
    if row is None:
        abort(404)

    db.execute(
        '''
        UPDATE suggestions
        SET status = ?, reviewed_by = ?, reviewed_at = ?, commit_url = ?
        WHERE id = ?
        ''',
        (new_status, user_id, now, commit_url, id)
    )
    log_action(db, user_id, f'suggestion_{new_status}', id)
    db.commit()

    labels = {'in_progress': 'в работу', 'implemented': 'внедренным', 'rejected': 'отклонённым'}
    flash(f'Предложение помечено {labels.get(new_status, new_status)}.', 'success')
    return redirect(url_for('suggestions.index'))
