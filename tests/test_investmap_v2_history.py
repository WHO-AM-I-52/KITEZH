import io
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask, g

from portal_analysis.analysis_history import create_analysis_history_tables
from routes import investmap_routes


class InvestmapV2HistoryIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = f"{self.temp_dir.name}/investmap-v2-history-test.db"
        self._prepare_test_db()

        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="investmap-v2-history-test-secret",
        )
        self.app.register_blueprint(investmap_routes.investmap_bp)

        @self.app.before_request
        def set_test_user():
            g.user = {
                "id": 1,
                "login": "investmap-v2-test-user",
            }

        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = 1
            session["role"] = "admin"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _open_test_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _prepare_test_db(self):
        conn = self._open_test_db()
        try:
            create_analysis_history_tables(conn)
            conn.execute(
                """
                CREATE TABLE activity_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    action TEXT NOT NULL,
                    request_id INTEGER,
                    detail TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _post_v2(self, data, texts, score_fn, log_action_side_effect=None):
        export_result = {
            "format": 2,
            "count": len(data),
            "data": data,
            "texts": texts,
        }

        log_patch_kwargs = {}
        if log_action_side_effect is not None:
            log_patch_kwargs["side_effect"] = log_action_side_effect

        with (
            patch.object(
                investmap_routes,
                "convert_excel_to_text",
                return_value=export_result,
            ),
            patch.object(
                investmap_routes,
                "calc_portal_score_v2",
                side_effect=score_fn,
            ) as scorer,
            patch.object(
                investmap_routes,
                "get_db",
                side_effect=self._open_test_db,
            ),
            patch.object(
                investmap_routes,
                "log_action",
                **log_patch_kwargs,
            ) as log_action_mock,
        ):
            if log_action_side_effect is None:
                log_action_mock.return_value = True

            response = self.client.post(
                "/investmap/v2",
                data={
                    "file": (
                        io.BytesIO(b"test-xlsx-content"),
                        "investmap-history.xlsx",
                    ),
                },
                content_type="multipart/form-data",
            )

        return response, scorer, log_action_mock

    @staticmethod
    def _result(score):
        return {
            "score": score,
            "filled": 3,
            "total": 4,
            "missing": [
                {
                    "field": "Геопривязка",
                    "hint": "Укажите координаты.",
                }
            ],
            "skipped": [],
        }

    def _get_history(self, path):
        with patch.object(
            investmap_routes,
            "get_db",
            side_effect=self._open_test_db,
        ):
            return self.client.get(path)

    def _create_history_run(
        self,
        created_at,
        formula_version="2.0.0",
        source_label="history.xlsx",
    ):
        conn = self._open_test_db()
        try:
            cursor = conn.execute(
                """
                INSERT INTO portal_analysis_runs (
                    created_at,
                    formula_version,
                    source_label
                ) VALUES (?, ?, ?)
                """,
                (
                    created_at,
                    formula_version,
                    source_label,
                ),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def _create_history_snapshot(
        self,
        run_id,
        site_id,
        *,
        score=50,
        included=1,
        analysis_status="ok",
        fields_hash="same",
        error_message=None,
    ):
        conn = self._open_test_db()
        try:
            conn.execute(
                """
                INSERT INTO portal_analysis_site_snapshots (
                    run_id,
                    site_id,
                    is_included,
                    score_percent,
                    field_values_hash,
                    error_message,
                    analysis_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    site_id,
                    included,
                    score,
                    fields_hash,
                    error_message,
                    analysis_status,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def test_history_endpoint_handles_no_runs_and_one_run(self):
        response = self._get_history("/investmap/v2/history")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIsNone(payload["latest"])
        self.assertIsNone(payload["previous"])
        self.assertFalse(payload["comparison"]["available"])
        self.assertEqual(payload["comparison"]["reason"], "no_runs")

        run_id = self._create_history_run(
            "2026-08-12T10:00:00+00:00",
        )

        response = self._get_history("/investmap/v2/history")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["latest"]["id"], run_id)
        self.assertIsNone(payload["previous"])
        self.assertFalse(payload["comparison"]["available"])
        self.assertEqual(
            payload["comparison"]["reason"],
            "no_previous_run",
        )

    def test_history_endpoint_returns_adjacent_runs_and_formula_mismatch(self):
        previous_id = self._create_history_run(
            "2026-08-11T10:00:00+00:00",
            formula_version="2.0.0",
            source_label="previous.xlsx",
        )
        current_id = self._create_history_run(
            "2026-08-12T10:00:00+00:00",
            formula_version="2.0.0",
            source_label="current.xlsx",
        )
        self._create_history_snapshot(previous_id, "1001", score=60)
        self._create_history_snapshot(current_id, "1001", score=80)

        response = self._get_history("/investmap/v2/history")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["latest"]["id"], current_id)
        self.assertEqual(payload["previous"]["id"], previous_id)
        self.assertTrue(payload["comparison"]["available"])
        self.assertIsNone(payload["comparison"]["reason"])
        self.assertEqual(payload["comparison"]["improved_sites"], 1)

        mismatch_id = self._create_history_run(
            "2026-08-13T10:00:00+00:00",
            formula_version="2.1.0",
            source_label="mismatch.xlsx",
        )

        response = self._get_history("/investmap/v2/history")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["latest"]["id"], mismatch_id)
        self.assertEqual(payload["previous"]["id"], current_id)
        self.assertFalse(payload["comparison"]["available"])
        self.assertEqual(
            payload["comparison"]["reason"],
            "formula_version_mismatch",
        )

    def test_history_changes_endpoint_contracts_and_compact_payload(self):
        response = self._get_history(
            "/investmap/v2/history/runs/999/changes",
        )
        self.assertEqual(response.status_code, 404)

        first_id = self._create_history_run(
            "2026-08-11T10:00:00+00:00",
        )

        response = self._get_history(
            f"/investmap/v2/history/runs/{first_id}/changes",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.get_json()["reason"],
            "no_previous_run",
        )

        second_id = self._create_history_run(
            "2026-08-12T10:00:00+00:00",
        )
        self._create_history_snapshot(
            first_id,
            "1001",
            score=50,
            fields_hash="previous",
        )
        self._create_history_snapshot(
            second_id,
            "1001",
            score=70,
            fields_hash="current",
        )

        response = self._get_history(
            f"/investmap/v2/history/runs/{second_id}/changes"
            "?kind=all&limit=1&offset=0",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["limit"], 1)
        self.assertEqual(payload["offset"], 0)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(len(payload["items"]), 1)
        self.assertEqual(payload["items"][0]["site_id"], "1001")
        self.assertEqual(payload["items"][0]["primary_kind"], "improved")

        forbidden = {
            "error_message",
            "missing_fields_json",
            "skipped_fields_json",
            "previous_snapshot_id",
            "field_values_hash",
        }
        self.assertFalse(forbidden & set(payload["items"][0]))

        for query in (
            "?kind=bad",
            "?limit=0",
            "?offset=-1",
            "?offset=not-a-number",
        ):
            with self.subTest(query=query):
                response = self._get_history(
                    f"/investmap/v2/history/runs/{second_id}/changes"
                    f"{query}",
                )
                self.assertEqual(response.status_code, 400)

        capped_response = self._get_history(
            f"/investmap/v2/history/runs/{second_id}/changes?limit=101",
        )
        self.assertEqual(capped_response.status_code, 200)
        self.assertEqual(
            capped_response.get_json()["limit"],
            100,
        )
                mismatch_id = self._create_history_run(
            "2026-08-13T10:00:00+00:00",
            formula_version="2.1.0",
        )

        response = self._get_history(
            f"/investmap/v2/history/runs/{mismatch_id}/changes",
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.get_json()["reason"],
            "formula_version_mismatch",
        )
    
    def test_batch_creates_one_run_snapshots_and_preserves_result_order(self):
        calls = []

        def score_fn(row, db):
            calls.append(row["global_id"])
            return self._result(80 if row["global_id"] == "1001" else 60)

        data = [
            {"global_id": "1001", "Статус площадки": "Свободна"},
            {"global_id": "1002", "Статус площадки": "Свободна"},
        ]
        texts = ["Текст 1001", "Текст 1002"]

        response, scorer, log_action_mock = self._post_v2(
            data,
            texts,
            score_fn,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertEqual(calls, ["1001", "1002"])
        self.assertEqual(scorer.call_count, 2)
        self.assertEqual(log_action_mock.call_count, 1)

        self.assertEqual(payload["count"], 2)
        self.assertEqual(len(payload["results"]), 2)
        self.assertEqual(payload["export"]["texts"], texts)
        self.assertEqual(payload["history"]["total_sites"], 2)
        self.assertEqual(payload["history"]["active_sites"], 2)
        self.assertEqual(payload["history"]["excluded_sites"], 0)
        self.assertEqual(payload["history"]["error_sites"], 0)

        self.assertEqual(
            [result["score"] for result in payload["results"]],
            [80, 60],
        )
        self.assertEqual(
            [result["analysis_status"] for result in payload["results"]],
            ["ok", "ok"],
        )
        self.assertEqual(
            [result["is_included"] for result in payload["results"]],
            [True, True],
        )

        conn = self._open_test_db()
        try:
            run = conn.execute(
                """
                SELECT formula_version, initiated_by, source_label
                FROM portal_analysis_runs
                WHERE id = ?
                """,
                (payload["history"]["run_id"],),
            ).fetchone()
            snapshots = conn.execute(
                """
                SELECT site_id, score_percent, analysis_status
                FROM portal_analysis_site_snapshots
                WHERE run_id = ?
                ORDER BY site_id
                """,
                (payload["history"]["run_id"],),
            ).fetchall()
        finally:
            conn.close()

        self.assertEqual(run["formula_version"], "2.0.0")
        self.assertEqual(run["initiated_by"], 1)
        self.assertEqual(run["source_label"], "investmap-history.xlsx")
        self.assertEqual(
            [
                (
                    row["site_id"],
                    row["score_percent"],
                    row["analysis_status"],
                )
                for row in snapshots
            ],
            [
                ("1001", 80, "ok"),
                ("1002", 60, "ok"),
            ],
        )

    def test_invalid_row_keeps_results_and_texts_index_alignment(self):
        calls = []

        def score_fn(row, db):
            calls.append(row["global_id"])
            return self._result(80 if row["global_id"] == "1001" else 60)

        data = [
            {"global_id": "1001", "Статус площадки": "Свободна"},
            {"global_id": "   ", "Статус площадки": "Свободна"},
            {"global_id": "1002", "Статус площадки": "Свободна"},
        ]
        texts = ["Текст 1001", "Пустой ID", "Текст 1002"]

        response, scorer, _ = self._post_v2(data, texts, score_fn)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertEqual(calls, ["1001", "1002"])
        self.assertEqual(scorer.call_count, 2)

        self.assertEqual(payload["count"], 3)
        self.assertEqual(len(payload["results"]), 3)
        self.assertEqual(payload["export"]["texts"], texts)
        self.assertEqual(payload["history"]["total_sites"], 3)
        self.assertEqual(payload["history"]["active_sites"], 2)
        self.assertEqual(payload["history"]["error_sites"], 1)

        invalid_result = payload["results"][1]
        self.assertIsNone(invalid_result["score"])
        self.assertEqual(invalid_result["filled"], 0)
        self.assertEqual(invalid_result["total"], 0)
        self.assertEqual(invalid_result["missing"], [])
        self.assertEqual(invalid_result["skipped"], [])
        self.assertFalse(invalid_result["is_included"])
        self.assertEqual(invalid_result["analysis_status"], "invalid_id")
        self.assertEqual(
            invalid_result["error"],
            "Пустой или некорректный global_id",
        )

        self.assertIsNotNone(payload["summary_sms"])
        self.assertIn("Проверено площадок: 2.", payload["summary_sms"])
        self.assertNotIn("Пустой или некорректный global_id", payload["summary_sms"])

    def test_excluded_site_keeps_snapshot_but_is_not_in_sms_or_average(self):
        def score_fn(row, db):
            scores = {
                "1001": 80,
                "1002": 20,
                "1003": 60,
            }
            return self._result(scores[row["global_id"]])

        data = [
            {"global_id": "1001", "Статус площадки": "Свободна"},
            {"global_id": "1002", "Статус площадки": "Продана"},
            {"global_id": "1003", "Статус площадки": "Свободна"},
        ]
        texts = ["Текст 1001", "Текст 1002", "Текст 1003"]

        response, scorer, _ = self._post_v2(data, texts, score_fn)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertEqual(scorer.call_count, 3)
        self.assertEqual(payload["history"]["active_sites"], 2)
        self.assertEqual(payload["history"]["excluded_sites"], 1)
        self.assertEqual(payload["results"][1]["score"], 20)
        self.assertFalse(payload["results"][1]["is_included"])
        self.assertEqual(
            payload["results"][1]["analysis_status"],
            "excluded",
        )

        self.assertIsNotNone(payload["summary_sms"])
        self.assertIn("Проверено площадок: 2.", payload["summary_sms"])
        self.assertNotIn("ID 1002", payload["summary_sms"])

        conn = self._open_test_db()
        try:
            run = conn.execute(
                """
                SELECT average_score
                FROM portal_analysis_runs
                WHERE id = ?
                """,
                (payload["history"]["run_id"],),
            ).fetchone()
            excluded_snapshot = conn.execute(
                """
                SELECT
                    is_included,
                    exclusion_reason,
                    score_percent,
                    analysis_status
                FROM portal_analysis_site_snapshots
                WHERE run_id = ? AND site_id = ?
                """,
                (payload["history"]["run_id"], "1002"),
            ).fetchone()
        finally:
            conn.close()

        self.assertEqual(run["average_score"], 70.0)
        self.assertEqual(excluded_snapshot["is_included"], 0)
        self.assertEqual(excluded_snapshot["exclusion_reason"], "продана")
        self.assertEqual(excluded_snapshot["score_percent"], 20)
        self.assertEqual(excluded_snapshot["analysis_status"], "excluded")

    def test_scorer_error_does_not_fail_batch(self):
        def score_fn(row, db):
            if row["global_id"] == "1002":
                raise ValueError("Ошибка V2 scorer")
            return self._result(80 if row["global_id"] == "1001" else 60)

        data = [
            {"global_id": "1001", "Статус площадки": "Свободна"},
            {"global_id": "1002", "Статус площадки": "Свободна"},
            {"global_id": "1003", "Статус площадки": "Свободна"},
        ]
        texts = ["Текст 1001", "Ошибка 1002", "Текст 1003"]

        response, scorer, _ = self._post_v2(data, texts, score_fn)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertEqual(scorer.call_count, 3)
        self.assertEqual(payload["history"]["error_sites"], 1)
        self.assertEqual(len(payload["results"]), 3)
        self.assertIsNone(payload["results"][1]["score"])
        self.assertEqual(payload["results"][1]["analysis_status"], "error")
        self.assertEqual(payload["results"][1]["error"], "Ошибка V2 scorer")

        self.assertIsNotNone(payload["summary_sms"])
        self.assertIn("Проверено площадок: 2.", payload["summary_sms"])
        self.assertNotIn("Ошибка V2 scorer", payload["summary_sms"])

    def test_log_action_failure_rolls_back_history_run(self):
        data = [
            {"global_id": "1001", "Статус площадки": "Свободна"},
            {"global_id": "1002", "Статус площадки": "Свободна"},
        ]
        texts = ["Текст 1001", "Текст 1002"]

        response, scorer, log_action_mock = self._post_v2(
            data,
            texts,
            score_fn=lambda row, db: self._result(80),
            log_action_side_effect=RuntimeError("activity log failure"),
        )

        self.assertEqual(response.status_code, 500)
        payload = response.get_json()

        self.assertEqual(payload["results"], [])
        self.assertEqual(payload["count"], 0)
        self.assertEqual(payload["error"], "Внутренняя ошибка сервера")
        self.assertEqual(scorer.call_count, 2)
        self.assertEqual(log_action_mock.call_count, 1)

        conn = self._open_test_db()
        try:
            run_count = conn.execute(
                "SELECT COUNT(*) FROM portal_analysis_runs"
            ).fetchone()[0]
            snapshot_count = conn.execute(
                "SELECT COUNT(*) FROM portal_analysis_site_snapshots"
            ).fetchone()[0]
            activity_count = conn.execute(
                "SELECT COUNT(*) FROM activity_log"
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(run_count, 0)
        self.assertEqual(snapshot_count, 0)
        self.assertEqual(activity_count, 0)


if __name__ == "__main__":
    unittest.main()
