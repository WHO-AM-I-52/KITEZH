import json
import sqlite3

import migrations
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
def test_fresh_history_schema_has_expected_tables_columns_and_indexes():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    create_analysis_history_tables(conn)
    create_analysis_history_tables(conn)

    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"portal_analysis_runs", "portal_analysis_site_snapshots"} <= tables

    snapshot_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(portal_analysis_site_snapshots)")
    }
    assert {
        "id",
        "run_id",
        "site_id",
        "site_name",
        "site_status",
        "is_included",
        "exclusion_reason",
        "score_percent",
        "required_fields_count",
        "filled_fields_count",
        "missing_fields_json",
        "skipped_fields_json",
        "field_values_hash",
        "error_message",
        "analysis_status",
        "previous_snapshot_id",
    } <= snapshot_columns

    indexes = {
        row["name"]
        for row in conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='index'
              AND tbl_name='portal_analysis_site_snapshots'
            """
        )
    }
    assert {
        "idx_portal_analysis_snapshots_site",
        "idx_portal_analysis_snapshots_run",
        "idx_portal_analysis_snapshots_included",
    } <= indexes


def test_legacy_history_schema_backfills_statuses_without_losing_rows():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
CREATE TABLE portal_analysis_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    formula_version TEXT NOT NULL,
    initiated_by INTEGER,
    source_label TEXT,
    total_sites INTEGER NOT NULL DEFAULT 0,
    active_sites INTEGER NOT NULL DEFAULT 0,
    excluded_sites INTEGER NOT NULL DEFAULT 0,
    error_sites INTEGER NOT NULL DEFAULT 0,
    average_score REAL,
    notes TEXT
);

CREATE TABLE portal_analysis_site_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES portal_analysis_runs(id) ON DELETE CASCADE,
    site_id TEXT NOT NULL,
    site_name TEXT,
    site_status TEXT,
    is_included INTEGER NOT NULL,
    exclusion_reason TEXT,
    score_percent INTEGER,
    required_fields_count INTEGER,
    filled_fields_count INTEGER,
    missing_fields_json TEXT NOT NULL DEFAULT '[]',
    skipped_fields_json TEXT NOT NULL DEFAULT '[]',
    field_values_hash TEXT,
    error_message TEXT,
    previous_snapshot_id INTEGER REFERENCES portal_analysis_site_snapshots(id),
    UNIQUE(run_id, site_id)
);
""")
    conn.execute(
        """
        INSERT INTO portal_analysis_runs (id, created_at, formula_version)
        VALUES (1, '2026-01-01T00:00:00+00:00', 'v2.0')
        """
    )
    conn.executemany(
        """
        INSERT INTO portal_analysis_site_snapshots (
            id, run_id, site_id, is_included, exclusion_reason,
            score_percent, missing_fields_json, field_values_hash,
            error_message, previous_snapshot_id
        ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (1, "ok-site", 1, None, 71, '["field_a"]', "hash-ok", None, None),
            (2, "excluded-site", 0, "продана", 42, "[]", "hash-excluded", None, 1),
            (3, "error-site", 1, None, 15, "[]", "hash-error", "Ошибка расчёта", 2),
        ],
    )

    create_analysis_history_tables(conn)
    create_analysis_history_tables(conn)

    rows = conn.execute(
        """
        SELECT
            id,
            analysis_status,
            score_percent,
            missing_fields_json,
            field_values_hash,
            previous_snapshot_id
        FROM portal_analysis_site_snapshots
        ORDER BY id
        """
    ).fetchall()

    assert [
        (row["id"], row["analysis_status"])
        for row in rows
    ] == [
        (1, "ok"),
        (2, "excluded"),
        (3, "error"),
    ]
    assert [
        (
            row["score_percent"],
            row["missing_fields_json"],
            row["field_values_hash"],
            row["previous_snapshot_id"],
        )
        for row in rows
    ] == [
        (71, '["field_a"]', "hash-ok", None),
        (42, "[]", "hash-excluded", 1),
        (15, "[]", "hash-error", 2),
    ]


def test_save_snapshot_sets_analysis_status():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_analysis_history_tables(conn)

    run_id = create_run(conn, "v2.0")
    ok_snapshot = save_snapshot(
        conn, run_id, "ok-site", "Свободная площадка", "Свободна"
    )
    excluded_snapshot = save_snapshot(
        conn, run_id, "excluded-site", "Проданная площадка", "Продана"
    )
    error_snapshot = save_snapshot(
        conn,
        run_id,
        "error-site",
        "Площадка с ошибкой",
        "Свободна",
        error_message="Ошибка расчёта",
    )

    statuses = {
        row["id"]: row["analysis_status"]
        for row in conn.execute(
            """
            SELECT id, analysis_status
            FROM portal_analysis_site_snapshots
            """
        )
    }
    assert statuses == {
        ok_snapshot: "ok",
        excluded_snapshot: "excluded",
        error_snapshot: "error",
    }


def test_init_and_migrate_db_use_history_bootstrap(monkeypatch, tmp_path):
    db_path = tmp_path / "history-bootstrap.db"
    calls = []

    def recording_bootstrap(conn):
        calls.append(conn)
        create_analysis_history_tables(conn)

    monkeypatch.setattr(migrations, "DB_PATH", str(db_path))
    monkeypatch.setattr(
        migrations,
        "create_analysis_history_tables",
        recording_bootstrap,
    )

    migrations.init_db()
    migrations.migrate_db()

    assert len(calls) == 2

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type='table'
                """
            )
        }
    finally:
        conn.close()

    assert {"portal_analysis_runs", "portal_analysis_site_snapshots"} <= tables
    
def test_save_snapshot_preserves_v2_missing_and_skipped_json():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_analysis_history_tables(conn)

    run_id = create_run(conn, "v2.1.0")

    missing = [
        {"field": "Инфраструктура", "hint": "Укажите расстояние до точки подключения"},
        {"field": "Транспорт", "hint": "Укажите расстояние до трассы"},
    ]
    skipped = [
        {"field": "Комментарий", "reason": "optional"},
    ]

    snapshot_id = save_snapshot(
        conn,
        run_id,
        "site-123",
        "Тестовая площадка",
        "Свободна",
        result={
            "score": 55,
            "filled": 2,
            "total": 4,
            "missing": missing,
            "skipped": skipped,
        },
    )

    row = conn.execute(
        """
        SELECT
            score_percent,
            filled_fields_count,
            required_fields_count,
            missing_fields_json,
            skipped_fields_json
        FROM portal_analysis_site_snapshots
        WHERE id = ?
        """,
        (snapshot_id,),
    ).fetchone()

    assert row["score_percent"] == 55
    assert row["filled_fields_count"] == 2
    assert row["required_fields_count"] == 4
    assert json.loads(row["missing_fields_json"]) == missing
    assert json.loads(row["skipped_fields_json"]) == skipped
