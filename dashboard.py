# ╔══════════════════════════════════════════════════════════════╗
# ║                       dashboard.py                           ║
# ║  Построение дашборда: KPI, графики, агрегаты по обращениям   ║
# ║  Актуальные коды статусов:                              ║
# ║    draft | registered | in_progress | under_review |          ║
# ║    ready_to_send | sent_to_applicant | closed                  ║
# ║  Норматив (рабочих дней по этапам):                     ║
# ║    1 + 1 + 5 + 2 + 1 = 10 до отправки, +12 до закрытия  ║
# ╚══════════════════════════════════════════════════════════════╝

from datetime import date, timedelta
from db import STATUS_NORM_DAYS

ACTIVE_STATUSES = ('draft', 'registered', 'in_progress', 'under_review', 'ready_to_send', 'sent_to_applicant')
CLOSED_STATUS   = 'closed'

# Описание этапов для карточки норматива
STAGE_NORMS = [
    {'from': 'Черновик',             'to': 'Зарегистрировано',    'days': STATUS_NORM_DAYS['registered'],        'field': 'at_registered'},
    {'from': 'Зарегистрировано',     'to': 'В работе',            'days': STATUS_NORM_DAYS['in_progress'],       'field': 'at_in_progress'},
    {'from': 'В работе',             'to': 'На проверке',         'days': STATUS_NORM_DAYS['under_review'],      'field': 'at_under_review'},
    {'from': 'На проверке',          'to': 'Готово к отправке',   'days': STATUS_NORM_DAYS['ready_to_send'],     'field': 'at_ready_to_send'},
    {'from': 'Готово к отправке',    'to': 'Документы отправлены','days': STATUS_NORM_DAYS['sent_to_applicant'], 'field': 'at_sent_to_applicant'},
    {'from': 'Документы отправлены', 'to': 'Закрыто',             'days': STATUS_NORM_DAYS['closed'],            'field': 'at_closed'},
]
NORM_TOTAL_DAYS_TO_SEND = sum(s['days'] for s in STAGE_NORMS if s['field'] != 'at_closed')  # = 10

# ─── НОРМАЛИЗАЦИЯ РАЙОНОВ ─────────────────────────────────────────────────────
# Канонические названия районов/округов Нижегородской области.
# Любое значение из БД, содержащее ключевое слово (case-insensitive),
# будет приведено к каноническому названию.
# Порядок важен: более специфичные паттерны — выше.
_DISTRICT_CANON = [
    ('нижний новгород',             'Нижний Новгород'),
    ('нижегородск',                 'Нижний Новгород'),
    ('дзержинск',                   'Городской округ Дзержинск'),
    ('арзамас',                     'Городской округ Арзамас'),
    ('бор',                         'Городской округ Бор'),
    ('кстово',                      'Кстовский муниципальный округ'),
    ('балахн',                      'Балахнинский муниципальный округ'),
    ('богородск',                   'Богородский муниципальный округ'),
    ('павловск',                    'Павловский муниципальный округ'),
    ('нижегородская агломерация',   'Нижегородская агломерация'),
    ('агломерац',                   'Нижегородская агломерация'),
    ('поволжье',                    'Поволжье'),
]

# Паттерны мусорных значений — не район, просто описание предпочтений.
# Такие строки попадут в группу «Не указан район» и не будут показаны на графике.
_DISTRICT_NOISE = (
    'км от', 'удаленност', 'пределах', 'черте город', 'транспортн',
    'метро', 'шоссе', 'вблизи', 'около', 'рядом', 'центр',
    'промзон', 'пестицид', 'гербицид', 'подстанц', 'парк',
    'развлекател', 'историческ', 'офис', 'помещени', 'расположен',
    'требований нет', 'отсутствует', '52:', 'кадастр',
)


