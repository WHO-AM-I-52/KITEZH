# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║                        quality_checks.py                                      ║
# ║  v1.1: fix F401 (timedelta), fix Q-03 assigned_to                        ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
"""Переиспользуемый модуль проверок качества данных по обращениям SONAR.

Использование:
    from quality_checks import check_request_quality, quality_report_all, quality_bp
    app.register_blueprint(quality_bp)   # добавить в app.py

    # Одна запись:
    result = check_request_quality(row_dict)
    # result == {'errors': [...], 'warnings': [...], 'info': [...], 'score': 0..100}

    # Вся база:
    report = quality_report_all(conn)
API:
    GET /quality/check        — JSON-отчёт по всей базе
    GET /quality/check/<id>   — JSON-отчёт по одному обращению
"""

from flask import Blueprint, jsonify, session
from datetime import datetime, date

from db import get_db
from core.auth_utils import login_required
from core.activity_log import log_action

quality_bp = Blueprint('quality', __name__)


# ─── РЕЕСТР ПРАВИЛ ─────────────────────────────────────────────────────────────────────────────────
CHECKS: dict[str, tuple[str, str, int]] = {
    'Q-01': ('error',   'Нет даты обращения',                                      20),
    'Q-02': ('error',   'Нет наименования заявителя',                           20),
    'Q-03': ('warn',    'Не назначен ответственный',                           10),
    'Q-04': ('warn',    'Нет контактных данных (ни телефона, ни email)',     10),
    'Q-05': ('warn',    'Статус «Ответ направлен», но нет даты ответа',  10),
    'Q-06': ('warn',    'Инвестиции > 0, но рабочих мест = 0',              10),
    'Q-07': ('info',    'Нет названия проекта',                                    5),
    'Q-08': ('info',    'Обращение старше 90 дней без ответа',                 5),
    'Q-09': ('warn',    'Нет площади участка (га)',                                 5),
    'Q-10': ('info',    'Не указаны предпочтительные районы',                5),
}

_MAX_PENALTY = sum(w for _, _, w in CHECKS.values())


def _val(row, key: str) -> str:
    """Безопасно получить строковое значение из строки / словаря."""
    try:
        v = row[key]
    except (KeyError, IndexError):
        return ''
    return str(v).strip() if v is not None else ''


def _int_val(row, key: str):
    """Возвращает числовое значение поля или None."""
    try:
        v = row[key]
        return int(v) if v is not None else None
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def check_request_quality(row) -> dict:
    """Проверяет одну запись обращения.

    Args:
        row: sqlite3.Row или dict с полями таблицы requests.

    Returns:
        {
            'errors':   [{'code': 'Q-01', 'message': '...'}],
            'warnings': [{'code': 'Q-03', 'message': '...'}],
            'info':     [{'code': 'Q-07', 'message': '...'}],
            'score':    int 0..100,
        }
    """
    errors   = []
    warnings = []
    info     = []
    penalty  = 0

    def _add(code: str, detail: str = ''):
        level, desc, weight = CHECKS[code]
        nonlocal penalty
        penalty += weight
        msg = f"{desc}{': ' + detail if detail else ''}"
        entry = {'code': code, 'message': msg}
        if level == 'error':
            errors.append(entry)
        elif level == 'warn':
            warnings.append(entry)
        else:
            info.append(entry)

    # Q-01: нет даты
    if not _val(row, 'request_date'):
        _add('Q-01')

    # Q-02: нет заявителя
    if not _val(row, 'applicant_full_name') and not _val(row, 'applicant_short_name'):
        _add('Q-02')

    # Q-03: нет ответственного — assigned_to числовой user_id
    assigned_id = _int_val(row, 'assigned_to')
    if not assigned_id and not _val(row, 'assigned_name'):
        _add('Q-03')

    # Q-04: нет контактов
    if not _val(row, 'contact_phone') and not _val(row, 'contact_email'):
        _add('Q-04')

    # Q-05: статус answered без даты ответа
    status = _val(row, 'status')
    if status in ('answered', 'sent_to_applicant') and not _val(row, 'answer_date'):
        _add('Q-05', f'статус: {status}')

    # Q-06: инвестиции есть, рабочих мест = 0
    try:
        inv = float(_val(row, 'investment_total') or 0)
        jobs_raw = _val(row, 'jobs_total')
        jobs = int(jobs_raw) if jobs_raw else 0
        if inv > 0 and jobs == 0:
            _add('Q-06', f'инвестиции: {inv} млн руб.')
    except (ValueError, TypeError):
        pass

    # Q-07: нет названия проекта
    if not _val(row, 'project_name'):
        _add('Q-07')

    # Q-08: без ответа больше 90 дней
    rdate_str = _val(row, 'request_date')
    if rdate_str and not _val(row, 'answer_date'):
        try:
            rdate = datetime.strptime(rdate_str[:10], '%Y-%m-%d').date()
            age = (date.today() - rdate).days
            if age > 90:
                _add('Q-08', f'{age} дней без ответа')
        except ValueError:
            pass

    # Q-09: нет площади
    if not _val(row, 'site_area_ha'):
        _add('Q-09')

    # Q-10: нет районов
    if not _val(row, 'preferred_districts'):
        _add('Q-10')

    score = max(0, round(100 * (1 - penalty / _MAX_PENALTY)))

    return {
        'errors':   errors,
        'warnings': warnings,
        'info':     info,
        'score':    score,
    }


