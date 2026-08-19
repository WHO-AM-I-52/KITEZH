import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.investmap_rf_batch_runner import (
    BatchReport,
    BatchItemResult,
    SnapshotSaveResult,
    main,
    read_global_ids,
    run_batch,
    write_report,
)
from services.investmap_rf_client import InvestmapRfClientError


class InvestmapRfBatchRunnerTest(unittest.TestCase):
    def test_read_global_ids_skips_comments_blank_lines_and_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "ids.txt"
            path.write_text(
                """
                # тестовая выборка
                2461092

                2511878  # комментарий
                2461092
                """,
                encoding="utf-8",
            )

            self.assertEqual(read_global_ids(path), [2461092, 2511878])

    def test_read_global_ids_rejects_invalid_value_with_line_number(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "ids.txt"
            path.write_text("2461092\nnot-an-id\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "Строка 2"):
                read_global_ids(path)

    def test_read_global_ids_rejects_non_positive_value(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "ids.txt"
            path.write_text("0\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "положительным"):
                read_global_ids(path)

    def test_run_batch_tracks_new_unchanged_and_failed_items(self):
        def collect(global_id):
            if global_id == 2:
                raise InvestmapRfClientError("лимит запросов")
            if global_id == 3:
                return SnapshotSaveResult(
                    snapshot_id=13,
                    is_new_snapshot=False,
                    changes_count=0,
                )
            return SnapshotSaveResult(
                snapshot_id=11,
                is_new_snapshot=True,
                changes_count=2,
            )

        sleep_calls = []
        report = run_batch(
            [1, 2, 3],
            delay_seconds=1.0,
            collect_snapshot_fn=collect,
            sleep_fn=sleep_calls.append,
        )

        self.assertEqual(report.requested_count, 3)
        self.assertEqual(report.processed_count, 3)
        self.assertEqual(report.new_snapshots_count, 1)
        self.assertEqual(report.unchanged_count, 1)
        self.assertEqual(report.errors_count, 1)
        self.assertFalse(report.interrupted)
        self.assertEqual(sleep_calls, [1.0, 1.0])

        self.assertEqual(report.items[0].status, "new")
        self.assertEqual(report.items[0].snapshot_id, 11)
        self.assertEqual(report.items[0].changes_count, 2)
        self.assertEqual(report.items[1].status, "error")
        self.assertEqual(report.items[1].error, "лимит запросов")
        self.assertEqual(report.items[2].status, "unchanged")
        self.assertEqual(report.items[2].snapshot_id, 13)

    def test_run_batch_marks_interruption_during_pause(self):
        sleep_calls = []

        def interrupting_sleep(seconds):
            sleep_calls.append(seconds)
            raise KeyboardInterrupt

        report = run_batch(
            [1, 2, 3],
            delay_seconds=1.0,
            collect_snapshot_fn=lambda global_id: SnapshotSaveResult(
                snapshot_id=global_id,
                is_new_snapshot=True,
                changes_count=0,
            ),
            sleep_fn=interrupting_sleep,
        )

        self.assertTrue(report.interrupted)
        self.assertEqual(report.requested_count, 3)
        self.assertEqual(report.processed_count, 1)
        self.assertEqual(report.new_snapshots_count, 1)
        self.assertEqual(sleep_calls, [1.0])

    def test_run_batch_marks_interruption_during_collection(self):
        processed_ids = []

        def interrupting_collect(global_id):
            processed_ids.append(global_id)
            raise KeyboardInterrupt

        report = run_batch(
            [1, 2, 3],
            delay_seconds=1.0,
            collect_snapshot_fn=interrupting_collect,
            sleep_fn=lambda seconds: self.fail("Пауза не должна запускаться."),
        )

        self.assertTrue(report.interrupted)
        self.assertEqual(report.requested_count, 3)
        self.assertEqual(report.processed_count, 0)
        self.assertEqual(report.new_snapshots_count, 0)
        self.assertEqual(report.unchanged_count, 0)
        self.assertEqual(report.errors_count, 0)
        self.assertEqual(processed_ids, [1])

    def test_run_batch_rejects_delay_below_minimum(self):
        with self.assertRaisesRegex(ValueError, "не может быть меньше"):
            run_batch(
                [1],
                delay_seconds=0.5,
                collect_snapshot_fn=lambda global_id: SnapshotSaveResult(
                    snapshot_id=global_id,
                    is_new_snapshot=True,
                    changes_count=0,
                ),
            )

    def test_write_report_writes_json_without_payload(self):
        report = BatchReport(
            started_at_utc="2026-08-19T10:00:00+00:00",
            completed_at_utc="2026-08-19T10:01:00+00:00",
            requested_count=1,
            processed_count=1,
            new_snapshots_count=1,
            unchanged_count=0,
            errors_count=0,
            interrupted=False,
            items=[
                BatchItemResult(
                    global_id=2461092,
                    status="new",
                    snapshot_id=1,
                    changes_count=0,
                    error=None,
               )
            ],
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "nested" / "report.json"
            write_report(path, report)

            payload = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload["requested_count"], 1)
        self.assertEqual(payload["items"][0]["global_id"], 2461092)
        self.assertNotIn("payload_json", json.dumps(payload))

    def test_main_returns_two_for_invalid_limit(self):
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            exit_code = main(["ids.txt", "--limit", "0"])

        self.assertEqual(exit_code, 2)
        self.assertIn("--limit должен быть положительным", stderr.getvalue())

    def test_main_returns_two_for_empty_ids_file(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            ids_path = Path(temporary_directory) / "ids.txt"
            ids_path.write_text("# пока пусто\n", encoding="utf-8")
            stderr = io.StringIO()

            with contextlib.redirect_stderr(stderr):
                exit_code = main([str(ids_path)])

        self.assertEqual(exit_code, 2)
        self.assertIn("Файл не содержит ID", stderr.getvalue())

    def test_main_writes_report_and_returns_zero(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            ids_path = Path(temporary_directory) / "ids.txt"
            report_path = Path(temporary_directory) / "report.json"
            ids_path.write_text("2461092\n", encoding="utf-8")

            report = BatchReport(
                started_at_utc="2026-08-19T10:00:00+00:00",
                completed_at_utc="2026-08-19T10:00:01+00:00",
                requested_count=1,
                processed_count=1,
                new_snapshots_count=1,
                unchanged_count=0,
                errors_count=0,
                interrupted=False,
                items=[],
            )
            stdout = io.StringIO()

            with patch(
                "services.investmap_rf_batch_runner.run_batch",
                return_value=report,
            ) as run_batch_mock:
                with contextlib.redirect_stdout(stdout):
                    exit_code = main(
                        [
                            str(ids_path),
                            "--report",
                            str(report_path),
                            "--limit",
                            "1",
                        ]
                    )

            self.assertEqual(exit_code, 0)
            run_batch_mock.assert_called_once_with(
                [2461092],
                delay_seconds=1.0,
            )
            self.assertTrue(report_path.exists())
            self.assertIn("новых=1", stdout.getvalue())

    def test_main_returns_one_when_batch_has_errors(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            ids_path = Path(temporary_directory) / "ids.txt"
            report_path = Path(temporary_directory) / "report.json"
            ids_path.write_text("2461092\n", encoding="utf-8")

            report = BatchReport(
                started_at_utc="2026-08-19T10:00:00+00:00",
                completed_at_utc="2026-08-19T10:00:01+00:00",
                requested_count=1,
                processed_count=1,
                new_snapshots_count=0,
                unchanged_count=0,
                errors_count=1,
                interrupted=False,
                items=[],
            )

            with patch(
                "services.investmap_rf_batch_runner.run_batch",
                return_value=report,
            ):
                exit_code = main(
                    [
                        str(ids_path),
                        "--report",
                        str(report_path),
                    ]
                )

        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()
