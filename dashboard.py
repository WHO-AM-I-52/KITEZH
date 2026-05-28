# ╔══════════════════════════════════════════════════════════════╗
# ║                       dashboard.py                           ║
# ║  Построение дашборда: KPI, графики, агрегаты по обращениям   ║
# ╚══════════════════════════════════════════════════════════════╝

from datetime import date, timedelta


def build_dash(conn, period):
    today = date.today()

    # ─── ФИЛЬТР ПО ПЕРИОДУ ───────────────────────────────────────────────────
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

    # ─── ОБЩЕЕ КОЛИЧЕСТВО ПО СТАТУСАМ + ПРОСРОЧКА (1 запрос) ────────────────
    # ВАЖНО: считается БЕЗ фильтра периода — счётчики всегда показывают все обращения
    status_row = conn.execute("""
        SELECT
            COUNT(*)                                                        AS total,
            SUM(status='draft')                                             AS draft,
            SUM(status='review')                                            AS review,
            SUM(status='accepted')                                          AS accepted,
            SUM(status='answered')                                          AS answered,
            SUM(status IN ('draft','review','accepted')
                AND julianday('now')-julianday(request_date)>7)             AS overdue_active
        FROM requests r
    """).fetchone()

    total_all        = status_row[0] or 0
    draft_all        = status_row[1] or 0
    review_all       = status_row[2] or 0
    accepted_all     = status_row[3] or 0
    answered_all     = status_row[4] or 0
    overdue_active_all = status_row[5] or 0

    # ─── СУММАРНЫЕ ПОКАЗАТЕЛИ ────────────────────────────────────────────────
    sums = conn.execute(
        f"SELECT COALESCE(SUM(investment_total),0), COALESCE(SUM(jobs_total),0) "
        f"FROM requests r WHERE 1=1{pw_sql}", pw_params
    ).fetchone()

    avg_row = conn.execute(
        f"SELECT AVG(julianday(answer_date)-julianday(request_date)) "
        f"FROM requests r WHERE status='answered' AND answer_date IS NOT NULL{pw_sql}",
        pw_params
    ).fetchone()

    # ─── KPI ПО СРОКАМ ───────────────────────────────────────────────────────
    norm_total = 7
    kpi = conn.execute(f"""
        SELECT COUNT(*),
        SUM(CASE WHEN julianday(answer_date)-julianday(request_date)<={norm_total} THEN 1 ELSE 0 END),
        SUM(CASE WHEN julianday(answer_date)-julianday(request_date)>{norm_total}  THEN 1 ELSE 0 END),
        SUM(CASE WHEN status IN ('draft','review','accepted')
            AND julianday('now')-julianday(request_date)>{norm_total} THEN 1 ELSE 0 END)
        FROM requests r WHERE 1=1{pw_sql}""", pw_params).fetchone()

    kpi_data = {
        'norm_days':      norm_total,
        'total_answered': kpi[0] or 0,
        'in_time':        kpi[1] or 0,
        'overdue':        kpi[2] or 0,
        'overdue_active': kpi[3] or 0,
        'pct':            round(kpi[1] / kpi[0] * 100) if kpi[0] else 0,
    }

    # ─── ТРЕНД ПО ВРЕМЕНИ ────────────────────────────────────────────────────
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

    # ─── ТОП СОТРУДНИКОВ И РАЙОНЫ ────────────────────────────────────────────
    emp_rows = conn.execute(
        f"SELECT COALESCE(u.full_name,'Не назначен'),COUNT(*) FROM requests r "
        f"LEFT JOIN users u ON r.assigned_to=u.id WHERE 1=1{pw_sql} "
        f"GROUP BY r.assigned_to ORDER BY 2 DESC LIMIT 10", pw_params
    ).fetchall()

    dist_rows = conn.execute(
        f"SELECT preferred_districts,COUNT(*) FROM requests r "
        f"WHERE preferred_districts IS NOT NULL AND preferred_districts!=''{pw_sql} "
        f"GROUP BY 1 ORDER BY 2 DESC LIMIT 12", pw_params
    ).fetchall()

    # ─── ТИП ПЛОЩАДКИ (1 запрос вместо 3) ───────────────────────────────────
    st_row = conn.execute(
        f"""SELECT
            SUM(site_type_free=1)                           AS free,
            SUM(site_type_existing=1)                       AS ex,
            SUM(site_type_free=1 AND site_type_existing=1)  AS both
        FROM requests r WHERE 1=1{pw_sql}""", pw_params
    ).fetchone()
    st_free = st_row[0] or 0
    st_ex   = st_row[1] or 0
    st_both = st_row[2] or 0

    # ─── РАСПРЕДЕЛЕНИЕ ПО ПЛОЩАДИ (1 запрос вместо 7) ───────────────────────
    area_row = conn.execute(
        f"""SELECT
            SUM(site_area_ha_min < 0.1)                                 AS b0,
            SUM(site_area_ha_min >= 0.1  AND site_area_ha_min < 0.5)    AS b1,
            SUM(site_area_ha_min >= 0.5  AND site_area_ha_min < 1)      AS b2,
            SUM(site_area_ha_min >= 1    AND site_area_ha_min < 2)      AS b3,
            SUM(site_area_ha_min >= 2    AND site_area_ha_min < 5)      AS b4,
            SUM(site_area_ha_min >= 5    AND site_area_ha_min < 10)     AS b5,
            SUM(site_area_ha_min >= 10)                                 AS b6
        FROM requests r WHERE site_area_ha_min IS NOT NULL{pw_sql}""", pw_params
    ).fetchone()

    area_data = [
        {'label': l, 'count': area_row[i] or 0}
        for i, l in enumerate(['<0.1 га', '0.1–0.5', '0.5–1', '1–2', '2–5', '5–10', '>10'])
    ]

    # ─── РАСПРЕДЕЛЕНИЕ ПО ПЛОЩАДИ ЗАСТРОЙКИ (1 запрос вместо 7) ─────────────
    build_row = conn.execute(
        f"""SELECT
            SUM(site_build_area_m2_min < 100)                                       AS b0,
            SUM(site_build_area_m2_min >= 100   AND site_build_area_m2_min < 300)   AS b1,
            SUM(site_build_area_m2_min >= 300   AND site_build_area_m2_min < 500)   AS b2,
            SUM(site_build_area_m2_min >= 500   AND site_build_area_m2_min < 1000)  AS b3,
            SUM(site_build_area_m2_min >= 1000  AND site_build_area_m2_min < 3000)  AS b4,
            SUM(site_build_area_m2_min >= 3000  AND site_build_area_m2_min < 5000)  AS b5,
            SUM(site_build_area_m2_min >= 5000)                                     AS b6
        FROM requests r WHERE site_build_area_m2_min IS NOT NULL{pw_sql}""", pw_params
    ).fetchone()

    build_data = [
        {'label': l, 'count': build_row[i] or 0}
        for i, l in enumerate(['<100 м²', '100–300', '300–500', '500–1000', '1000–3000', '3000–5000', '>5000'])
    ]

    # ─── ИСТОЧНИКИ ОБРАЩЕНИЙ ─────────────────────────────────────────────────
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

    # ─── ФИНАЛЬНЫЙ НАБОР ДАННЫХ ───────────────────────────────────────────────
    return {
        'period':         period,
        # Счётчики — всегда все записи без фильтра периода
        'total':          total_all,
        'draft':          draft_all,
        'review':         review_all,
        'accepted':       accepted_all,
        'answered':       answered_all,
        'overdue_active': overdue_active_all,
        # Аналитика — за выбранный период
        'investment_sum': float(sums[0]) if sums else 0,
        'jobs_sum':       int(sums[1]) if sums else 0,
        'avg_days':       round(avg_row[0]) if avg_row and avg_row[0] else None,
        'kpi':            kpi_data,
        'trend_chart':    {'labels': [r[0] for r in tr],        'values': [r[1] for r in tr]},
        'emp_chart':      {'labels': [r[0] for r in emp_rows],  'values': [r[1] for r in emp_rows]},
        'dist_chart':     {'labels': [r[0] for r in dist_rows], 'values': [r[1] for r in dist_rows]},
        'site_type': {
            'free':          st_free,
            'existing':      st_ex,
            'both':          st_both,
            'only_free':     st_free - st_both,
            'only_existing': st_ex  - st_both,
        },
        'area_data':      area_data,
        'build_data':     build_data,
        'source_chart':   {'labels': list(src_counts.keys()), 'values': list(src_counts.values())},
    }