def quality_report_all(conn) -> dict:
    """Прогоняет все обращения из БД и возвращает сводный отчёт."""
    rows = conn.execute("""
        SELECT r.id, r.request_number, r.request_date, r.status,
               r.applicant_full_name, r.applicant_short_name,
               r.assigned_to, r.contact_phone, r.contact_email,
               r.investment_total, r.jobs_total, r.project_name,
               r.answer_date, r.site_area_ha, r.preferred_districts,
               u.full_name AS assigned_name
        FROM requests r
        LEFT JOIN users u ON r.assigned_to = u.id
        ORDER BY r.id
    """).fetchall()

    total      = len(rows)
    score_sum  = 0
    by_code: dict[str, int] = {code: 0 for code in CHECKS}
    records    = []

    for row in rows:
        result = check_request_quality(row)
        score_sum += result['score']

        for entry in result['errors'] + result['warnings'] + result['info']:
            code = entry['code']
            if code in by_code:
                by_code[code] += 1

        records.append({
            'id':             row['id'],
            'request_number': row['request_number'] or '—',
            'score':          result['score'],
            'errors':         result['errors'],
            'warnings':       result['warnings'],
            'info':           result['info'],
        })

    avg_score = round(score_sum / total, 1) if total else 0.0

    return {
        'total':     total,
        'avg_score': avg_score,
        'by_code':   by_code,
        'checks':    {
            code: {'level': lvl, 'description': desc, 'penalty': w}
            for code, (lvl, desc, w) in CHECKS.items()
        },
        'records':   records,
    }


# ─── МАРШРУТЫ ───────────────────────────────────────────────────────────────────────────────────

@quality_bp.route('/quality/check')
@login_required
def quality_check_all():
    """GET /quality/check — JSON-отчёт по всей базе."""
    conn = get_db()
    try:
        report = quality_report_all(conn)
        try:
            log_action(
                conn, session['user_id'], 'quality_check_all',
                detail=f"Проверка качества: {report['total']} записей, ср. оценка {report['avg_score']}"
            )
            conn.commit()
        except Exception:
            pass
    finally:
        conn.close()
    return jsonify(report)


@quality_bp.route('/quality/check/<int:request_id>')
@login_required
def quality_check_one(request_id: int):
    """GET /quality/check/<id> — JSON-отчёт по одному обращению."""
    conn = get_db()
    try:
        row = conn.execute("""
            SELECT r.id, r.request_number, r.request_date, r.status,
                   r.applicant_full_name, r.applicant_short_name,
                   r.assigned_to, r.contact_phone, r.contact_email,
                   r.investment_total, r.jobs_total, r.project_name,
                   r.answer_date, r.site_area_ha, r.preferred_districts,
                   u.full_name AS assigned_name
            FROM requests r
            LEFT JOIN users u ON r.assigned_to = u.id
            WHERE r.id = ?
        """, (request_id,)).fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({'error': f'Обращение ID {request_id} не найдено'}), 404

    result = check_request_quality(row)
    result['id']             = row['id']
    result['request_number'] = row['request_number'] or '—'
    return jsonify(result)
