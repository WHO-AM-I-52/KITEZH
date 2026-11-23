# -*- coding: utf-8 -*-
"""Тесты документов раздела «Договоры»: версии файлов, генерация DOCX.

Генерация проверяется на реальном шаблоне и созданном файле:
подставленные значения, отсутствие незаменённых меток, реквизиты.
"""
import os
import sys
import sqlite3
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import docx

import db as db_module
import migrations as migrations_module

from contracts import documents, pricing


class DocumentsTestBase(unittest.TestCase):

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

        self.conn.execute(
            "INSERT INTO users (username, password, full_name, role)"
            " VALUES ('e', 'x', 'Исполнитель', 'contract_executor')"
        )
        self.uid = self.conn.execute(
            "SELECT id FROM users WHERE username='e'"
        ).fetchone()[0]
        self.contract_id = self.conn.execute(
            """INSERT INTO contracts
               (inner_number, is_free, customer_type, customer_full_name,
                customer_short_name, customer_inn, customer_kpp,
                customer_ogrn, customer_address, signatory_name,
                signatory_position, signatory_basis, contact_email,
                contact_name, project_name, executor_id, payment_scheme,
                payment_days, payment_day_type, duration_days,
                duration_day_type, total_kop, vat_percent, created_by)
               VALUES ('ДГ-2026-0001', 0, 'legal',
                'Общество с ограниченной ответственностью «Ромашка»',
                'ООО «Ромашка»', '5250000000', '525001001',
                '1025200000000', 'г. Нижний Новгород, ул. Тестовая, 1',
                'Петров Петр Петрович', 'генерального директора', 'Устава',
                'petrov@example.com', 'Петров Петр Петрович',
                'Тестовый проект', ?, 'prepay_100', 10, 'work', 10,
                'work', 16000000, '22', ?)""",
            (self.uid, self.uid)
        ).lastrowid
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        super().tearDown()

    def _get(self):
        return self.conn.execute(
            "SELECT * FROM contracts WHERE id=?", (self.contract_id,)
        ).fetchone()


class DocxGenerationTests(DocumentsTestBase):

    def test_template_exists(self):
        self.assertTrue(os.path.exists(documents.TEMPLATE_PATH))

    def test_generation_fills_contract_data(self):
        services = [
            {'name': 'Подготовка пакета документов для предоставления'
                     ' земельного участка на Совете'},
            {'name': 'Подготовка бизнес-плана.'},
        ]
        out = documents.generate_contract_docx(
            self.conn, self._get(), services
        )
        self.addCleanup(os.remove, out)
        self.assertTrue(os.path.getsize(out) > 10000)

        doc = docx.Document(out)
        full = '\n'.join(p.text for p in doc.paragraphs)
        for token in ('ООО «Ромашка»', 'Петров Петр Петрович',
                      'petrov@example.com', 'Подготовка бизнес-плана',
                      'НДС 22%'):
            self.assertIn(token, full)
        # незаменённых меток быть не должно
        self.assertNotIn('{{', full)
        # сумма и НДС по контрольному примеру
        self.assertIn('Сто шестьдесят тысяч', full)
        vat = pricing.vat_from_total(16000000, '22')
        self.assertIn(
            documents.money_in_doc(vat).split('(')[1].split(')')[0], full
        )
        # реквизиты заказчика в таблице
        tables_text = ''
        for t in doc.tables:
            for r in t.rows:
                for c in r.cells:
                    tables_text += c.text + '\n'
        self.assertIn('ИНН: 5250000000', tables_text)
        self.assertIn('КПП: 525001001', tables_text)

    def test_money_in_doc_format(self):
        self.assertEqual(
            documents.money_in_doc(16000000),
            '160 000 (Сто шестьдесят тысяч) рублей 00 копеек'
        )
        self.assertEqual(
            documents.money_in_doc(2885246),
            '28 852 (Двадцать восемь тысяч восемьсот пятьдесят два)'
            ' рубля 46 копеек'
        )

    def test_ip_preamble(self):
        self.conn.execute(
            "UPDATE contracts SET customer_type='ip',"
            " signatory_basis='Свидетельства о госрегистрации' WHERE id=?",
            (self.contract_id,)
        )
        self.conn.commit()
        text = documents._preamble(self._get())
        self.assertIn('Индивидуальный предприниматель', text)
        self.assertIn('Свидетельства', text)

    def test_individual_preamble(self):
        self.conn.execute(
            "UPDATE contracts SET customer_type='individual' WHERE id=?",
            (self.contract_id,)
        )
        self.conn.commit()
        text = documents._preamble(self._get())
        self.assertIn('Гражданин Российской Федерации', text)


class FileVersionTests(DocumentsTestBase):

    def test_allowed_extensions(self):
        self.assertTrue(documents.allowed_file('plan.dwg'))
        self.assertTrue(documents.allowed_file('акт.PDF'))
        self.assertFalse(documents.allowed_file('script.exe'))

    def test_save_version_makes_current(self):
        import io

        class F:
            filename = 'dogovor.docx'

            def read(self):
                return b'test-content'

        fid1, err = documents.save_version(
            self.conn, self.contract_id, 'project', F(), self.uid
        )
        self.assertIsNone(err)
        fid2, err = documents.save_version(
            self.conn, self.contract_id, 'project', F(), self.uid
        )
        self.assertIsNone(err)
        rows = self.conn.execute(
            "SELECT * FROM contract_files WHERE contract_id=?"
            " AND category='project' ORDER BY version",
            (self.contract_id,)
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual([r['is_current'] for r in rows], [0, 1])
        self.assertEqual([r['version'] for r in rows], [1, 2])
        # файлы физически лежат в uploads/contracts/<id>/
        for r in rows:
            self.assertTrue(
                os.path.exists(documents.file_abs_path(r))
            )
            self.addCleanup(os.remove, documents.file_abs_path(r))

    def test_reject_bad_extension(self):
        class F:
            filename = 'virus.exe'

            def read(self):
                return b'x'

        fid, err = documents.save_version(
            self.conn, self.contract_id, 'other', F(), self.uid
        )
        self.assertIsNone(fid)
        self.assertIn('Недопустимый формат', err)


if __name__ == '__main__':
    unittest.main()
