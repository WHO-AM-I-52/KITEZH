# ╔════════════════════════════════════════════════════════════════════════╗
# ║                          workflow.py                                      ║
# ║  Стадии договора, условия переходов, история и создание задач.         ║
# ║                                                                        ║
# ║  Контракт: функции получают conn (без commit/close) и user-контекст.   ║
# ║  Транзакцией владеет вызывающий маршрут.                                ║
# ╚══════════════════════════════════════════════════════════════════════╝

from datetime import datetime

from contracts.workdays import add_days, fmt as workdays_fmt


# ─── ОПИСАНИЕ СТАДИЙ ────────────────────────────────────────────────────────

STAGES = [
    'draft',                 # 1. Черновик
    'customer_review',       # 2. На согласовании у заказчика
    'directum_review',       # 3. На согласовании в Directum
    'customer_signing',     # 4. На утверждении и подписании у заказчика
    'payment',               # 5. В оплате
    'in_work',               # 6. В работе
    'act_signing',           # 7. Подписание актов
    'completed',             # 8. Исполнение завершено
]

STAGE_LABELS = {
    'draft':            'Черновик',
    'customer_review':  'На согласовании у заказчика',
    'directum_review':  'На согласовании в Directum',
    'customer_signing': 'На утверждении и подписании',
    'payment':          'В оплате',
    'in_work':          'В работе',
    'act_signing':      'Подписание актов',
    'completed':        'Исполнение договора завершено',
}

STAGE_ORDER = {s: i for i, s in enumerate(STAGES)}
STATES = ('active', 'paused', 'cancelled', 'terminated', 'completed')

# Безвозмездные договоры: поручение руководителя, договор и оплата
# не оформляются, акты не формируются в ЭДО.
FREE_STAGES = ['draft', 'in_work', 'act_signing', 'completed']
FREE_STAGE_ORDER = {s: i for i, s in enumerate(FREE_STAGES)}

FREE_NEXT_ACTIONS = {
    'draft': [
        ('to_in_work_free', 'in_work', 'Принято в работу'),
    ],
    'in_work': [
        ('to_act_signing_free', 'act_signing', 'Результат передан заказчику'),
    ],
    'act_signing': [
        ('to_completed_free', 'completed', 'Работа завершена'),
    ],
    'completed': [],
}

# Задачи этапов безвозмездного маршрута (сроков нет — устное поручение).
FREE_STAGE_DEADLINES = {
    'in_work':     {'task': 'Оказать услуги по поручению'},
    'act_signing': {'task': 'Направить и подписать акт'},
}

# Внутренние контрольные сроки стадий возмездного договора.
# day_type: 'work' — рабочие, 'cal' — календарные.
STAGE_DEADLINES = {
    'customer_review':  {'days': 5,  'day_type': 'work',
                         'task': 'Получить обратную связь от заказчика'},
    'directum_review':  {'days': 14, 'day_type': 'cal',
                         'task': 'Согласовать договор в Directum'},
    'customer_signing': {'days': 3,  'day_type': 'work',
                         'task': 'Получить подписанный договор'},
    'payment':          {'days': None, 'day_type': None,
                         'task': 'Проконтролировать оплату'},
    'in_work':          {'days': None, 'day_type': None,
                         'task': 'Оказать услуги по договору'},
    'act_signing':      {'days': 5,  'day_type': 'work',
                         'task': 'Направить акты заказчику'},
}

# Подписание актов — всегда 7 рабочих дней с даты направления.
ACT_SIGN_DEADLINE_WORK = 7
# Направление актов — 5 рабочих дней с даты готовности результата.
ACT_SEND_DEADLINE_WORK = 5

# Переходы «вперёд»: кнопка события -> целевая стадия.
NEXT_ACTIONS = {
    'draft': [
        ('to_customer_review', 'customer_review',
         'Проект отправлен заказчику'),
    ],
    'customer_review': [
        ('to_directum_review', 'directum_review',
         'Согласование с заказчиком завершено'),
    ],
    'directum_review': [
        ('to_customer_signing', 'customer_signing',
         'Согласовано в Directum'),
    ],
    'customer_signing': [
        ('to_payment', 'payment', 'Договор подписан'),
    ],
    'payment': [
        ('to_in_work', 'in_work', 'Основание начала работ получено'),
    ],
    'in_work': [
        ('to_act_signing', 'act_signing', 'Услуги оказаны'),
    ],
    'act_signing': [
        ('to_completed', 'completed', 'Акт подписан'),
    ],
    'completed': [],
}