def _normalize_district(raw: str) -> str | None:
    """Приводит одно сырое значение к каноническому названию района.
    Возвращает None, если значение является «мусором» (свободный текст без района).
    """
    v = raw.strip()
    if not v:
        return None
    vl = v.lower()

    # Сначала проверяем шум
    for noise in _DISTRICT_NOISE:
        if noise in vl:
            return None

    # Ищем канонический район
    for pattern, canon in _DISTRICT_CANON:
        if pattern in vl:
            return canon

    # Значение не совпало ни с одним паттерном —
    # если оно длиннее 60 символов, скорее всего это свободный текст
    if len(v) > 60:
        return None

    # Иначе возвращаем как есть (возможно новый район, которого нет в маппинге)
    return v


def _bucket_query(conn, field_min, field_max, buckets, pw_sql, pw_params):
    """One SELECT with CASE WHEN for all buckets.
    CAST AS REAL обязателен — поля хранятся как TEXT в SQLite,
    без приведения типа числовые сравнения работают лексикографически.
    """
    cases = ', '.join(
        f"SUM(CASE WHEN CAST(COALESCE({field_min},{field_max}) AS REAL)>={lo} "
        f"AND CAST(COALESCE({field_min},{field_max}) AS REAL)<{hi} THEN 1 ELSE 0 END)"
        for _, lo, hi in buckets
    )
    row = conn.execute(
        f"SELECT {cases} FROM requests r WHERE "
        f"(CAST({field_min} AS REAL) > 0 OR CAST({field_max} AS REAL) > 0)"
        f"{pw_sql}",
        pw_params
    ).fetchone()
    return [{'label': lbl, 'count': (row[i] or 0)} for i, (lbl, _, __) in enumerate(buckets)]


