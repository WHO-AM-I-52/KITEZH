import sqlite3

from portal_analysis.analysis_history import create_analysis_history_tables
from portal_analysis.batch_analysis import analyze_and_save_rows


def _connection():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    create_analysis_history_tables(conn)
    return conn


def _site(status='Свободна', name='Тестовая площадка'):
    return {'global_id': '1001', 'Название площадки': name, 'Статус площадки': status}


def test_excluded_site_is_saved_but_not_scored():
    conn = _connection()
    summary = analyze_and_save_rows(conn, [_site('Продана')])
    snapshot = conn.execute('SELECT * FROM portal_analysis_site_snapshots').fetchone()
    assert summary['active_sites'] == 0
    assert summary['excluded_sites'] == 1
    assert snapshot['is_included'] == 0
    assert snapshot['score_percent'] is None
    assert snapshot['exclusion_reason'] == 'продана'


def test_second_run_links_previous_snapshot_and_detects_change():
    conn = _connection()
    first = analyze_and_save_rows(conn, [_site()])
    conn.commit()
    second = analyze_and_save_rows(conn, [_site(name='Обновлённая площадка')])
    snapshot = conn.execute('SELECT * FROM portal_analysis_site_snapshots WHERE run_id=?', (second['run_id'],)).fetchone()
    assert first['new_sites'] == 1
    assert second['changed_source_sites'] == 1
    assert snapshot['previous_snapshot_id'] is not None
