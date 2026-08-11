import json
import sqlite3

from portal_analysis.analysis_history import create_analysis_history_tables
from portal_analysis.batch_analysis import (
    analyze_and_save_rows,
    run_batch_history,
)


def _connection():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_analysis_history_tables(conn)
    return conn


def _site(
    global_id="1001",
    status="Свободна",
    name="Тестовая площадка",
):
    return {
        "global_id": global_id,
        "Название площадки": name,
        "Статус площадки": status,
    }


def _v2_result(
    score=75,
    filled=3,
    total=4,
    missing=None,
    skipped=None,
):
    return {
        "score": score,
        "filled": filled,
        "total": total,
        "missing": [] if missing is None else missing,
        "skipped": [] if skipped is None else skipped,
    }


def test_excluded_site_is_saved_but_not_scored():
    conn = _connection()

    summary = analyze_and_save_rows(conn, [_site(status="Продана")])

    snapshot = conn.execute(
        "SELECT * FROM portal_analysis_site_snapshots"
    ).fetchone()

    assert summary["active_sites"] == 0
    assert summary["excluded_sites"] == 1
    assert snapshot["is_included"] == 0
    assert snapshot["score_percent"] is None
    assert snapshot["exclusion_reason"] == "продана"


def test_second_run_links_previous_snapshot_and_detects_change():
    conn = _connection()

    first = analyze_and_save_rows(conn, [_site()])
    conn.commit()

    second = analyze_and_save_rows(
        conn,
        [_site(name="Обновлённая площадка")],
    )

    snapshot = conn.execute(
        """
        SELECT *
        FROM portal_analysis_site_snapshots
        WHERE run_id = ?
        """,
        (second["run_id"],),
    ).fetchone()

    assert first["new_sites"] == 1
    assert second["changed_source_sites"] == 1
    assert snapshot["previous_snapshot_id"] is not None


