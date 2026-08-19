import contextlib
import io
import sqlite3
import unittest
from unittest.mock import patch

from services.investmap_rf_client import (
    InvestmapRfCard,
    InvestmapRfClientError,
)
from services.investmap_rf_snapshot_runner import (
    SnapshotSaveResult,
    collect_card_snapshot,
    main,
)


_SCHEMA = """
CREATE TABLE investmap_rf_card_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    global_id INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    fetched_at_utc TEXT NOT NULL,
    filling_level INTEGER,
    region_code INTEGER,
    UNIQUE(global_id, payload_sha256)
);

CREATE TABLE investmap_rf_card_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    global_id INTEGER NOT NULL,
    previous_snapshot_id INTEGER NOT NULL,
    current_snapshot_id INTEGER NOT NULL,
    field_path TEXT NOT NULL,
    old_value_json TEXT,
    new_value_json TEXT,
    detected_at_utc TEXT NOT NULL
);
"""


def _card(global_id=2461092, title="Площадка"):
    payload = {
        "id": global_id,
        "title": title,
        "regionCode": 52,
    }
    return InvestmapRfCard(
        global_id=global_id,
        filling_level=None,
        region_code=52,
        regions=[],
        payload=payload,
    )


class _TrackingConnection:
    """Обёртка, позволяющая проверить commit/rollback/close."""

    def __init__(self, conn):
        self._conn = conn
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)

    def commit(self):
        self.commits += 1
        self._conn.commit()

    def rollback(self):
        self.rollbacks += 1
        self._conn.rollback()

    def close(self):
        self.closed = True
        self._conn.close()


class InvestmapRfSnapshotRunnerTest(unittest.TestCase):
    def _connection(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        return _TrackingConnection(conn)

    def test_collect_saves_new_snapshot_commits_and_closes(self):
        conn = self._connection()

        result = collect_card_snapshot(
            2461092,
            fetch_card_fn=lambda global_id: _card(global_id),
            get_db_fn=lambda: conn,
        )

        self.assertTrue(result.is_new_snapshot)
        self.assertEqual(result.changes_count, 0)
        self.assertEqual(conn.commits, 1)
        self.assertEqual(conn.rollbacks, 0)
        self.assertTrue(conn.closed)

    def test_collect_rolls_back_and_closes_when_api_fails(self):
        conn = self._connection()

        def fail_fetch(global_id):
            raise InvestmapRfClientError("API недоступен")

        with self.assertRaisesRegex(InvestmapRfClientError, "API недоступен"):
            collect_card_snapshot(
                2461092,
                fetch_card_fn=fail_fetch,
                get_db_fn=lambda: conn,
            )

        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)
        self.assertTrue(conn.closed)

    def test_collect_rolls_back_and_closes_when_sqlite_fails(self):
        conn = self._connection()
        conn._conn.execute("DROP TABLE investmap_rf_card_snapshots")

        with self.assertRaises(sqlite3.Error):
            collect_card_snapshot(
                2461092,
                fetch_card_fn=lambda global_id: _card(global_id),
                get_db_fn=lambda: conn,
            )

        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)
        self.assertTrue(conn.closed)

    def test_main_returns_zero_and_prints_result(self):
        result = SnapshotSaveResult(
            snapshot_id=7,
            is_new_snapshot=True,
            changes_count=2,
        )
        stdout = io.StringIO()

        with patch(
            "services.investmap_rf_snapshot_runner.collect_card_snapshot",
            return_value=result,
        ) as collect:
            with contextlib.redirect_stdout(stdout):
                exit_code = main(["2461092"])

        self.assertEqual(exit_code, 0)
        collect.assert_called_once_with(2461092)
        self.assertIn("Карточка 2461092: создан новый снимок", stdout.getvalue())
        self.assertIn("snapshot_id=7", stdout.getvalue())
        self.assertIn("changes=2", stdout.getvalue())

    def test_main_returns_two_for_non_positive_id(self):
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            exit_code = main(["0"])

        self.assertEqual(exit_code, 2)
        self.assertIn("global_id должен быть положительным", stderr.getvalue())

    def test_main_returns_one_for_api_error(self):
        stderr = io.StringIO()

        with patch(
            "services.investmap_rf_snapshot_runner.collect_card_snapshot",
            side_effect=InvestmapRfClientError("доступ запрещён"),
        ):
            with contextlib.redirect_stderr(stderr):
                exit_code = main(["2461092"])

        self.assertEqual(exit_code, 1)
        self.assertIn("Не удалось получить карточку", stderr.getvalue())

    def test_main_returns_one_for_sqlite_error(self):
        stderr = io.StringIO()

        with patch(
            "services.investmap_rf_snapshot_runner.collect_card_snapshot",
            side_effect=sqlite3.OperationalError("таблица недоступна"),
        ):
            with contextlib.redirect_stderr(stderr):
                exit_code = main(["2461092"])

        self.assertEqual(exit_code, 1)
        self.assertIn("Не удалось сохранить снимок в SQLite", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
