import sqlite3

import pytest

from portal_analysis.analysis_history import (
    create_analysis_history_tables,
    create_run,
    get_exclusion_reason,
    is_site_included,
    make_fields_hash,
    normalize_site_status,
    save_snapshot,
)


def test_normalize_site_status():
    assert normalize_site_status("  Предоставлена   в АРЕНДУ ") == "предоставлена в аренду"


@pytest.mark.parametrize(
    "status",
    ["Продана", "Предоставлена в аренду", "Использована для других целей", "Снята с реализации"],
)
def test_excluded_statuses(status):
    assert not is_site_included(status)
    assert get_exclusion_reason(status) is not None


def test_other_statuses_are_included():
    assert is_site_included("Свободна")
    assert is_site_included(None)


def test_fields_hash_is_stable():
    row = {"Название площадки": "Тест", "Статус площадки": "Свободна"}
    fields = ["Статус площадки", "Название площадки"]
    assert make_fields_hash(row, fields) == make_fields_hash(row, list(reversed(fields)))


def test_snapshot_links_to_previous_record():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_analysis_history_tables(conn)

    first_run = create_run(conn, "v2.0")
    first_snapshot = save_snapshot(
        conn, first_run, "1530550", "Контрольная площадка", "Свободна",
        {"score": 79, "filled": 79, "total": 100, "missing": [], "skipped": []},
    )
    second_run = create_run(conn, "v2.0")
    second_snapshot = save_snapshot(
        conn, second_run, "1530550", "Контрольная площадка", "Продана",
        {"score": 80, "filled": 80, "total": 100, "missing": [], "skipped": []},
    )

    snapshot = conn.execute(
        "SELECT is_included, previous_snapshot_id FROM portal_analysis_site_snapshots WHERE id=?",
        (second_snapshot,),
    ).fetchone()
    assert snapshot["is_included"] == 0
    assert snapshot["previous_snapshot_id"] == first_snapshot