def build_dash(conn, period):
    today = date.today()

    def pw():
        if   period == 'today':
            pf = today.isoformat()
        elif period == 'week':
            pf = (today - timedelta(days=7)).isoformat()
        elif period == 'month':
            pf = (today - timedelta(days=30)).isoformat()
        elif period == 'quarter':
            pf = (today - timedelta(days=90)).isoformat()
        elif period == 'year':
            pf = (today - timedelta(days=365)).isoformat()
        else:
            pf = None
        return (" AND r.request_date>=?", [pf]) if pf else ("", [])

    pw_sql, pw_params = pw()

    def cnt_all(status=None):
        if status:
            return conn.execute(
                "SELECT COUNT(*) FROM requests r WHERE r.status=?", [status]
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM requests r").fetchone()[0]

    def cnt(status=None):
        if status:
            return conn.execute(
                f"SELECT COUNT(*) FROM requests r WHERE r.status=?{pw_sql}",
                [status] + pw_params
            ).fetchone()[0]
        return conn.execute(
            f"SELECT COUNT(*) FROM requests r WHERE 1=1{pw_sql}", pw_params
        ).fetchone()[0]

    # ─── ПРОСРОЧЕННЫЕ (по этапному review_deadline) ───────────────
    overdue_active_all = conn.execute(
        "SELECT COUNT(*) FROM requests r "
        "WHERE r.status NOT IN ('closed','draft') "
        "AND r.review_deadline IS NOT NULL AND r.review_deadline != '' "
        "AND r.review_deadline < date('now')"
    ).fetchone()[0]

    sums = conn.execute(
        f"SELECT COALESCE(SUM(investment_total),0), COALESCE(SUM(jobs_total),0) "
        f"FROM requests r WHERE 1=1{pw_sql}", pw_params
    ).fetchone()

    # Среднее время ответа (от регистрации до отправки, в рабочих днях — приближение через календарь)
    avg_row = conn.execute(
        f"SELECT AVG(julianday(sent_to_applicant_at)-julianday(request_date)) "
        f"FROM requests r WHERE status IN ('sent_to_applicant','closed') "
        f"AND sent_to_applicant_at IS NOT NULL AND at_registered IS NOT NULL{pw_sql}",
        pw_params
    ).fetchone()

    # ─── KPI ПО СРОКАМ ─────────────────────────────────────────────
    # Норматив «до отправки» = сумма первых 5 этапов
    norm_to_send = NORM_TOTAL_DAYS_TO_SEND  # 10 рабочих дней

    # Для уже закрытых: сравниваем фактическое время (calendar days / 7 * 5 ≈ рабочие дни)
    # Точное сравнение: at_sent_to_applicant - request_date <= norm / 5 * 7
    # Используем упрощение: 10 рабочих дней ≈ 14 календарных
    norm_calendar = round(norm_to_send * 7 / 5)

    kpi_rows = conn.execute(
        f"""
        SELECT
            COUNT(*) AS total_sent,
            SUM(CASE WHEN julianday(at_sent_to_applicant) - julianday(request_date) <= {norm_calendar}
                     THEN 1 ELSE 0 END) AS in_time,
            SUM(CASE WHEN julianday(at_sent_to_applicant) - julianday(request_date) > {norm_calendar}
                     THEN 1 ELSE 0 END) AS overdue_sent
        FROM requests r
        WHERE status IN ('sent_to_applicant', 'closed')
          AND at_sent_to_applicant IS NOT NULL
          AND at_sent_to_applicant != ''
          AND request_date IS NOT NULL{pw_sql}
        """,
        pw_params
    ).fetchone()

    # Статистика по каждому этапу (среднее время прохождения, дней)
    stage_stats = []
    stage_queries = [
        ('registered',        'request_date',      'at_registered'),
        ('in_progress',       'at_registered',     'at_in_progress'),
        ('under_review',      'at_in_progress',    'at_under_review'),
        ('ready_to_send',     'at_under_review',   'at_ready_to_send'),
        ('sent_to_applicant', 'at_ready_to_send',  'at_sent_to_applicant'),
        ('closed',            'at_sent_to_applicant', 'at_closed'),
    ]
    for status_key, from_field, to_field in stage_queries:
        row = conn.execute(
            f"SELECT ROUND(AVG(julianday({to_field}) - julianday({from_field})), 1) AS avg_days "
            f"FROM requests r "
            f"WHERE {to_field} IS NOT NULL AND {to_field} != '' "
            f"AND {from_field} IS NOT NULL AND {from_field} != ''"
            f"{pw_sql}",
            pw_params
        ).fetchone()
        stage_stats.append({
            'status': status_key,
            'avg_days': row['avg_days'] if row and row['avg_days'] else None,
            'norm_days': STATUS_NORM_DAYS.get(status_key),
        })

    kpi_data = {
        'norm_days':          norm_to_send,
        'norm_calendar':      norm_calendar,
        'norm_stages':        STAGE_NORMS,
        'total_answered':     (kpi_rows['total_sent']   or 0),
        'in_time':            (kpi_rows['in_time']      or 0),
        'overdue':            (kpi_rows['overdue_sent'] or 0),
        'overdue_active':     overdue_active_all,
        'pct':                round(kpi_rows['in_time'] / kpi_rows['total_sent'] * 100)
                              if kpi_rows['total_sent'] else 0,
        'stage_stats':        stage_stats,
    }

    # ─── ТРЕНД ПО ВРЕМЕНИ ───────────────────────────────────────
    if period == 'today':
        tr = conn.execute(
            "SELECT strftime('%H:00',request_date),COUNT(*) "
            "FROM requests r WHERE 1=1" + pw_sql + " GROUP BY 1 ORDER BY 1", pw_params
        ).fetchall()
    elif period in ('week', 'month'):
        tr = conn.execute(
            "SELECT request_date,COUNT(*) FROM requests r WHERE 1=1" + pw_sql +
            " GROUP BY 1 ORDER BY 1", pw_params
        ).fetchall()
    elif period == 'quarter':
        tr = conn.execute(
            "SELECT strftime('%Y-W%W',request_date),COUNT(*) "
            "FROM requests r WHERE 1=1" + pw_sql + " GROUP BY 1 ORDER BY 1", pw_params
        ).fetchall()
    else:
        tr = conn.execute(
            "SELECT strftime('%Y-%m',request_date),COUNT(*) "
            "FROM requests r WHERE 1=1" + pw_sql + " GROUP BY 1 ORDER BY 1", pw_params
        ).fetchall()

    emp_rows = conn.execute(
        f"SELECT COALESCE(u.full_name,'Не назначен'),COUNT(*) FROM requests r "
        f"LEFT JOIN users u ON r.assigned_to=u.id WHERE 1=1{pw_sql} "
        f"GROUP BY r.assigned_to, u.full_name ORDER BY 2 DESC LIMIT 20", pw_params
    ).fetchall()

    dist_raw = conn.execute(
        f"SELECT preferred_districts FROM requests r "
        f"WHERE preferred_districts IS NOT NULL AND preferred_districts!=''{pw_sql}",
        pw_params
    ).fetchall()

    dist_counts = {}
    for row in dist_raw:
        for d in (row[0] or '').split(','):
            canon = _normalize_district(d)
            if canon:
                dist_counts[canon] = dist_counts.get(canon, 0) + 1
    dist_top = sorted(dist_counts.items(), key=lambda x: x[1], reverse=True)

    st_free = conn.execute(
        f"SELECT COUNT(*) FROM requests r WHERE site_type_free=1{pw_sql}", pw_params
    ).fetchone()[0]
    st_ex = conn.execute(
        f"SELECT COUNT(*) FROM requests r WHERE site_type_existing=1{pw_sql}", pw_params
    ).fetchone()[0]
    st_both = conn.execute(
        f"SELECT COUNT(*) FROM requests r WHERE site_type_free=1 AND site_type_existing=1{pw_sql}",
        pw_params
    ).fetchone()[0]

    area_buckets = [
        ('<0.1 га', 0, .1), ('0.1–0.5', .1, .5), ('0.5–1', .5, 1),
        ('1–2', 1, 2), ('2–5', 2, 5), ('5–10', 5, 10), ('>10', 10, 999999)
    ]
    build_buckets = [
        ('<100 м²', 0, 100), ('100–300', 100, 300), ('300–500', 300, 500),
        ('500–1000', 500, 1000), ('1000–3000', 1000, 3000),
        ('3000–5000', 3000, 5000), ('>5000', 5000, 999999)
    ]

    area_data  = _bucket_query(conn, 'site_area_ha_min',       'site_area_ha_max',       area_buckets,  pw_sql, pw_params)
    build_data = _bucket_query(conn, 'site_build_area_m2_min', 'site_build_area_m2_max', build_buckets, pw_sql, pw_params)

    src_rows = conn.execute(
        f"SELECT source_type,COUNT(*) FROM requests r "
        f"WHERE source_type IS NOT NULL AND source_type!=''{pw_sql} "
        f"GROUP BY source_type ORDER BY 2 DESC", pw_params
    ).fetchall()

    src_counts = {}
    for row in src_rows:
        for s in (row[0] or '').split(','):
            s = s.strip()
            if s:
                src_counts[s] = src_counts.get(s, 0) + row[1]

    return {
        'period':            period,
        'total':             cnt_all(),
        'draft':             cnt_all('draft'),
        'registered':        cnt_all('registered'),
        'in_progress':       cnt_all('in_progress'),
        'under_review':      cnt_all('under_review'),
        'ready_to_send':     cnt_all('ready_to_send'),
        'sent_to_applicant': cnt_all('sent_to_applicant'),
        'closed':            cnt_all('closed'),
        'overdue_active':    overdue_active_all,
        'investment_sum':    float(sums[0]) if sums else 0,
        'jobs_sum':          int(sums[1]) if sums else 0,
        'avg_days':          round(avg_row[0]) if avg_row and avg_row[0] else None,
        'kpi':               kpi_data,
        'trend_chart':       {'labels': [r[0] for r in tr],        'values': [r[1] for r in tr]},
        'emp_chart':         {'labels': [r[0] for r in emp_rows],  'values': [r[1] for r in emp_rows]},
        'dist_chart':        {'labels': [k for k, v in dist_top],  'values': [v for k, v in dist_top]},
        'site_type': {
            'free':          st_free,
            'existing':      st_ex,
            'both':          st_both,
            'only_free':     st_free - st_both,
            'only_existing': st_ex - st_both,
        },
        'area_data':         area_data,
        'build_data':        build_data,
        'source_chart':      {'labels': list(src_counts.keys()), 'values': list(src_counts.values())},
    }