def _now() -> str:
    return datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S')


# ─── ПРОВЕРКИ ПЕРЕХОДОВ ─────────────────────────────────────────────────────

def _stage_log_count(conn, contract_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM contract_stage_log WHERE contract_id=?",
        (contract_id,)
    ).fetchone()[0]


def load_work_calendar(conn) -> dict:
    """Производственный календарь 'ГГГГ-ММ-ДД' -> 'holiday'|'workday'."""
    return {
        row['day']: row['kind']
        for row in conn.execute("SELECT day, kind FROM work_calendar")
    }


def route_of(contract):
    """Маршрут договора: безвозмездный или полный возмездный."""
    return FREE_STAGES if contract['is_free'] else STAGES


def route_order(contract):
    return FREE_STAGE_ORDER if contract['is_free'] else STAGE_ORDER


def next_actions(contract):
    """Кнопки перехода для текущей стадии с учётом типа договора."""
    if contract['is_free']:
        return FREE_NEXT_ACTIONS.get(contract['stage'], [])
    return NEXT_ACTIONS.get(contract['stage'], [])


def transition_allowed(conn, contract, target: str) -> bool:
    """Разрешён ли переход с текущей стадии на target.

    Правила: вперёд — только на следующую стадию своего маршрута
    (возмездного или безвозмездного); назад — на любую предыдущую
    (с причиной, проверяется в маршруте).
    """
    current = contract['stage']
    order = route_order(contract)
    if current not in order or target not in order:
        return False
    if contract['state'] not in ('active',):
        return False
    # Повторное нажатие той же кнопки: договор уже на целевой стадии.
    if target == current:
        return True
    if order[target] == order[current] + 1:
        return True
    if order[target] < order[current]:
        # возврат разрешён, но повторная фиксация той же стадии — нет,
        # если история уже содержит такой переход
        return True
    return False


def stage_requirements(conn, contract, target: str) -> list:
    """Обязательные условия перехода (список сообщений об отсутствии)."""
    missing = []
    if contract['is_free']:
        # Безвозмездный: договор и оплата не оформляются.
        if target == 'in_work':
            if not contract['free_basis']:
                missing.append('Не указано основание поручения руководителя')
        elif target == 'act_signing':
            if not _current_file(conn, contract['id'], 'result'):
                missing.append('Не загружены результаты услуг')
        return missing
    if target == 'customer_review':
        if not contract['customer_full_name']:
            missing.append('Не указан заказчик')
        if not contract['contact_name']:
            missing.append('Не указано контактное лицо')
        if not contract['total_kop'] and not contract['is_free']:
            missing.append('Не добавлены услуги')
    elif target == 'directum_review':
        if not _current_file(conn, contract['id'], 'project'):
            missing.append('Не загружена версия проекта договора')
    elif target == 'customer_signing':
        if not contract['reg_number']:
            missing.append('Не указан номер договора из Directum')
    elif target == 'payment':
        if contract['is_free']:
            # безвозмездный: договор не формируется
            return []
        if not _current_file(conn, contract['id'], 'signed_contract'):
            missing.append('Не загружен подписанный договор')
    elif target == 'in_work':
        if not contract['work_start_basis']:
            missing.append('Не выбрано основание начала работ')
        if not contract['duration_days']:
            missing.append('Не указан срок оказания услуг')
    elif target == 'act_signing':
        if not contract['act_sign_method']:
            missing.append('Не выбран способ подписания актов')
        if not _current_file(conn, contract['id'], 'result'):
            missing.append('Не загружены результаты услуг')
    elif target == 'completed':
        if contract['is_free']:
            return []
        if not _current_file(conn, contract['id'], 'act'):
            missing.append('Не загружен подписанный акт')
    return missing


