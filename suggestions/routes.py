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
    'new':        'Новое',
    'in_roadmap': 'В дорожной карте',
    'rejected':   'Отклонено',
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
    status_filter = request.args.get('status', '')
    db = get_db()
    query = '''
        SELECT s.id, s.comment, s.file_path, s.status, s.created_at,
               s.reviewed_at,
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


def _set_status(id, new_status, action_name):
    if new_status not in ('in_roadmap', 'rejected'):
        abort(400)
    user_id = session['user_id']
    now = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')
    db = get_db()
    row = db.execute('SELECT id FROM suggestions WHERE id = ?', (id,)).fetchone()
    if row is None:
        abort(404)
    db.execute(
        'UPDATE suggestions SET status = ?, reviewed_by = ?, reviewed_at = ? WHERE id = ?',
        (new_status, user_id, now, id)
    )
    log_action(db, user_id, action_name, id)
    db.commit()


@suggestions_bp.route('/<int:id>/roadmap', methods=['POST'])
@admin_required
def to_roadmap(id):
    """Принять предложение в дорожную карту."""
    _set_status(id, 'in_roadmap', 'suggestion_roadmap')
    flash('Предложение принято в дорожную карту.', 'success')
    return redirect(url_for('suggestions.index'))


@suggestions_bp.route('/<int:id>/reject', methods=['POST'])
@admin_required
def reject(id):
    """Отклонить предложение."""
    _set_status(id, 'rejected', 'suggestion_reject')
    flash('Предложение отклонено.', 'success')
    return redirect(url_for('suggestions.index'))
