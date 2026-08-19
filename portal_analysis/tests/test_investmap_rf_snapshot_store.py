import json
import sqlite3
import unittest

from services.investmap_rf_client import InvestmapRfCard
from services.investmap_rf_snapshot_store import save_card_snapshot


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


def _card(payload):
    return InvestmapRfCard(
        global_id=payload["id"],
        filling_level=payload.get("fillingLevel"),
        region_code=payload.get("regionCode"),
        regions=payload.get("regions", []),
        payload=payload,
    )


class InvestmapRfSnapshotStoreTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    def tearDown(self):
        self.conn.close()

    def test_first_snapshot_is_saved_without_changes(self):
        card = _card(
            {
                "id": 2461092,
                "fillingLevel": 91,
                "regionCode": 52,
                "regions": ["Нижегородская область"],
            }
        )

        result = save_card_snapshot(
            self.conn,
            card,
            fetched_at_utc="2026-08-19T10:00:00+00:00",
        )

        self.assertTrue(result.is_new_snapshot)
        self.assertEqual(result.changes_count, 0)

        snapshot = self.conn.execute(
            """
            SELECT global_id, payload_json, fetched_at_utc, filling_level, region_code
            FROM investmap_rf_card_snapshots
            """
        ).fetchone()
        self.assertEqual(snapshot["global_id"], 2461092)
        self.assertEqual(
            json.loads(snapshot["payload_json"]),
            card.payload,
        )
        self.assertEqual(
            snapshot["fetched_at_utc"],
            "2026-08-19T10:00:00+00:00",
        )
        self.assertEqual(snapshot["filling_level"], 91)
        self.assertEqual(snapshot["region_code"], 52)

        changes_count = self.conn.execute(
            "SELECT COUNT(*) FROM investmap_rf_card_changes"
        ).fetchone()[0]
        self.assertEqual(changes_count, 0)

    def test_same_payload_does_not_create_second_snapshot(self):
        card = _card(
            {
                "id": 2461092,
                "regionCode": 52,
                "title": "Площадка",
            }
        )

        first = save_card_snapshot(self.conn, card)
        second = save_card_snapshot(self.conn, card)

        self.assertTrue(first.is_new_snapshot)
        self.assertFalse(second.is_new_snapshot)
        self.assertEqual(second.snapshot_id, first.snapshot_id)
        self.assertEqual(second.changes_count, 0)

        snapshots_count = self.conn.execute(
            "SELECT COUNT(*) FROM investmap_rf_card_snapshots"
        ).fetchone()[0]
        self.assertEqual(snapshots_count, 1)

    def test_key_order_does_not_create_false_change(self):
        first = _card(
            {
                "id": 2461092,
                "title": "Площадка",
                "regionCode": 52,
            }
        )
        same_data_other_key_order = _card(
            {
                "regionCode": 52,
                "id": 2461092,
                "title": "Площадка",
            }
        )

        saved = save_card_snapshot(self.conn, first)
        repeated = save_card_snapshot(self.conn, same_data_other_key_order)

        self.assertTrue(saved.is_new_snapshot)
        self.assertFalse(repeated.is_new_snapshot)
        self.assertEqual(repeated.snapshot_id, saved.snapshot_id)

    def test_nested_value_change_is_recorded(self):
        first = _card(
            {
                "id": 2461092,
                "contacts": {
                    "responsible": {
                        "phone": "+7 000 000-00-00",
                    }
                },
            }
        )
        changed = _card(
            {
                "id": 2461092,
                "contacts": {
                    "responsible": {
                        "phone": "+7 111 111-11-11",
                    }
                },
            }
        )

        previous = save_card_snapshot(self.conn, first)
        current = save_card_snapshot(self.conn, changed)

        self.assertTrue(current.is_new_snapshot)
        self.assertEqual(current.changes_count, 1)

        change = self.conn.execute(
            """
            SELECT previous_snapshot_id, current_snapshot_id, field_path,
                   old_value_json, new_value_json
            FROM investmap_rf_card_changes
            """
        ).fetchone()
        self.assertEqual(change["previous_snapshot_id"], previous.snapshot_id)
        self.assertEqual(change["current_snapshot_id"], current.snapshot_id)
        self.assertEqual(change["field_path"], "/contacts/responsible/phone")
        self.assertEqual(json.loads(change["old_value_json"]), "+7 000 000-00-00")
        self.assertEqual(json.loads(change["new_value_json"]), "+7 111 111-11-11")

    def test_list_change_is_recorded_by_index(self):
        first = _card(
            {
                "id": 2461092,
                "contacts": [{"name": "Первый"}, {"name": "Второй"}],
            }
        )
        changed = _card(
            {
                "id": 2461092,
                "contacts": [{"name": "Первый"}, {"name": "Обновлённый"}],
            }
        )

        save_card_snapshot(self.conn, first)
        result = save_card_snapshot(self.conn, changed)

        self.assertEqual(result.changes_count, 1)

        change = self.conn.execute(
            """
            SELECT field_path, old_value_json, new_value_json
            FROM investmap_rf_card_changes
            """
        ).fetchone()
        self.assertEqual(change["field_path"], "/contacts/1/name")
        self.assertEqual(json.loads(change["old_value_json"]), "Второй")
        self.assertEqual(json.loads(change["new_value_json"]), "Обновлённый")

    def test_json_pointer_special_characters_are_escaped(self):
        first = _card(
            {
                "id": 2461092,
                "contact/name~type": "старое",
            }
        )
        changed = _card(
            {
                "id": 2461092,
                "contact/name~type": "новое",
            }
        )

        save_card_snapshot(self.conn, first)
        save_card_snapshot(self.conn, changed)

        field_path = self.conn.execute(
            "SELECT field_path FROM investmap_rf_card_changes"
        ).fetchone()[0]
        self.assertEqual(field_path, "/contact~1name~0type")

    def test_added_and_removed_values_are_recorded(self):
        first = _card(
            {
                "id": 2461092,
                "title": "Площадка",
                "obsolete": True,
            }
        )
        changed = _card(
            {
                "id": 2461092,
                "title": "Площадка",
                "newField": {"value": 1},
            }
        )

        save_card_snapshot(self.conn, first)
        result = save_card_snapshot(self.conn, changed)

        self.assertEqual(result.changes_count, 2)

        rows = self.conn.execute(
            """
            SELECT field_path, old_value_json, new_value_json
            FROM investmap_rf_card_changes
            ORDER BY field_path
            """
        ).fetchall()
        self.assertEqual(rows[0]["field_path"], "/newField")
        self.assertIsNone(json.loads(rows[0]["old_value_json"]))
        self.assertEqual(json.loads(rows[0]["new_value_json"]), {"value": 1})
        self.assertEqual(rows[1]["field_path"], "/obsolete")
        self.assertTrue(json.loads(rows[1]["old_value_json"]))
        self.assertIsNone(json.loads(rows[1]["new_value_json"]))


if __name__ == "__main__":
    unittest.main()