def _current_file(conn, contract_id: int, category: str):
    return conn.execute(
        """SELECT * FROM contract_files
           WHERE contract_id=? AND category=? AND is_current=1
           ORDER BY version DESC LIMIT 1""",
        (contract_id, category)
    ).fetchone()


# ─── ПЕРЕХОД И СОЗДАНИЕ ЗАДАЧ ───────────────────────────────────────────────

def _close_stage_tasks(conn, contract_id: int, user_id: int, comment: str):
    """Закрывает активные задачи стадии и отмечает их в contract_tasks."""
    rows = conn.execute(
        """SELECT ct.task_id FROM contract_tasks ct
           JOIN tasks t ON t.id = ct.task_id
           WHERE ct.contract_id=? AND ct.closed_at IS NULL
             AND t.status NOT IN ('done', 'cancelled')""",
        (contract_id,)
    ).fetchall()
    now = _now()
    for row in rows:
        conn.execute(
            "UPDATE tasks SET status='done', closed_at=?, result=? WHERE id=?",
            (now, comment, row['task_id'])
        )
        conn.execute(
            """INSERT INTO task_comments
               (task_id, user_id, event_type, body, created_at)
               VALUES (?, ?, 'status_change', ?, ?)""",
            (row['task_id'], user_id, comment, now)
        )
        conn.execute(
            "UPDATE contract_tasks SET closed_at=? WHERE task_id=?",
            (now, row['task_id'])
        )


def stage_task_spec(contract, stage: str):
    """Спецификация задачи стадии с учётом маршрута договора."""
    if contract['is_free']:
        return FREE_STAGE_DEADLINES.get(stage)
    return STAGE_DEADLINES.get(stage)