def test_batch_service_creates_one_run_and_two_snapshots():
    conn = _connection()
    calls = []

    def score_fn(row):
        calls.append(row["global_id"])
        return _v2_result(score=70 if row["global_id"] == "1001" else 80)

    result = run_batch_history(
        conn=conn,
        source_rows=[
            _site(global_id="1001"),
            _site(global_id="1002"),
        ],
        score_fn=score_fn,
        formula_version="v2.1.0",
        initiated_by=17,
        source_label="test-source",
    )

    run_count = conn.execute(
        "SELECT COUNT(*) FROM portal_analysis_runs"
    ).fetchone()[0]
    snapshot_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM portal_analysis_site_snapshots
        WHERE run_id = ?
        """,
        (result["run_id"],),
    ).fetchone()[0]

    assert run_count == 1
    assert snapshot_count == 2
    assert result["total_sites"] == 2
    assert result["saved_sites"] == 2
    assert result["active_sites"] == 2
    assert result["excluded_sites"] == 0
    assert result["error_sites"] == 0
    assert result["errors"] == []
    assert calls == ["1001", "1002"]


def test_batch_service_calls_score_fn_once_for_each_valid_unique_row():
    conn = _connection()
    calls = []

    def score_fn(row):
        calls.append(row["global_id"])
        return _v2_result()

    result = run_batch_history(
        conn=conn,
        source_rows=[
            _site(global_id="1001"),
            _site(global_id="1002", status="Продана"),
        ],
        score_fn=score_fn,
        formula_version="v2.1.0",
    )

    assert calls == ["1001", "1002"]
    assert result["active_sites"] == 1
    assert result["excluded_sites"] == 1
    assert result["saved_sites"] == 2


def test_batch_service_preserves_v2_missing_field_and_hint_json():
    conn = _connection()
    expected_missing = [
        {
            "field": "Инфраструктура",
            "hint": "Укажите расстояние до точки подключения",
        }
    ]
    expected_skipped = [
        {
            "field": "Комментарий",
            "reason": "optional",
        }
    ]

    result = run_batch_history(
        conn=conn,
        source_rows=[_site()],
        score_fn=lambda row: _v2_result(
            score=50,
            filled=2,
            total=4,
            missing=expected_missing,
            skipped=expected_skipped,
        ),
        formula_version="v2.1.0",
    )

    snapshot = conn.execute(
        """
        SELECT
            score_percent,
            filled_fields_count,
            required_fields_count,
            missing_fields_json,
            skipped_fields_json
        FROM portal_analysis_site_snapshots
        WHERE run_id = ?
        """,
        (result["run_id"],),
    ).fetchone()

    assert snapshot["score_percent"] == 50
    assert snapshot["filled_fields_count"] == 2
    assert snapshot["required_fields_count"] == 4
    assert json.loads(snapshot["missing_fields_json"]) == expected_missing
    assert json.loads(snapshot["skipped_fields_json"]) == expected_skipped


def test_scorer_error_does_not_stop_two_valid_rows():
    conn = _connection()
    calls = []

    def score_fn(row):
        site_id = row["global_id"]
        calls.append(site_id)
        if site_id == "1002":
            raise ValueError("Ошибка расчёта 1002")
        return _v2_result(score=70 if site_id == "1001" else 90)

    result = run_batch_history(
        conn=conn,
        source_rows=[
            _site(global_id="1001"),
            _site(global_id="1002"),
            _site(global_id="1003"),
        ],
        score_fn=score_fn,
        formula_version="v2.1.0",
    )

    snapshots = conn.execute(
        """
        SELECT site_id, analysis_status, error_message
        FROM portal_analysis_site_snapshots
        WHERE run_id = ?
        ORDER BY site_id
        """,
        (result["run_id"],),
    ).fetchall()

    assert calls == ["1001", "1002", "1003"]
    assert result["error_sites"] == 1
    assert result["saved_sites"] == 3
    assert len(result["errors"]) == 1
    assert result["errors"][0]["site_id"] == "1002"
    assert result["errors"][0]["error_type"] == "score_or_snapshot_error"
    assert result["results"][1]["analysis_status"] == "error"
    assert result["results"][1]["error"] == "Ошибка расчёта 1002"
    assert [
        (row["site_id"], row["analysis_status"])
        for row in snapshots
    ] == [
        ("1001", "ok"),
        ("1002", "error"),
        ("1003", "ok"),
    ]


def test_empty_global_id_is_error_without_snapshot():
    conn = _connection()
    calls = []

    def score_fn(row):
        calls.append(row)
        return _v2_result()

    result = run_batch_history(
        conn=conn,
        source_rows=[_site(global_id="   ")],
        score_fn=score_fn,
        formula_version="v2.1.0",
    )

    snapshot_count = conn.execute(
        "SELECT COUNT(*) FROM portal_analysis_site_snapshots"
    ).fetchone()[0]

    assert calls == []
    assert result["saved_sites"] == 0
    assert result["error_sites"] == 1
    assert snapshot_count == 0
    assert result["errors"] == [
        {
            "row_index": 0,
            "site_id": "",
            "error_type": "invalid_global_id",
            "message": "Пустой или некорректный global_id",
        }
    ]


def test_duplicate_global_id_creates_one_snapshot_and_one_error():
    conn = _connection()
    calls = []

    def score_fn(row):
        calls.append(row["global_id"])
        return _v2_result()

    result = run_batch_history(
        conn=conn,
        source_rows=[
            _site(global_id="1001", name="Первая площадка"),
            _site(global_id="1001", name="Повторная площадка"),
        ],
        score_fn=score_fn,
        formula_version="v2.1.0",
    )

    snapshots = conn.execute(
        """
        SELECT site_id, site_name
        FROM portal_analysis_site_snapshots
        WHERE run_id = ?
        """,
        (result["run_id"],),
    ).fetchall()

    assert calls == ["1001"]
    assert result["saved_sites"] == 1
    assert result["error_sites"] == 1
    assert len(snapshots) == 1
    assert snapshots[0]["site_id"] == "1001"
    assert snapshots[0]["site_name"] == "Первая площадка"
    assert result["errors"][0]["error_type"] == "duplicate_global_id"
    assert result["errors"][0]["site_id"] == "1001"


class _TrackingConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commit_calls = 0
        self.rollback_calls = 0
        self.close_calls = 0

    def commit(self):
        self.commit_calls += 1
        return super().commit()

    def rollback(self):
        self.rollback_calls += 1
        return super().rollback()

    def close(self):
        self.close_calls += 1
        return super().close()


def test_batch_service_does_not_manage_caller_connection_lifecycle():
    conn = sqlite3.connect(":memory:", factory=_TrackingConnection)
    conn.row_factory = sqlite3.Row
    create_analysis_history_tables(conn)

    result = run_batch_history(
        conn=conn,
        source_rows=[_site()],
        score_fn=lambda row: _v2_result(),
        formula_version="v2.1.0",
    )

    assert result["saved_sites"] == 1
    assert conn.commit_calls == 0
    assert conn.rollback_calls == 0
    assert conn.close_calls == 0


def test_formula_version_is_saved_in_run():
    conn = _connection()

    result = run_batch_history(
        conn=conn,
        source_rows=[_site()],
        score_fn=lambda row: _v2_result(),
        formula_version="v2.7.3-test",
        initiated_by=42,
        source_label="v2-test-import",
    )

    run = conn.execute(
        """
        SELECT formula_version, initiated_by, source_label
        FROM portal_analysis_runs
        WHERE id = ?
        """,
        (result["run_id"],),
    ).fetchone()

    assert run["formula_version"] == "v2.7.3-test"
    assert run["initiated_by"] == 42
    assert run["source_label"] == "v2-test-import"


def test_previous_snapshot_id_links_same_global_id_between_runs():
    conn = _connection()

    first = run_batch_history(
        conn=conn,
        source_rows=[_site(global_id="1001")],
        score_fn=lambda row: _v2_result(score=70),
        formula_version="v2.1.0",
    )
    second = run_batch_history(
        conn=conn,
        source_rows=[_site(global_id="1001")],
        score_fn=lambda row: _v2_result(score=80),
        formula_version="v2.1.0",
    )

    first_snapshot = conn.execute(
        """
        SELECT id
        FROM portal_analysis_site_snapshots
        WHERE run_id = ?
        """,
        (first["run_id"],),
    ).fetchone()
    second_snapshot = conn.execute(
        """
        SELECT previous_snapshot_id
        FROM portal_analysis_site_snapshots
        WHERE run_id = ?
        """,
        (second["run_id"],),
    ).fetchone()

    assert second_snapshot["previous_snapshot_id"] == first_snapshot["id"]

def test_batch_results_preserve_order_for_invalid_global_id():
    conn = _connection()
    calls = []

    def score_fn(row):
        calls.append(row["global_id"])
        return _v2_result(score=70 if row["global_id"] == "1001" else 80)

    result = run_batch_history(
        conn=conn,
        source_rows=[
            _site(global_id="1001"),
            _site(global_id="   "),
            _site(global_id="1002"),
        ],
        score_fn=score_fn,
        formula_version="v2.1.0",
    )

    snapshot_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM portal_analysis_site_snapshots
        WHERE run_id = ?
        """,
        (result["run_id"],),
    ).fetchone()[0]

    assert calls == ["1001", "1002"]
    assert snapshot_count == 2
    assert len(result["results"]) == 3
    assert [item["row_index"] for item in result["results"]] == [0, 1, 2]
    assert [item["analysis_status"] for item in result["results"]] == [
        "ok",
        "invalid_id",
        "ok",
    ]

    invalid_item = result["results"][1]
    assert invalid_item["site_id"] == ""
    assert invalid_item["included"] is None
    assert invalid_item["snapshot_saved"] is False
    assert invalid_item["result"] is None
    assert invalid_item["error"] == "Пустой или некорректный global_id"


