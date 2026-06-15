# ╔══════════════════════════════════════════════════════════════╗
# ║                       dashboard.py                           ║
# ║  Построение дашборда: KPI, графики, агрегаты по обращениям   ║
# ║  Актуальные коды статусов:                              ║
# ║    draft | registered | in_progress | under_review |          ║
# ║    ready_to_send | sent_to_applicant | closed                  ║
# ╚══════════════════════════════════════════════════════════════╝

from datetime import date, timedelta

# Статусы, которые считаются «активными» (ещё не закрыты)
ACTIVE_STATUSES = ('draft', 'registered', 'in_progress', 'under_review', 'ready_to_send', 'sent_to_applicant')
# Статус, по которому считается среднее время ответа
CLOSED_STATUS = 'closed'


def _bucket_query(conn, field_min, field_max, buckets, pw_sql, pw_params):
    """One SELECT with CASE WHEN for all buckets.
    Uses COALESCE(field_min, field_max) so records with only _max are counted.
    """
    cases = ' '.join(
        f"SUM(CASE WHEN COALESCE({field_min},{field_max})>={lo} "
        f"AND COALESCE({field_min},{field_max})<{hi} THEN 1 ELSE 0 END)"
        for _, lo, hi in buckets
    )
    row = conn.execute(
        f"SELECT {cases} FROM requests r WHERE "
        f"(({field_min} IS NOT NULL AND {field_min}!='') "
        f"OR ({field_max} IS NOT NULL AND {field_max}!=''))"
        f"{pw_sql}",
        pw_params
    ).fetchone()
    return [{'label': lbl, 'count': (row[i] or 0)} for i, (lbl, _, __) in enumerate(buckets)]


