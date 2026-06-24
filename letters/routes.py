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


def _get_users(conn):
    return conn.execute(
        'SELECT id, username, full_name FROM users WHERE is_active=1 ORDER BY full_name'
    ).fetchall()


# ─── СПИСОК ──────────────────────────────────────────────────────────────────────────────────

@letters_bp.route('/')
def list_letters():
    if _login_required():
        return redirect(url_for('auth.login'))

    db = get_db()
    users = _get_users(db)

    return render_template(
        'letters/list.html',
        can_delete=_can_delete(),
        can_manage_templates=(session.get('role') == 'admin'),
        users=users,
    )


# ─── API: СПИСОК ПИСЕМ ДЛЯ TABULATOR ─────────────────────────────────────────

@letters_bp.route('/api/list')
def api_list():
    if _login_required():
        return jsonify([]), 401

    db = get_db()
    user_id = session.get('user_id')
    role    = session.get('role')
    can_del = _can_delete()

    rows = db.execute(
        '''
        SELECT l.id, l.date, l.number, l.subject, l.note,
               l.created_by, l.direction, l.counterparty_id,
               l.executor_id,
               u_exec.username  AS executor_username,
               u_exec.full_name AS executor_name,
               cp.name          AS counterparty_name
        FROM letters l
        LEFT JOIN users u_exec ON u_exec.id = l.executor_id
        LEFT JOIN counterparties cp ON cp.id = l.counterparty_id
        ORDER BY l.date DESC, l.id DESC
        ''',
    ).fetchall()

    result = []
    for r in rows:
        tags = _get_letter_tags(db, r['id'])
        executor_display = r['executor_name'] or r['executor_username'] or ''
        can_edit = (r['created_by'] == user_id or role in ('admin', 'manager'))
        result.append({
            'id':                r['id'],
            'date':              r['date'] or '',
            'number':            r['number'] or '',
            'direction':         r['direction'] or 'out',
            'counterparty_name': r['counterparty_name'] or '',
            'subject':           r['subject'] or '',
            'note':              r['note'] or '',
            'tags':              tags,
            'executor_display':  executor_display,
            'can_edit':          can_edit,
            'can_delete':        can_del,
        })

    return jsonify(result)


# ─── СОЗДАНИЕ ──────────────────────────────────────────────────────────────────────────────

@letters_bp.route('/create', methods=['POST'])
def create_letter():
    if _login_required():
        return redirect(url_for('auth.login'))

    db = get_db()
    date             = request.form.get('date', '').strip()
    number           = request.form.get('number', '').strip()
    subject          = request.form.get('subject', '').strip()
    note             = request.form.get('note', '').strip()
    tags             = request.form.get('tags', '').strip()
    executor_id      = request.form.get('executor_id') or None
    direction        = request.form.get('direction', 'out').strip() or 'out'
    counterparty_id  = request.form.get('counterparty_id') or None

    if executor_id:
        executor_id = int(executor_id)
    if counterparty_id:
        counterparty_id = int(counterparty_id)

    if not date:
        return redirect(url_for('letters.list_letters'))

    created_at = datetime.utcnow().isoformat()
    cur = db.execute(
        '''
        INSERT INTO letters
            (date, number, subject, note, created_by, created_at,
             executor_id, direction, counterparty_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''',
        (date, number, subject, note, session['user_id'], created_at,
         executor_id, direction, counterparty_id),
    )
    letter_id = cur.lastrowid
    _set_letter_tags(db, letter_id, tags)
    db.commit()

    log_action(db, session['user_id'], 'letter_create', letter_id)
    return redirect(url_for('letters.list_letters'))


# ─── РЕДАКТИРОВАНИЕ ─────────────────────────────────────────────────────────────────────────────

