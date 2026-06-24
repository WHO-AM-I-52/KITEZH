from flask import (
    Blueprint, render_template, request, redirect,
    url_for, session, jsonify,
)
from datetime import datetime
from db import get_db
from core.activity_log import log_action

letters_bp = Blueprint(
    'letters',
    __name__,
    template_folder='templates',
    url_prefix='/letters',
)


def _login_required():
    return 'user_id' not in session


def _can_edit(letter):
    return (
        letter['created_by'] == session.get('user_id')
        or session.get('role') in ('admin', 'manager')
    )


def _can_delete():
    return session.get('role') == 'admin'


def _can_delete_template(template):
    return (
        template['created_by'] == session.get('user_id')
        or session.get('role') == 'admin'
    )


def _normalize_tag(name: str) -> str:
    return name.lower().strip()


def _get_or_create_tag(conn, name: str) -> int:
    name = _normalize_tag(name)
    row = conn.execute(
        'SELECT id FROM letter_tags WHERE name = ?', (name,)
    ).fetchone()
    if row:
        return row['id']
    cur = conn.execute('INSERT INTO letter_tags (name) VALUES (?)', (name,))
    return cur.lastrowid


def _set_letter_tags(conn, letter_id: int, raw_tags: str):
    conn.execute(
        'DELETE FROM letter_tag_links WHERE letter_id = ?', (letter_id,)
    )
    if not raw_tags:
        return
    names = [t for t in (s.strip() for s in raw_tags.replace(',', '\n').splitlines()) if t]
    for name in names:
        tag_id = _get_or_create_tag(conn, name)
        conn.execute(
            'INSERT OR IGNORE INTO letter_tag_links (letter_id, tag_id) VALUES (?, ?)',
            (letter_id, tag_id),
        )


def _get_letter_tags(conn, letter_id: int) -> list:
    rows = conn.execute(
        '''
        SELECT lt.name
        FROM letter_tags lt
        JOIN letter_tag_links ll ON ll.tag_id = lt.id
        WHERE ll.letter_id = ?
        ORDER BY lt.name
        ''',
        (letter_id,),
    ).fetchall()
    return [r['name'] for r in rows]


# ─── СПИСОК ────────────────────────────────────────────────────────────────────────────

@letters_bp.route('/')
def list_letters():
    if _login_required():
        return redirect(url_for('login'))

    db = get_db()
    date_from = request.args.get('date_from', '').strip()
    date_to   = request.args.get('date_to', '').strip()
    tag_filter = request.args.get('tag', '').strip()

    query = '''
        SELECT l.id, l.date, l.number, l.subject, l.note,
               l.created_by, l.created_at,
               u.username AS author
        FROM letters l
        JOIN users u ON u.id = l.created_by
    '''
    params = []
    conditions = []

    if date_from:
        conditions.append('l.date >= ?')
        params.append(date_from)
    if date_to:
        conditions.append('l.date <= ?')
        params.append(date_to)
    if tag_filter:
        conditions.append('''
            l.id IN (
                SELECT ll.letter_id FROM letter_tag_links ll
                JOIN letter_tags lt ON lt.id = ll.tag_id
                WHERE lt.name = ?
            )
        ''')
        params.append(_normalize_tag(tag_filter))

    if conditions:
        query += ' WHERE ' + ' AND '.join(conditions)
    query += ' ORDER BY l.date DESC, l.id DESC'

    letters = db.execute(query, params).fetchall()

    letters_with_tags = []
    for letter in letters:
        tags = _get_letter_tags(db, letter['id'])
        letters_with_tags.append({
            'id':         letter['id'],
            'date':       letter['date'],
            'number':     letter['number'],
            'subject':    letter['subject'],
            'note':       letter['note'],
            'created_by': letter['created_by'],
            'created_at': letter['created_at'],
            'author':     letter['author'],
            'tags':       tags,
            'can_edit':   _can_edit(letter),
        })

    all_tags = db.execute(
        'SELECT name FROM letter_tags ORDER BY name'
    ).fetchall()

    return render_template(
        'letters/list.html',
        letters=letters_with_tags,
        all_tags=[r['name'] for r in all_tags],
        date_from=date_from,
        date_to=date_to,
        tag_filter=tag_filter,
        can_delete=_can_delete(),
    )


