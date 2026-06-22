# ╔══════════════════════════════════════════════════════════════╗
# ║ preview_routes.py                                            ║
# ║ API-превью обращения для hover-popover (фича #6)             ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import Blueprint, jsonify, session
from db import get_db
from core.auth_utils import login_required

preview_bp = Blueprint('preview', __name__)

STATUS_LABELS = {
    'draft':    'Черновик',
    'review':   'На проверке',
    'accepted': 'В работе',
    'answered': 'Отвечено',
}


@preview_bp.route('/api/request/<int:rid>/preview')
@login_required
def request_preview(rid):
    """
    Возвращает JSON-превью обращения для Bootstrap Popover.
    Поля: id, number, applicant, status, status_label,
          contact_person, contact_phone, updated_at
    """
    conn = get_db()
    row = conn.execute(
        """
        SELECT r.id,
               r.request_number,
               COALESCE(r.applicant_short_name, r.applicant_full_name, '—') AS applicant,
               r.status,
               r.contact_person,
               r.contact_phone,
               r.contact_email,
               r.updated_at
        FROM requests r
        WHERE r.id = ?
        """,
        (rid,)
    ).fetchone()
    conn.close()

    if not row:
        return jsonify({'error': 'not found'}), 404

    return jsonify({
        'id':           row['id'],
        'number':       row['request_number'] or '—',
        'applicant':    row['applicant'],
        'status':       row['status'],
        'status_label': STATUS_LABELS.get(row['status'], row['status'] or '—'),
        'contact':      row['contact_person'] or '—',
        'phone':        row['contact_phone']  or '—',
        'email':        row['contact_email']  or '—',
        'updated_at':   (row['updated_at'] or '—')[:16],   # YYYY-MM-DD HH:MM
    })