@letters_bp.route('/<int:id>/edit', methods=['GET', 'POST'])
def edit_letter(id):
    if _login_required():
        return redirect(url_for('auth.login'))

    db = get_db()
    letter = db.execute('SELECT * FROM letters WHERE id = ?', (id,)).fetchone()
    if letter is None:
        return redirect(url_for('letters.list_letters'))

    if not _can_edit(letter):
        return redirect(url_for('letters.list_letters'))

    if request.method == 'GET':
        tags = _get_letter_tags(db, id)
        cp_name = ''
        if letter['counterparty_id']:
            cp_row = db.execute(
                'SELECT name FROM counterparties WHERE id = ?',
                (letter['counterparty_id'],)
            ).fetchone()
            if cp_row:
                cp_name = cp_row['name']
        return jsonify({
            'id':               letter['id'],
            'date':             letter['date'],
            'number':           letter['number'],
            'subject':          letter['subject'],
            'note':             letter['note'],
            'tags':             ', '.join(tags),
            'executor_id':      letter['executor_id'],
            'direction':        letter['direction'] or 'out',
            'counterparty_id':  letter['counterparty_id'],
            'counterparty_name': cp_name,
        })

    date             = request.form.get('date', '').strip()
    number           = request.form.get('number', '').strip()
    subject          = request.form.get('subject', '').strip()
    note             = request.form.get('note', '').strip()
    tags             = request.form.get('tags', '').strip()
    executor_id      = request.form.get('executor_id') or None
    direction        = request.form.get('direction', 'out').strip() or 'out'
    counterparty_id  = request.form.get('counterparty_id') or None

    if executor_id:
        executor_id = int(executor_id)
    if counterparty_id:
        counterparty_id = int(counterparty_id)

    if not date:
        return redirect(url_for('letters.list_letters'))

    db.execute(
        '''
        UPDATE letters
        SET date=?, number=?, subject=?, note=?,
            executor_id=?, direction=?, counterparty_id=?
        WHERE id=?
        ''',
        (date, number, subject, note, executor_id, direction, counterparty_id, id),
    )
    _set_letter_tags(db, id, tags)
    db.commit()

    log_action(db, session['user_id'], 'letter_edit', id)
    return redirect(url_for('letters.list_letters'))


# ─── УДАЛЕНИЕ ──────────────────────────────────────────────────────────────────────────────

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


# ─── AUTOCOMPLETE ТЕГОВ ─────────────────────────────────────────────────────────────────────────────────

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


# ─── AUTOCOMPLETE КОНТРАГЕНТОВ (#13) ───────────────────────────────────────────────────────

@letters_bp.route('/api/counterparties', methods=['GET', 'POST'])
def api_counterparties():
    if _login_required():
        if request.method == 'GET':
            return jsonify([])
        return jsonify({'error': 'unauthorized'}), 401

    db = get_db()

    if request.method == 'GET':
        q = request.args.get('q', '').strip()
        if q:
            rows = db.execute(
                'SELECT id, name FROM counterparties WHERE name LIKE ? ORDER BY name LIMIT 20',
                (f'%{q}%',),
            ).fetchall()
        else:
            rows = db.execute(
                'SELECT id, name FROM counterparties ORDER BY name LIMIT 20'
            ).fetchall()
        return jsonify([{'id': r['id'], 'name': r['name']} for r in rows])

    # POST
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or request.form.get('name', '')).strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    row = db.execute(
        'SELECT id, name FROM counterparties WHERE name = ?', (name,)
    ).fetchone()
    if row:
        return jsonify({'id': row['id'], 'name': row['name']})
    created_at = datetime.utcnow().isoformat()
    cur = db.execute(
        'INSERT INTO counterparties (name, created_at) VALUES (?, ?)',
        (name, created_at),
    )
    db.commit()
    log_action(db, session['user_id'], 'counterparty_create', cur.lastrowid)
    return jsonify({'id': cur.lastrowid, 'name': name}), 201


# ─── ШАБЛОНЫ ПИСЕМ (#12) ───────────────────────────────────────────────────────────────────────────

@letters_bp.route('/templates')
def list_templates():
    if _login_required():
        return redirect(url_for('auth.login'))

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
        return redirect(url_for('auth.login'))

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
        return redirect(url_for('auth.login'))

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

    if role == 'admin':
        base_where = 'WHERE 1=1'
        params = []
    else:
        base_where = 'WHERE (t.is_shared = 1 OR t.created_by = ?)'
        params = [user_id]

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