# ─── СОЗДАНИЕ ──────────────────────────────────────────────────────────────────────────

@letters_bp.route('/create', methods=['POST'])
def create_letter():
    if _login_required():
        return redirect(url_for('login'))

    db = get_db()
    date    = request.form.get('date', '').strip()
    number  = request.form.get('number', '').strip()
    subject = request.form.get('subject', '').strip()
    note    = request.form.get('note', '').strip()
    tags    = request.form.get('tags', '').strip()

    if not date:
        return redirect(url_for('letters.list_letters'))

    created_at = datetime.utcnow().isoformat()
    cur = db.execute(
        '''
        INSERT INTO letters (date, number, subject, note, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ''',
        (date, number, subject, note, session['user_id'], created_at),
    )
    letter_id = cur.lastrowid
    _set_letter_tags(db, letter_id, tags)
    db.commit()

    log_action(db, session['user_id'], 'letter_create', letter_id)
    return redirect(url_for('letters.list_letters'))


# ─── РЕДАКТИРОВАНИЕ ───────────────────────────────────────────────────────────────────────

@letters_bp.route('/<int:id>/edit', methods=['GET', 'POST'])
def edit_letter(id):
    if _login_required():
        return redirect(url_for('login'))

    db = get_db()
    letter = db.execute('SELECT * FROM letters WHERE id = ?', (id,)).fetchone()
    if letter is None:
        return redirect(url_for('letters.list_letters'))

    if not _can_edit(letter):
        return redirect(url_for('letters.list_letters'))

    if request.method == 'GET':
        tags = _get_letter_tags(db, id)
        return jsonify({
            'id':      letter['id'],
            'date':    letter['date'],
            'number':  letter['number'],
            'subject': letter['subject'],
            'note':    letter['note'],
            'tags':    ', '.join(tags),
        })

    date    = request.form.get('date', '').strip()
    number  = request.form.get('number', '').strip()
    subject = request.form.get('subject', '').strip()
    note    = request.form.get('note', '').strip()
    tags    = request.form.get('tags', '').strip()

    if not date:
        return redirect(url_for('letters.list_letters'))

    db.execute(
        '''
        UPDATE letters SET date=?, number=?, subject=?, note=?
        WHERE id=?
        ''',
        (date, number, subject, note, id),
    )
    _set_letter_tags(db, id, tags)
    db.commit()

    log_action(db, session['user_id'], 'letter_edit', id)
    return redirect(url_for('letters.list_letters'))


# ─── УДАЛЕНИЕ ──────────────────────────────────────────────────────────────────────────

@letters_bp.route('/<int:id>/delete', methods=['POST'])
def delete_letter(id):
    if _login_required():
        return redirect(url_for('letters.list_letters'))
    if not _can_delete():
        return redirect(url_for('letters.list_letters'))

    db = get_db()
    db.execute('DELETE FROM letters WHERE id = ?', (id,))
    db.commit()

    log_action(db, session['user_id'], 'letter_delete', id)
    return redirect(url_for('letters.list_letters'))


# ─── AUTOCOMPLETE ТЕГОВ ─────────────────────────────────────────────────────────────────────

@letters_bp.route('/api/tags')
def api_tags():
    if _login_required():
        return jsonify([])
    q = request.args.get('q', '').strip().lower()
    db = get_db()
    if q:
        rows = db.execute(
            'SELECT id, name FROM letter_tags WHERE name LIKE ? ORDER BY name LIMIT 20',
            (f'%{q}%',),
        ).fetchall()
    else:
        rows = db.execute(
            'SELECT id, name FROM letter_tags ORDER BY name LIMIT 20'
        ).fetchall()
    return jsonify([{'id': r['id'], 'name': r['name']} for r in rows])


