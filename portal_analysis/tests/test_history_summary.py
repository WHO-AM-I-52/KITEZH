import sqlite3
import unittest

from portal_analysis.analysis_history import create_analysis_history_tables
from portal_analysis.history_summary import (
    get_history_summary,
    get_immediately_previous_run_metadata,
    get_latest_run_metadata,
    get_paginated_run_changes,
    get_run_comparison,
)


class HistorySummaryTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        create_analysis_history_tables(self.conn)

    def tearDown(self):
        self.conn.close()

    def add_run(self, created_at, formula_version="2.0.0"):
        return self.conn.execute(
            """INSERT INTO portal_analysis_runs
               (created_at, formula_version)
               VALUES (?, ?)""",
            (created_at, formula_version),
        ).lastrowid

    def add_snapshot(
        self,
        run_id,
        site_id,
        *,
        score=50,
        included=1,
        status="ok",
        fields_hash="same",
        error_message=None,
    ):
        self.conn.execute(
            """INSERT INTO portal_analysis_site_snapshots (
                run_id, site_id, is_included, score_percent,
                field_values_hash, error_message, analysis_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                site_id,
                included,
                score,
                fields_hash,
                error_message,
                status,
            ),
        )

    def add_pair(self, formula_version="2.0.0"):
        previous = self.add_run(
            "2026-08-01T00:00:00+00:00",
            formula_version,
        )
        current = self.add_run(
            "2026-08-02T00:00:00+00:00",
            formula_version,
        )
        return previous, current

    def test_zero_runs_and_one_run(self):
        self.assertIsNone(get_latest_run_metadata(self.conn))
        self.assertEqual(get_history_summary(self.conn)["latest"], None)

        run_id = self.add_run("2026-08-01T00:00:00+00:00")
        self.assertEqual(get_latest_run_metadata(self.conn)["id"], run_id)
        self.assertIsNone(
            get_immediately_previous_run_metadata(self.conn, run_id)
        )

    def test_formula_mismatch_has_no_comparison(self):
        previous = self.add_run(
            "2026-08-01T00:00:00+00:00",
            "2.0.0",
        )
        current = self.add_run(
            "2026-08-02T00:00:00+00:00",
            "2.1.0",
        )
        self.add_snapshot(previous, "old")
        self.add_snapshot(current, "new")

        comparison = get_run_comparison(self.conn, current, previous)
        self.assertFalse(comparison["comparison_available"])
        self.assertEqual(comparison["changes_total"], 0)
        self.assertEqual(
            get_paginated_run_changes(self.conn, current, previous)["items"],
            [],
        )

    def test_change_kinds_and_primary_priority(self):
        previous, current = self.add_pair()
        self.add_snapshot(previous, "removed")
        self.add_snapshot(current, "new")
        self.add_snapshot(previous, "source", fields_hash="old")
        self.add_snapshot(current, "source", fields_hash="new")
        self.add_snapshot(previous, "up", score=20, fields_hash="old")
        self.add_snapshot(current, "up", score=30, fields_hash="new")
        self.add_snapshot(previous, "down", score=30)
        self.add_snapshot(current, "down", score=20)
        self.add_snapshot(previous, "out", included=1)
        self.add_snapshot(current, "out", included=0)
        self.add_snapshot(previous, "back", included=0)
        self.add_snapshot(current, "back", included=1)
        self.add_snapshot(
            previous,
            "error",
            status="error",
            error_message="private traceback",
        )
        self.add_snapshot(current, "error")

        result = get_paginated_run_changes(
            self.conn,
            current,
            previous,
            limit=100,
        )
        items = {item["site_id"]: item for item in result["items"]}

        self.assertEqual(result["total"], 8)
        self.assertEqual(len(items), 8)
        self.assertEqual(items["up"]["primary_kind"], "improved")
        self.assertTrue(items["up"]["changed_source"])
        self.assertEqual(items["error"]["primary_kind"], "error")
        self.assertTrue(items["out"]["changed_inclusion"])
        self.assertFalse(items["out"]["improved"])
        self.assertFalse(items["out"]["worsened"])
        self.assertTrue(items["back"]["changed_inclusion"])
        self.assertFalse(items["back"]["improved"])
        self.assertFalse(items["back"]["worsened"])

        comparison = get_run_comparison(self.conn, current, previous)
        self.assertEqual(comparison["changes_total"], 8)
        self.assertEqual(comparison["changed_inclusion_sites"], 2)
        self.assertEqual(comparison["new_sites"], 1)
        self.assertEqual(comparison["removed_sites"], 1)
        self.assertEqual(comparison["improved_sites"], 1)
        self.assertEqual(comparison["worsened_sites"], 1)

    def test_filters_pagination_and_payload_hygiene(self):
        previous, current = self.add_pair()
        for site_id in ("a", "b", "c"):
            self.add_snapshot(current, site_id)

        filtered = get_paginated_run_changes(
            self.conn,
            current,
            previous,
            kind="new",
            limit=2,
        )
        self.assertEqual(filtered["total"], 3)
        self.assertEqual(len(filtered["items"]), 2)

        page = get_paginated_run_changes(
            self.conn,
            current,
            previous,
            kind="new",
            limit=1,
            offset=1,
        )
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(
            get_paginated_run_changes(
                self.conn,
                current,
                previous,
                limit=999,
            )["limit"],
            100,
        )

        forbidden = {
            "missing_fields_json",
            "skipped_fields_json",
            "error_message",
            "previous_snapshot_id",
            "field_values_hash",
        }
        self.assertFalse(forbidden & set(filtered["items"][0]))

        self.assertEqual(
            get_paginated_run_changes(
                self.conn,
                current,
                previous,
                kind="removed",
            )["total"],
            0,
        )

    def test_invalid_pagination_and_non_adjacent_pair(self):
        first = self.add_run("2026-08-01T00:00:00+00:00")
        self.add_run("2026-08-02T00:00:00+00:00")
        latest = self.add_run("2026-08-03T00:00:00+00:00")

        with self.assertRaises(ValueError):
            get_run_comparison(self.conn, latest, first)

        previous = get_immediately_previous_run_metadata(self.conn, latest)
        for kwargs in (
            {"kind": "bad"},
            {"limit": 0},
            {"offset": -1},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    get_paginated_run_changes(
                        self.conn,
                        latest,
                        previous["id"],
                        **kwargs,
                    )


if __name__ == "__main__":
    unittest.main()