def build_dash(conn, period):
    today = date.today()

    # ─── ФИЛЬТР ПО ПЕРИОДУ ────────────────────────────────────
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

    # ─── ОБЩЕЕ КОЛИЧЕСТВО ПО СТАТУСАМ ────────────────────
    # IMPORTANT: total и счётчики считаются БЕЗ фильтра периода,
    # чтобы карточки на главной всегда показывали все обращения.
    def cnt_all(status=None):
        if status:
            return conn.execute(
                "SELECT COUNT(*) FROM requests r WHERE r.status=?",
                [status]
            ).fetchone()[0]
        return conn.execute(
            "SELECT COUNT(*) FROM requests r"
        ).fetchone()[0]

    # Для графиков и аналитики — с фильтром периода
    def cnt(status=None):
        if status:
            return conn.execute(
                f"SELECT COUNT(*) FROM requests r WHERE r.status=?{pw_sql}",
                [status] + pw_params
            ).fetchone()[0]
        return conn.execute(
            f"SELECT COUNT(*) FROM requests r WHERE 1=1{pw_sql}",
            pw_params
        ).fetchone()[0]

    # ─── ПРОСРОЧЕННЫЕ (всегда без фильтра периода) ────────────────
    active_in = ','.join('?' * len(ACTIVE_STATUSES))
    overdue_active_all = conn.execute(
        f"SELECT COUNT(*) FROM requests r "
        f"WHERE r.status IN ({active_in}) "
        f"AND julianday('now')-julianday(r.request_date)>7",
        list(ACTIVE_STATUSES)
    ).fetchone()[0]

    # ─── СУММАРНЫЕ ПОКАЗАТЕЛИ ───────────────────────────
    sums = conn.execute(
        f"SELECT COALESCE(SUM(investment_total),0), COALESCE(SUM(jobs_total),0) "
        f"FROM requests r WHERE 1=1{pw_sql}", pw_params
    ).fetchone()

    # Среднее время ответа — считаем по закрытым
    avg_row = conn.execute(
        f"SELECT AVG(julianday(sent_to_applicant_at)-julianday(request_date)) "
        f"FROM requests r WHERE status='{CLOSED_STATUS}' "
        f"AND sent_to_applicant_at IS NOT NULL{pw_sql}",
        pw_params
    ).fetchone()

    # ─── KPI ПО СРОКАМ ────────────────────────────────────────────
    norm_total = 7
    active_kpi_in = ','.join('?' * len(ACTIVE_STATUSES))
    kpi = conn.execute(f"""
        SELECT COUNT(*),
        SUM(CASE WHEN julianday(sent_to_applicant_at)-julianday(request_date)<={norm_total} THEN 1 ELSE 0 END),
        SUM(CASE WHEN julianday(sent_to_applicant_at)-julianday(request_date)>{norm_total}  THEN 1 ELSE 0 END),
        SUM(CASE WHEN status IN ({active_kpi_in})
            AND julianday('now')-julianday(request_date)>{norm_total} THEN 1 ELSE 0 END)
        FROM requests r WHERE status='{CLOSED_STATUS}'{pw_sql}""",
        list(ACTIVE_STATUSES) + pw_params
    ).fetchone()

    kpi_data = {
        'norm_days':      norm_total,
        'total_answered': kpi[0] or 0,
        'in_time':        kpi[1] or 0,
        'overdue':        kpi[2] or 0,
        'overdue_active': overdue_active_all,
        'pct':            round(kpi[1] / kpi[0] * 100) if kpi[0] else 0,
    }

    # ─── ТРЕНД ПО ВРЕМЕНИ ───────────────────────────────────
    if period == 'today':
        tr = conn.execute(
            "SELECT strftime('%H:00',request_date),COUNT(*) "
            "FROM requests r WHERE 1=1" + pw_sql +
            " GROUP BY 1 ORDER BY 1", pw_params
        ).fetchall()
    elif period in ('week', 'month'):
        tr = conn.execute(
            "SELECT request_date,COUNT(*) "
            "FROM requests r WHERE 1=1" + pw_sql +
            " GROUP BY 1 ORDER BY 1", pw_params
        ).fetchall()
    elif period == 'quarter':
        tr = conn.execute(
            "SELECT strftime('%Y-W%W',request_date),COUNT(*) "
            "FROM requests r WHERE 1=1" + pw_sql +
            " GROUP BY 1 ORDER BY 1", pw_params
        ).fetchall()
    else:
        tr = conn.execute(
            "SELECT strftime('%Y-%m',request_date),COUNT(*) "
            "FROM requests r WHERE 1=1" + pw_sql +
            " GROUP BY 1 ORDER BY 1", pw_params
        ).fetchall()

    # ─── ТОП СОТРУДНИКОВ ─────────────────────────────────────────
    # GROUP BY по обоим полям — исключает слияние разных людей с одинаковым именем
    # LIMIT 20 — покрывает все возможные назначения (у нас 12 сотрудников)
    emp_rows = conn.execute(
        f"SELECT COALESCE(u.full_name,'Не назначен'),COUNT(*) FROM requests r "
        f"LEFT JOIN users u ON r.assigned_to=u.id WHERE 1=1{pw_sql} "
        f"GROUP BY r.assigned_to, u.full_name ORDER BY 2 DESC LIMIT 20", pw_params
    ).fetchall()

    # ─── РАЙОНЫ ──────────────────────────────────────────────────
    # preferred_districts может хранить несколько районов через запятую —
    # разбиваем в Python как source_type, затем берём топ-12
    dist_raw = conn.execute(
        f"SELECT preferred_districts FROM requests r "
        f"WHERE preferred_districts IS NOT NULL AND preferred_districts!=''{pw_sql}",
        pw_params
    ).fetchall()

    dist_counts = {}
    for row in dist_raw:
        for d in (row[0] or '').split(','):
            d = d.strip()
            if d:
                dist_counts[d] = dist_counts.get(d, 0) + 1

    dist_top = sorted(dist_counts.items(), key=lambda x: x[1], reverse=True)[:12]

    # ─── ТИП ПЛОЩАДКИ ──────────────────────────────────────
    st_free = conn.execute(
        f"SELECT COUNT(*) FROM requests r WHERE site_type_free=1{pw_sql}",
        pw_params
    ).fetchone()[0]
    st_ex = conn.execute(
        f"SELECT COUNT(*) FROM requests r WHERE site_type_existing=1{pw_sql}",
        pw_params
    ).fetchone()[0]
    st_both = conn.execute(
        f"SELECT COUNT(*) FROM requests r "
        f"WHERE site_type_free=1 AND site_type_existing=1{pw_sql}",
        pw_params
    ).fetchone()[0]

    # ─── РАСПРЕДЕЛЕНИЕ ПО ПЛОЩАДИ ────────────────────────
    # Один SELECT с CASE WHEN вместо 7 отдельных; COALESCE(_min,_max) учитывает записи
    # где заполнено только одно из полей
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

    # ─── ИСТОЧНИКИ ОБРАЩЕНИЙ ──────────────────────────
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

    # ─── ФИНАЛЬНЫЙ НАБОР ДАННЫХ ─────────────────────────────
    return {
        'period':            period,
        # Счётчики — всегда все записи без фильтра периода
        'total':             cnt_all(),
        'draft':             cnt_all('draft'),
        'registered':        cnt_all('registered'),
        'in_progress':       cnt_all('in_progress'),
        'under_review':      cnt_all('under_review'),
        'ready_to_send':     cnt_all('ready_to_send'),
        'sent_to_applicant': cnt_all('sent_to_applicant'),
        'closed':            cnt_all('closed'),
        # Активные с просрочкой — всегда без фильтра периода
        'overdue_active':    overdue_active_all,
        # Аналитика — за выбранный период
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