# ─── ШАБЛОНЫ ПИСЕМ ──────────────────────────────────────────────────────────────────────

@letters_bp.route('/templates')
def list_templates():
    if _login_required():
        return redirect(url_for('login'))

    db = get_db()
    user_id = session['user_id']
    role = session.get('role')

    if role == 'admin':
        rows = db.execute(
            '''
            SELECT t.id, t.name, t.subject, t.body, t.is_shared, t.created_by,
                   u.username AS author
            FROM letter_templates t
            LEFT JOIN users u ON u.id = t.created_by
            ORDER BY t.name
            '''
        ).fetchall()
    else:
        rows = db.execute(
            '''
            SELECT t.id, t.name, t.subject, t.body, t.is_shared, t.created_by,
                   u.username AS author
            FROM letter_templates t
            LEFT JOIN users u ON u.id = t.created_by
            WHERE t.is_shared = 1 OR t.created_by = ?
            ORDER BY t.name
            ''',
            (user_id,),
        ).fetchall()

    templates = []
    for r in rows:
        templates.append({
            'id':         r['id'],
            'name':       r['name'],
            'subject':    r['subject'],
            'body':       r['body'],
            'is_shared':  r['is_shared'],
            'created_by': r['created_by'],
            'author':     r['author'],
            'can_delete': (
                r['created_by'] == user_id or role == 'admin'
            ),
        })

    return render_template('letters/templates.html', templates=templates)


@letters_bp.route('/templates/create', methods=['POST'])
def create_template():
    if _login_required():
        return redirect(url_for('login'))

    name      = request.form.get('name', '').strip()
    subject   = request.form.get('subject', '').strip()
    body      = request.form.get('body', '').strip()
    is_shared = 1 if request.form.get('is_shared') else 0

    if not name:
        return redirect(url_for('letters.list_templates'))

    db = get_db()
    cur = db.execute(
        '''
        INSERT INTO letter_templates (name, subject, body, created_by, is_shared)
        VALUES (?, ?, ?, ?, ?)
        ''',
        (name, subject, body, session['user_id'], is_shared),
    )
    db.commit()
    log_action(db, session['user_id'], 'letter_template_create', cur.lastrowid)
    return redirect(url_for('letters.list_templates'))


@letters_bp.route('/templates/<int:id>/delete', methods=['POST'])
def delete_template(id):
    if _login_required():
        return redirect(url_for('letters.list_templates'))

    db = get_db()
    tmpl = db.execute(
        'SELECT id, created_by FROM letter_templates WHERE id = ?', (id,)
    ).fetchone()

    if tmpl is None or not _can_delete_template(tmpl):
        return redirect(url_for('letters.list_templates'))

    db.execute('DELETE FROM letter_templates WHERE id = ?', (id,))
    db.commit()
    log_action(db, session['user_id'], 'letter_template_delete', id)
    return redirect(url_for('letters.list_templates'))


@letters_bp.route('/api/templates')
def api_templates():
    if _login_required():
        return jsonify([])

    q = request.args.get('q', '').strip()
    user_id = session['user_id']
    role = session.get('role')
    db = get_db()

    base_where = 'WHERE (t.is_shared = 1 OR t.created_by = ?)' if role != 'admin' else 'WHERE 1=1'
    params = [user_id] if role != 'admin' else []

    if q:
        base_where += ' AND t.name LIKE ?'
        params.append(f'%{q}%')

    rows = db.execute(
        f'''
        SELECT t.id, t.name, t.subject, t.body
        FROM letter_templates t
        {base_where}
        ORDER BY t.name
        LIMIT 30
        ''',
        params,
    ).fetchall()

    return jsonify([{
        'id':      r['id'],
        'name':    r['name'],
        'subject': r['subject'],
        'body':    r['body'],
    } for r in rows])
