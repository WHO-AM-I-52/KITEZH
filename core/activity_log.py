# ╔══════════════════════════════════════════════════════════════╗
# ║                     activity_log.py                          ║
# ║  Журнал действий пользователей                               ║
# ╚══════════════════════════════════════════════════════════════╝

import sqlite3
import time
from datetime import datetime

from db import get_db


ACTION_LABELS = {
    'create':        'Создание обращения',
    'edit':          'Редактирование обращения',
    'delete':        'Удаление обращения',
    'accept':        'Принятие обращения',
    'reject':        'Возврат на доработку',
    'answer':        'Фиксация ответа',
    'rollback':      'Откат истории',
    'status':        'Смена статуса',
    'favorite':      'Избранное',
    'export_report': 'Выгрузка отчёта Excel',
    'export_minek':  'Выгрузка МинЭК Excel',
    'register':      'Регистрация обращения (присвоен номер)',
    'take_work':     'Исполнитель принял в работу',
    'send_reviewer': 'Направлено на проверку',
    'reviewer_ok':   'Проверяющий одобрил',
    'reviewer_rej':  'Проверяющий отклонил',
    'docs_sent':     'Документы отправлены заявителю',
    'close':         'Обращение закрыто',
    'admin_return':  'Админ вернул в черновик',
    'perm_change':   'Изменение прав пользователя',
}


def log_action(conn, user_id: int, action: str, request_id: int = None, detail: str = None):
    """Записывает действие пользователя; блокировка SQLite не ломает запрос."""
    for attempt in range(3):
        try:
            conn.execute(
                "INSERT INTO activity_log (user_id, action, request_id, detail, created_at) VALUES (?,?,?,?,?)",
                (user_id, action, request_id, detail, datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
            )
            return True
        except sqlite3.OperationalError as exc:
            if 'database is locked' not in str(exc).lower():
                return False
            if attempt < 2:
                time.sleep(0.15 * (attempt + 1))
        except Exception:
            return False
    return False


def get_activity_log(limit: int = 100, user_id: int = None, action: str = None, date_from: str = None):
    """Возвращает события из activity_log."""
    conn = get_db()
    where = ['1=1']
    params = []
    if user_id:
        where.append('al.user_id=?')
        params.append(user_id)
    if action:
        where.append('al.action=?')
        params.append(action)
    if date_from:
        where.append('al.created_at >= ?')
        params.append(date_from)
    params.append(limit)
    rows = conn.execute(
        "SELECT al.*, u.full_name, u.username FROM activity_log al "
        "LEFT JOIN users u ON al.user_id = u.id "
        f"WHERE {' AND '.join(where)} ORDER BY al.id DESC LIMIT ?",
        params,
    ).fetchall()
    conn.close()
    return rows


def get_perm_audit(limit: int = 200):
    """Возвращает только события изменения прав пользователей."""
    conn = get_db()
    rows = conn.execute(
        "SELECT al.*, u_actor.full_name AS actor_name, u_actor.username AS actor_username "
        "FROM activity_log al LEFT JOIN users u_actor ON al.user_id = u_actor.id "
        "WHERE al.action = 'perm_change' ORDER BY al.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    conn.close()
    return rows