def test_batch_results_preserve_order_for_duplicate_global_id():
    conn = _connection()
    calls = []

    def score_fn(row):
        calls.append(row["global_id"])
        return _v2_result(score=70 if row["global_id"] == "1001" else 80)

    result = run_batch_history(
        conn=conn,
        source_rows=[
            _site(global_id="1001", name="Первая"),
            _site(global_id="1001", name="Повтор"),
            _site(global_id="1002", name="Третья"),
        ],
        score_fn=score_fn,
        formula_version="v2.1.0",
    )

    snapshots = conn.execute(
        """
        SELECT site_id, site_name
        FROM portal_analysis_site_snapshots
        WHERE run_id = ?
        ORDER BY site_id
        """,
        (result["run_id"],),
    ).fetchall()

    assert calls == ["1001", "1002"]
    assert len(snapshots) == 2
    assert [
        (row["site_id"], row["site_name"])
        for row in snapshots
    ] == [
        ("1001", "Первая"),
        ("1002", "Третья"),
    ]

    assert len(result["results"]) == 3
    assert [item["row_index"] for item in result["results"]] == [0, 1, 2]
    assert [item["analysis_status"] for item in result["results"]] == [
        "ok",
        "error",
        "ok",
    ]

    duplicate_item = result["results"][1]
    assert duplicate_item["site_id"] == "1001"
    assert duplicate_item["included"] is None
    assert duplicate_item["snapshot_saved"] is False
    assert duplicate_item["result"] is None
    assert duplicate_item["error"] == "Повторный global_id в одном пакете"
