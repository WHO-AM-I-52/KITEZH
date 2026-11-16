# -*- coding: utf-8 -*-
"""Тесты workflow раздела «Договоры»: переходы, задачи, безвозмездный путь.

Работают на временной SQLite-БД через реальную последовательность
миграций приложения, без production-БД и production-приложения.
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db as db_module
import migrations as migrations_module

from contracts import pricing
from contracts.workdays import add_days
from contracts import workflow
from contracts.workflow import (
    apply_transition, stage_requirements, transition_allowed,
)


class WorkflowTestBase(unittest.TestCase):
    """Временная БД + проверка переходов напрямую через conn."""

    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db_path = os.path.join(self._tmpdir.name, 'test.db')

        db_module.DB_PATH = self.db_path
        migrations_module.DB_PATH = self.db_path
        db_module._migrated = False
        migrations_module.init_db()
        migrations_module.migrate_db()
        migrations_module.migrate_districts()
        db_module.run_migrations()

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")

        self._seed_users()
        self._seed_contract()

    def tearDown(self):
        self.conn.close()
        super().tearDown()

    def _seed_users(self):
        # init_db создаёт admin (id=1); добавляем исполнителя договоров.
        self.conn.execute(
            "INSERT INTO users (username, password, full_name, role)"
            " VALUES ('exec1', 'x', 'Исполнитель Один', 'contract_executor')"
        )
        self.conn.commit()
        self.executor_id = self.conn.execute(
            "SELECT id FROM users WHERE username='exec1'"
        ).fetchone()[0]

    def _seed_contract(self, **kw):
        base = dict(
            inner_number='ДГ-2026-0001',
            is_free=0,
            customer_type='legal',
            customer_full_name='Общество с ограниченной ответственностью «Тест»',
            customer_short_name='ООО «Тест»',
            customer_opf='ООО',
            customer_inn='1234567890',
            customer_ogrn='0987654321',
            contact_name='Иванов Иван Иванович',
            contact_phone='+7 900 000-00-00',
            project_name='Тестовый проект',
            executor_id=None,
            stage='draft',
            state='active',
            payment_scheme='prepay_100',
            payment_days=10,
            payment_day_type='work',
            work_start_basis='payment',
            duration_days=10,
            duration_day_type='work',
            act_sign_method='edo_upp',
            total_kop=16000000,
            vat_percent='22',
            created_by=None,
        )
        base['executor_id'] = self.executor_id
        base['created_by'] = self.executor_id
        base.update(kw)
        cols = ', '.join(base)
        qs = ', '.join('?' for _ in base)
        cur = self.conn.execute(
            f"INSERT INTO contracts ({cols}) VALUES ({qs})",
            list(base.values())
        )
        self.contract_id = cur.lastrowid
        self.conn.commit()
        self.contract = self._get()

    def _get(self):
        return self.conn.execute(
            "SELECT * FROM contracts WHERE id=?", (self.contract_id,)
        ).fetchone()

    def _add_file(self, category):
        self.conn.execute(
            """INSERT INTO contract_files
               (contract_id, category, version, is_current, orig_name,
                stored_name, user_id)
               VALUES (?, ?, 1, 1, 'x.docx', 'x.docx', 1)""",
            (self.contract_id, category)
        )
        self.conn.commit()


class TransitionTests(WorkflowTestBase):

    def test_draft_to_customer_review_ok(self):
        ok, err = apply_transition(
            self.conn, self.contract, 'customer_review', 1, '2026-11-16'
        )
        self.assertTrue(ok, err)
        self.assertEqual(self._get()['stage'], 'customer_review')
        # создана задача этапа
        cnt = self.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE title LIKE '%обратную связь%'"
        ).fetchone()[0]
        self.assertEqual(cnt, 1)

    def test_draft_to_customer_review_missing_customer(self):
        self.conn.execute(
            "UPDATE contracts SET customer_full_name=NULL WHERE id=?",
            (self.contract_id,)
        )
        self.conn.commit()
        contract = self._get()
        missing = stage_requirements(
            self.conn, contract, 'customer_review'
        )
        self.assertIn('Не указан заказчик', missing)
        ok, err = apply_transition(
            self.conn, contract, 'customer_review', 1, '2026-11-16'
        )
        self.assertFalse(ok)

    def test_full_route(self):
        """Проход по всем стадиям с нужными файлами и полями."""
        route = [
            ('customer_review', 'project'),
            ('directum_review', None),
            ('customer_signing', None),
            ('payment', 'signed_contract'),
            ('in_work', None),
            ('act_signing', 'result'),
            ('completed', 'act'),
        ]
        # номер договора появляется после Directum
        self._add_file('project')
        for target, file_cat in route:
            if file_cat:
                self._add_file(file_cat)
            if target == 'customer_signing':
                self.conn.execute(
                    "UPDATE contracts SET reg_number='81/26-ДГ' WHERE id=?",
                    (self.contract_id,)
                )
                self.conn.commit()
            ok, err = apply_transition(
                self.conn, self._get(), target, 1, '2026-11-16'
            )
            self.assertTrue(ok, f"{target}: {err}")
            self.assertEqual(self._get()['stage'], target)
        self.assertEqual(self._get()['state'], 'completed')

    def test_backward_transition_allowed(self):
        for target in ('customer_review', 'directum_review'):
            self._add_file('project')
            ok, err = apply_transition(
                self.conn, self._get(), target, 1, '2026-11-16'
            )
            self.assertTrue(ok, err)
        ok, err = apply_transition(
            self.conn, self._get(), 'customer_review', 1, '2026-11-16',
            comment='Заказчик прислал замечания'
        )
        self.assertTrue(ok, err)

    def test_skip_stage_not_allowed(self):
        ok, err = apply_transition(
            self.conn, self.contract, 'directum_review', 1, '2026-11-16'
        )
        self.assertFalse(ok)

    def test_free_contract_skips_contract_required(self):
        self.conn.execute(
            "UPDATE contracts SET is_free=1 WHERE id=?", (self.contract_id,)
        )
        self.conn.commit()
        contract = self._get()
        # для безвозмездного: signed_contract не требуется
        missing = stage_requirements(self.conn, contract, 'payment')
        self.assertEqual(missing, [])

    def test_duplicate_click_no_second_task(self):
        self._add_file('project')
        for _ in range(2):
            ok, err = apply_transition(
                self.conn, self._get(), 'customer_review', 1, '2026-11-16'
            )
            self.assertTrue(ok, err)
        cnt = self.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE title LIKE '%обратную связь%'"
        ).fetchone()[0]
        self.assertEqual(cnt, 1)


class DeadlineTests(WorkflowTestBase):
    """Проверка контрольных сроков по согласованным правилам."""

    def test_customer_review_5_workdays(self):
        # Пн 16.11.2026 + 5 раб. дней = Пн 23.11.2026
        deadline = add_days(date(2026, 11, 16), 5, 'work')
        self.assertEqual(str(deadline), '2026-11-23')

    def test_directum_14_calendar_days(self):
        deadline = add_days(date(2026, 11, 16), 14, 'cal')
        self.assertEqual(str(deadline), '2026-11-30')

    def test_act_signing_7_workdays(self):
        # Подписание актов: всегда 7 рабочих дней (ЭДО/УПД, бумага, СБИС)
        deadline = add_days(date(2026, 11, 16), 7, 'work')
        self.assertEqual(str(deadline), '2026-11-25')


class PricingIntegrationTests(WorkflowTestBase):
    """Расчёт итоговой цены на реальных строках услуг."""

    def test_full_package_with_submission(self):
        lines = [
            {'code': '2.1', 'amount_kop': pricing._to_kop(30000)},
            {'code': '2.2', 'amount_kop': pricing._to_kop(40000)},
            {'code': '2.3', 'amount_kop': pricing._to_kop(15000)},
            {'code': '2.4', 'amount_kop': pricing._to_kop(60000)},
            {'code': '2.5', 'amount_kop': pricing._to_kop(15000)},
            {'code': '3.1', 'amount_kop': pricing._to_kop(5000)},
        ]
        total = pricing.council_services_total(lines)
        self.assertEqual(total, 16000000)
        self.assertEqual(
            pricing.vat_from_total(total, 22), 2885246
        )
        self.assertIn(
            'сто шестьдесят тысяч', pricing.rub_in_words(total)
        )


if __name__ == '__main__':
    unittest.main()
