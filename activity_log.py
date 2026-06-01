# ╔══════════════════════════════════════════════════════════════╗
# ║                     activity_log.py                          ║
# ║  Журнал действий пользователей (создание, редактирование,    ║
# ║  удаление, принятие, ответ, откат, выгрузки)                 ║
# ╚══════════════════════════════════════════════════════════════╝

from datetime import datetime
from db import get_db


ACTION_LABELS = {
    # ─ Старые действия
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
    # ─ Issue #53: новая логика статусов
    'register':       'Регистрация обращения (присвоен номер)',
    'take_work':      'Исполнитель принял в работу',
    'send_reviewer':  'Направлено на проверку',
    'reviewer_ok':    'Проверяющий одобрил',
    'reviewer_rej':   'Проверяющий отклонил',
    'docs_sent':      'Документы отправлены заявителю',
    'close':          'Обращение закрыто',
    'admin_return':   'Админ вернул в черновик',
}


def log_action(conn, user_id: int, action: str,
               request_id: int = None, detail: str = None):
    """
    Записывает действие пользователя в таблицу activity_log.
    conn — открытое соединение с БД (не закрывает его).
    """
    conn.execute(
        "INSERT INTO activity_log "
        "(user_id, action, request_id, detail, created_at) "
        "VALUES (?,?,?,?,?)",
        (
            user_id,
            action,
            request_id,
            detail,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        )
    )


def get_activity_log(limit: int = 100, user_id: int = None,
                     action: str = None, date_from: str = None):
    """
    Возвращает события из activity_log.
    user_id   — фильтр по пользователю
    action    — фильтр по типу действия
    date_from — фильтр от даты (YYYY-MM-DD)
    """
    conn = get_db()
    where = ["1=1"]
    params = []

    if user_id:
        where.append("al.user_id=?")
        params.append(user_id)
    if action:
        where.append("al.action=?")
        params.append(action)
    if date_from:
        where.append("al.created_at >= ?")
        params.append(date_from)

    params.append(limit)
    rows = conn.execute(
        "SELECT al.*, u.full_name, u.username "
        "FROM activity_log al "
        "LEFT JOIN users u ON al.user_id = u.id "
        f"WHERE {' AND '.join(where)} "
        "ORDER BY al.id DESC LIMIT ?",
        params
    ).fetchall()
    conn.close()
    return rows
