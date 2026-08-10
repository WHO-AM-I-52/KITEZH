"""Сводные данные истории запусков анализа V2."""

MANUAL_REFERENCE = {'sites': 616, 'average_score': 84.87, 'as_of': '2026-08-06'}


def get_history_summary(conn):
    runs = conn.execute('SELECT * FROM portal_analysis_runs ORDER BY id DESC LIMIT 2').fetchall()
    if not runs:
        return {'latest': None, 'previous': None, 'comparison': {}, 'manual_reference': MANUAL_REFERENCE}
    latest = runs[0]
    comparison = conn.execute("""
        SELECT
            SUM(CASE WHEN s.previous_snapshot_id IS NULL THEN 1 ELSE 0 END) AS new_sites,
            SUM(CASE WHEN p.field_values_hash != s.field_values_hash THEN 1 ELSE 0 END) AS changed_source_sites,
            SUM(CASE WHEN s.score_percent > p.score_percent THEN 1 ELSE 0 END) AS improved_sites,
            SUM(CASE WHEN s.score_percent < p.score_percent THEN 1 ELSE 0 END) AS worsened_sites,
            SUM(CASE WHEN s.is_included != p.is_included THEN 1 ELSE 0 END) AS changed_inclusion_sites
        FROM portal_analysis_site_snapshots s
        LEFT JOIN portal_analysis_site_snapshots p ON p.id = s.previous_snapshot_id
        WHERE s.run_id = ?
    """, (latest['id'],)).fetchone()
    return {'latest': dict(latest), 'previous': dict(runs[1]) if len(runs) > 1 else None, 'comparison': dict(comparison), 'manual_reference': MANUAL_REFERENCE}