def create_stage_task(conn, contract, stage: str, deadline: str, user_id: int,
                      kind: str = 'stage', title: str = None):
    """Создаёт контрольную задачу стадии для ответственного исполнителя.

    Задача попадает в существующий модуль «Задачи» (/tasks/my).
    """
    spec = stage_task_spec(contract, stage) or {}
    if kind == 'stage' and not spec.get('task'):
        return None
    now = _now()
    if title is None:
        title = (f"{spec.get('task', stage)}"
                 f" — {STAGE_LABELS.get(stage, '')}")
    source = f"Договор {contract['inner_number'] or contract['id']}"
    cur = conn.execute(
        """INSERT INTO tasks (title, description, source, deadline, status,
                              created_by, created_at)
           VALUES (?, ?, ?, ?, 'new', ?, ?)""",
        (title,
         (f"Договор: {contract['inner_number'] or contract['id']}\n"
          f"Заказчик: {contract['customer_short_name'] or contract['customer_full_name']}\n"
          f"Проект: {contract['project_name'] or '—'}"),
         source, deadline, user_id, now)
    )
    task_id = cur.lastrowid
    conn.execute(
        """INSERT INTO task_assignees (task_id, user_id, assigned_at, assigned_by)
           VALUES (?, ?, ?, ?)""",
        (task_id, contract['executor_id'], now, user_id)
    )
    conn.execute(
        """INSERT INTO task_comments (task_id, user_id, event_type, body, created_at)
           VALUES (?, ?, 'status_change', 'Задача создана автоматически
            по договору', ?)""",
        (task_id, user_id, now)
    )
    conn.execute(
        """INSERT INTO contract_tasks (contract_id, task_id, stage, kind, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (contract['id'], task_id, stage, kind, now)
    )
    return task_id


# ─── КОНТРОЛЬНЫЕ СРОКИ СТАДИЙ ───────────────────────────────────────────────

def stage_deadline(conn, contract, stage: str, event_date):
    """Контрольный срок стадии от даты события. None — срока нет.

    Возмездный маршрут:
      customer_review  — 5 рабочих дней;
      directum_review  — 14 календарных дней;
      customer_signing — 3 рабочих дня;
      payment          — по условиям договора (payment_days/payment_day_type);
      in_work          — срок оказания услуг (duration_days/duration_day_type);
      act_signing      — 5 рабочих дней на направление актов.
    Безвозмездный маршрут: контрольных сроков нет (устное поручение).
    """
    if contract['is_free']:
        return None
    spec = STAGE_DEADLINES.get(stage)
    if not spec:
        return None
    calendar = load_work_calendar(conn)
    if stage == 'payment':
        days, day_type = contract['payment_days'], contract['payment_day_type']
    elif stage == 'in_work':
        days, day_type = contract['duration_days'], contract['duration_day_type']
    else:
        days, day_type = spec['days'], spec['day_type']
    if not days:
        return None
    return str(add_days(event_date, days,
                        day_type or 'work', calendar))


def acts_sent(conn, contract, user_id: int, send_date: str):
    """Акты направлены заказчику: закрыть задачу направления, поставить
    задачу «Получить подписанный акт» сроком 7 рабочих дней.

    Возвращает (ok: bool, error: str|None). Вызывающий делает commit.
    """
    if contract['stage'] != 'act_signing':
        return False, 'Акты направляются на стадии «Подписание актов»'
    if contract['is_free']:
        return False, 'Для безвозмездного договора акт не оформляется'
    already = conn.execute(
        """SELECT 1 FROM contract_tasks ct
           JOIN tasks t ON t.id = ct.task_id
           WHERE ct.contract_id=? AND ct.kind='act_sign'
             AND ct.closed_at IS NULL
             AND t.status NOT IN ('done', 'cancelled')""",
        (contract['id'],)
    ).fetchone()
    if already:
        return False, 'Акты уже направлены заказчику'
    deadline = str(add_days(send_date, ACT_SIGN_DEADLINE_WORK, 'work',
                            load_work_calendar(conn)))
    _close_stage_tasks(
        conn, contract['id'], user_id,
        f"Акты направлены заказчику {workdays_fmt(send_date)}"
    )
    create_stage_task(
        conn, contract, 'act_signing', deadline, user_id,
        kind='act_sign', title='Получить подписанный акт'
    )
    return True, None


def apply_transition(conn, contract, target: str, user_id: int,
                     event_date: str, comment: str = None,
                     deadline: str = None):
    """Выполняет переход: история + задачи. Вызывающий делает commit.

    Контрольный срок стадии рассчитывается автоматически от event_date
    (явный deadline — только для отката/корректировки).
    Возвращает (ok: bool, error: str|None).
    """
    if not transition_allowed(conn, contract, target):
        return False, 'Переход на указанную стадию не разрешён'

    missing = stage_requirements(conn, contract, target)
    if missing:
        return False, '; '.join(missing)

    # Повторное нажатие: если уже есть активный переход на target,
    # ничего не создаём.
    now = _now()
    last = conn.execute(
        """SELECT to_stage FROM contract_stage_log
           WHERE contract_id=? ORDER BY id DESC LIMIT 1""",
        (contract['id'],)
    ).fetchone()
    if last and last['to_stage'] == target and target != 'draft':
        # уже переведено; не создаём дубликат задачи
        conn.execute(
            "UPDATE contracts SET stage=?, updated_at=? WHERE id=?",
            (target, now, contract['id'])
        )
        return True, None

    _close_stage_tasks(
        conn, contract['id'], user_id,
        f"Закрыта переходом договора на стадию «{STAGE_LABELS.get(target, target)}»"
    )
    conn.execute(
        """INSERT INTO contract_stage_log
           (contract_id, from_stage, to_stage, event_date, entered_at, user_id, comment)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (contract['id'], contract['stage'], target, event_date, now,
         user_id, comment)
    )
    conn.execute(
        "UPDATE contracts SET stage=?, state='active', updated_at=? WHERE id=?",
        (target, now, contract['id'])
    )
    spec = stage_task_spec(contract, target)
    if spec and spec.get('task'):
        if deadline is None:
            deadline = stage_deadline(conn, contract, target, event_date)
        create_stage_task(conn, contract, target, deadline, user_id)
    if target == 'completed':
        conn.execute(
            "UPDATE contracts SET state='completed' WHERE id=?",
            (contract['id'],)
        )
    return True, None
