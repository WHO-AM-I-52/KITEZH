import sqlite3
import unittest
from types import SimpleNamespace

from services.investmap_rf_manager_assignment import (
    MATCH_STATUS_AMBIGUOUS,
    MATCH_STATUS_MANUAL,
    MATCH_STATUS_MATCHED,
    MATCH_STATUS_UNMATCHED,
    normalize_municipality,
    update_card_manager_assignment,
)


class InvestmapRfManagerAssignmentTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._create_schema()
        self._create_admin()

    def tearDown(self):
        self.conn.close()

    def _create_schema(self):
        self.conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                role TEXT NOT NULL,
                is_active INTEGER NOT NULL
            );

            CREATE TABLE notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                link TEXT,
                is_read INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE investmap_rf_municipality_manager_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                municipality_name TEXT NOT NULL,
                municipality_normalized TEXT NOT NULL UNIQUE,
                manager_name TEXT NOT NULL,
                match_mode TEXT NOT NULL DEFAULT 'contains',
                is_active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE investmap_rf_card_manager_assignments (
                global_id INTEGER PRIMARY KEY,
                municipality_raw TEXT,
                municipality_normalized TEXT,
                manager_name TEXT,
                rule_id INTEGER,
                assignment_source TEXT NOT NULL,
                match_status TEXT NOT NULL,
                assigned_by_user_id INTEGER,
                updated_at_utc TEXT NOT NULL
            );

            CREATE TABLE investmap_rf_manager_match_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                global_id INTEGER NOT NULL,
                municipality_raw TEXT,
                municipality_normalized TEXT,
                issue_type TEXT NOT NULL,
                details TEXT,
                is_resolved INTEGER NOT NULL DEFAULT 0,
                resolved_by_user_id INTEGER,
                resolved_at_utc TEXT,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            );

            CREATE UNIQUE INDEX
            idx_investmap_rf_manager_match_issues_open_unique
            ON investmap_rf_manager_match_issues (
                global_id,
                municipality_normalized,
                issue_type
            )
            WHERE is_resolved = 0;
            """
        )

    def _create_admin(self):
        self.conn.execute(
            """
            INSERT INTO users (id, role, is_active)
            VALUES (1, 'admin', 1)
            """
        )

    def _add_rule(
        self,
        municipality_name,
        manager_name,
        *,
        match_mode="contains",
        is_active=1,
    ):
        return self.conn.execute(
            """
            INSERT INTO investmap_rf_municipality_manager_rules (
                municipality_name,
                municipality_normalized,
                manager_name,
                match_mode,
                is_active
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                municipality_name,
                normalize_municipality(municipality_name),
                manager_name,
                match_mode,
                is_active,
            ),
        ).lastrowid

    def _card(self, global_id, municipality, **payload_fields):
        payload = {
            "municipality": municipality,
            **payload_fields,
        }
        return SimpleNamespace(
            global_id=global_id,
            payload=payload,
        )

    def _assignment(self, global_id):
        return self.conn.execute(
            """
            SELECT *
            FROM investmap_rf_card_manager_assignments
            WHERE global_id = ?
            """,
            (global_id,),
        ).fetchone()

    def _open_issues(self, global_id):
        return self.conn.execute(
            """
            SELECT *
            FROM investmap_rf_manager_match_issues
            WHERE global_id = ?
              AND is_resolved = 0
            ORDER BY id
            """,
            (global_id,),
        ).fetchall()

    def test_single_rule_creates_automatic_assignment(self):
        rule_id = self._add_rule("город Нижний Новгород", "Иван Иванов")
        card = self._card(1001, "  ГОРОД   Нижний НОВГОРОД  ")

        result = update_card_manager_assignment(self.conn, card=card)

        self.assertEqual(result["status"], MATCH_STATUS_MATCHED)
        self.assertFalse(result["notification_created"])
        self.assertIsNone(result["issue"])

        assignment = self._assignment(1001)
        self.assertEqual(assignment["manager_name"], "Иван Иванов")
        self.assertEqual(assignment["rule_id"], rule_id)
        self.assertEqual(assignment["assignment_source"], "auto")
        self.assertEqual(assignment["match_status"], MATCH_STATUS_MATCHED)
        self.assertEqual(
            assignment["municipality_normalized"],
            "город нижний новгород",
        )
        self.assertEqual(self._open_issues(1001), [])

    def test_no_rule_creates_unmatched_issue_and_notification(self):
        card = self._card(1002, "Неизвестный муниципалитет")

        result = update_card_manager_assignment(self.conn, card=card)

        self.assertEqual(result["status"], MATCH_STATUS_UNMATCHED)
        self.assertTrue(result["notification_created"])
        self.assertEqual(result["issue"]["issue_type"], "unmatched")

        assignment = self._assignment(1002)
        self.assertIsNone(assignment["manager_name"])
        self.assertEqual(assignment["assignment_source"], "auto")
        self.assertEqual(assignment["match_status"], MATCH_STATUS_UNMATCHED)

        issues = self._open_issues(1002)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["issue_type"], "unmatched")

        notification_count = self.conn.execute(
            "SELECT COUNT(*) FROM notifications"
        ).fetchone()[0]
        activity_count = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM activity_log
            WHERE action = 'investmap_rf_manager_match_unmatched'
            """
        ).fetchone()[0]

        self.assertEqual(notification_count, 1)
        self.assertEqual(activity_count, 1)

    def test_multiple_rules_create_ambiguous_issue(self):
        self._add_rule("Нижний Новгород", "Иван Иванов")
        self._add_rule("Новгород", "Пётр Петров")
        card = self._card(1003, "город Нижний Новгород")

        result = update_card_manager_assignment(self.conn, card=card)

        self.assertEqual(result["status"], MATCH_STATUS_AMBIGUOUS)
        self.assertTrue(result["notification_created"])
        self.assertEqual(result["issue"]["issue_type"], "ambiguous")

        assignment = self._assignment(1003)
        self.assertIsNone(assignment["manager_name"])
        self.assertEqual(assignment["match_status"], MATCH_STATUS_AMBIGUOUS)

        issues = self._open_issues(1003)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["issue_type"], "ambiguous")
        self.assertIn("Иван Иванов", issues[0]["details"])
        self.assertIn("Пётр Петров", issues[0]["details"])

    def test_manual_assignment_is_not_overwritten(self):
        rule_id = self._add_rule("город Арзамас", "Автоматический управляющий")
        self.conn.execute(
            """
            INSERT INTO investmap_rf_card_manager_assignments (
                global_id,
                municipality_raw,
                municipality_normalized,
                manager_name,
                rule_id,
                assignment_source,
                match_status,
                assigned_by_user_id,
                updated_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1004,
                "город Арзамас",
                "город арзамас",
                "Ручной управляющий",
                rule_id,
                "manual",
                MATCH_STATUS_MANUAL,
                1,
                "2026-08-25T00:00:00+00:00",
            ),
        )
        card = self._card(1004, "город Арзамас")

        result = update_card_manager_assignment(self.conn, card=card)

        self.assertEqual(result["status"], MATCH_STATUS_MANUAL)
        self.assertFalse(result["notification_created"])
        self.assertIsNone(result["issue"])

        assignment = self._assignment(1004)
        self.assertEqual(assignment["manager_name"], "Ручной управляющий")
        self.assertEqual(assignment["assignment_source"], "manual")
        self.assertEqual(assignment["match_status"], MATCH_STATUS_MANUAL)

    def test_repeated_unmatched_card_does_not_duplicate_open_issue(self):
        card = self._card(1005, "Неизвестный муниципалитет")

        first_result = update_card_manager_assignment(self.conn, card=card)
        second_result = update_card_manager_assignment(self.conn, card=card)

        self.assertTrue(first_result["notification_created"])
        self.assertFalse(second_result["notification_created"])
        self.assertEqual(len(self._open_issues(1005)), 1)

        notification_count = self.conn.execute(
            "SELECT COUNT(*) FROM notifications"
        ).fetchone()[0]
        activity_count = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM activity_log
            WHERE action = 'investmap_rf_manager_match_unmatched'
            """
        ).fetchone()[0]

        self.assertEqual(notification_count, 1)
        self.assertEqual(activity_count, 1)

    def test_kulibin_card_overrides_municipality_rule(self):
        self._add_rule("город Дзержинск", "Алюков Алексей")
        card = self._card(
            1006,
            "город Дзержинск",
            preferentialBusinessLink={
                "name": 'ОЭЗ ППТ "Кулибин"',
            },
        )

        result = update_card_manager_assignment(self.conn, card=card)

        self.assertEqual(result["status"], MATCH_STATUS_MATCHED)
        self.assertFalse(result["notification_created"])
        self.assertIsNone(result["issue"])

        assignment = self._assignment(1006)
        self.assertEqual(
            assignment["manager_name"],
            "Земсков Александр Николаевич",
        )
        self.assertIsNone(assignment["rule_id"])
        self.assertEqual(assignment["assignment_source"], "auto")
        self.assertEqual(assignment["match_status"], MATCH_STATUS_MATCHED)
        self.assertEqual(self._open_issues(1006), [])

    def test_manual_assignment_overrides_kulibin_rule(self):
        self.conn.execute(
            """
            INSERT INTO investmap_rf_card_manager_assignments (
                global_id,
                municipality_raw,
                municipality_normalized,
                manager_name,
                rule_id,
                assignment_source,
                match_status,
                assigned_by_user_id,
                updated_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1007,
                "город Дзержинск",
                "город дзержинск",
                "Ручной управляющий",
                None,
                "manual",
                MATCH_STATUS_MANUAL,
                1,
                "2026-08-25T00:00:00+00:00",
            ),
        )
        card = self._card(
            1007,
            "город Дзержинск",
            businessEnvironmentPreferentialLink={
                "name": 'ОЭЗ ППТ "Кулибин"',
            },
        )

        result = update_card_manager_assignment(self.conn, card=card)

        self.assertEqual(result["status"], MATCH_STATUS_MANUAL)
        self.assertFalse(result["notification_created"])
        self.assertIsNone(result["issue"])

        assignment = self._assignment(1007)
        self.assertEqual(assignment["manager_name"], "Ручной управляющий")
        self.assertEqual(assignment["assignment_source"], "manual")
        self.assertEqual(assignment["match_status"], MATCH_STATUS_MANUAL)

if __name__ == "__main__":
    unittest.main()
