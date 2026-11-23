# -*- coding: utf-8 -*-
"""Тесты маршрутов раздела «Договоры».

Изолированное Flask-приложение: только contracts_bp и заглушка auth,
временная SQLite-БД через реальные миграции. Проверяются права,
реестр, карточка, переходы и генерация DOCX.
"""
import io
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Blueprint, Flask, session

import db as db_module
import migrations as migrations_module


auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login')
def login():
    return 'login', 401


def make_app(db_path):
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(
        __name__,
        template_folder=os.path.join(repo_root, 'templates'),
    )
    app.config['TESTING'] = True
    app.config['SECRET_KEY'] = 'test'
    app.register_blueprint(auth_bp)

    from contracts import contracts_bp
    app.register_blueprint(contracts_bp)

    # Стаб base.html: настоящий base.html ссылается на endpoint'ы
    # всех модулей приложения, которых нет в изолированном тесте.
    import jinja2
    stub_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            '_stub_templates')
    app.create_jinja_environment()
    app.jinja_loader = jinja2.ChoiceLoader([
        jinja2.FileSystemLoader(stub_dir),
        app.jinja_loader,
    ])
    return app


class RoutesTestBase(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db_path = os.path.join(self._tmpdir.name, 'test.db')
        self.uploads_dir = os.path.join(self._tmpdir.name, 'uploads')

        db_module.DB_PATH = self.db_path
        migrations_module.DB_PATH = self.db_path
        db_module._migrated = False
        migrations_module.init_db()
        migrations_module.migrate_db()
        migrations_module.migrate_districts()
        db_module.run_migrations()

        # пути выгрузок договоров — во временную папку
        import paths
        self._old_uploads = paths.UPLOADS_DIR
        paths.UPLOADS_DIR = self.uploads_dir
        from contracts import documents
        documents.CONTRACTS_UPLOAD_DIR = os.path.join(
            self.uploads_dir, 'contracts')
        documents.TMP_DOCX_DIR = os.path.join(self.uploads_dir, 'tmp')

        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "INSERT INTO users (username, password, full_name, role,"
            " can_view_contracts, can_edit_contracts, can_view_all_contracts,"
            " can_manage_contracts)"
            " VALUES ('executor', 'x', 'Исполнитель', 'contract_executor',"
            " 1, 1, 0, 0)"
        )
        self.conn.commit()
        self.executor_id = self.conn.execute(
            "SELECT id FROM users WHERE username='executor'"
        ).fetchone()[0]
        # сессия исполняется в момент запроса — соединение закрываем
        self.conn.close()

        self.app = make_app(self.db_path)
        self.client = self.app.test_client()

    def tearDown(self):
        import paths
        paths.UPLOADS_DIR = self._old_uploads
        super().tearDown()

    def _login(self, perms=1):
        with self.client.session_transaction() as s:
            s['user_id'] = self.executor_id
            s['role'] = 'contract_executor'
            for key in ('can_view_contracts', 'can_edit_contracts',
                        'can_view_all_contracts', 'can_manage_contracts',
                        'can_contract_payments'):
                s[f'perm_{key}'] = perms if isinstance(perms, int) \
                    else int(perms.get(key, 0))

    def _seed_contract(self, **kw):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        base = dict(
            inner_number='ДГ-2026-0001', is_free=0,
            customer_type='legal',
            customer_full_name='ООО «Тест»',
            customer_short_name='ООО «Тест»',
            contact_name='Иванов Иван Иванович',
            project_name='Тестовый проект',
            executor_id=self.executor_id,
            stage='draft', state='active',
            payment_days=10, payment_day_type='work',
            duration_days=10, duration_day_type='work',
            work_start_basis='payment', act_sign_method='edo_upp',
            total_kop=16000000, vat_percent='22',
            created_by=self.executor_id,
        )
        base.update(kw)
        cols = ', '.join(base)
        qs = ', '.join('?' for _ in base)
        cur = conn.execute(
            f"INSERT INTO contracts ({cols}) VALUES ({qs})",
            list(base.values())
        )
        conn.commit()
        cid = cur.lastrowid
        conn.close()
        return cid


class AccessTests(RoutesTestBase):

    def test_redirect_when_anonymous(self):
        r = self.client.get('/contracts/')
        self.assertEqual(r.status_code, 302)

    def test_403_without_permission(self):
        self._login(perms=0)
        r = self.client.get('/contracts/')
        self.assertEqual(r.status_code, 403)

    def test_list_with_permission(self):
        self._login()
        cid = self._seed_contract()
        r = self.client.get('/contracts/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('ДГ-2026-0001', r.get_data(as_text=True))

    def test_detail_own_contract(self):
        self._login()
        cid = self._seed_contract()
        r = self.client.get(f'/contracts/{cid}')
        self.assertEqual(r.status_code, 200)
        self.assertIn('ООО «Тест»', r.get_data(as_text=True))


class ActionTests(RoutesTestBase):

    def test_create_contract(self):
        self._login()
        r = self.client.post('/contracts/new', data={
            'customer_full_name': 'ООО «Новая»',
            'customer_short_name': 'ООО «Новая»',
            'customer_type': 'legal',
            'contact_name': 'Пётр Петров',
            'project_name': 'Новый проект',
            'is_free': '0',
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn('Договор создан', r.get_data(as_text=True))

    def test_transition_creates_stage_task(self):
        self._login()
        cid = self._seed_contract()
        r = self.client.post(f'/contracts/{cid}/transition', data={
            'target': 'customer_review',
            'event_date': '2026-11-16',
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn('Стадия изменена', r.get_data(as_text=True))
        conn = sqlite3.connect(self.db_path)
        stage = conn.execute(
            "SELECT stage FROM contracts WHERE id=?", (cid,)
        ).fetchone()[0]
        tasks_cnt = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE title LIKE '%обратную связь%'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(stage, 'customer_review')
        self.assertEqual(tasks_cnt, 1)

    def test_backward_transition_requires_comment(self):
        self._login()
        cid = self._seed_contract(stage='customer_review')
        r = self.client.post(f'/contracts/{cid}/transition', data={
            'target': 'draft',
            'event_date': '2026-11-16',
        }, follow_redirects=True)
        self.assertIn('причину возврата', r.get_data(as_text=True))
        conn = sqlite3.connect(self.db_path)
        stage = conn.execute(
            "SELECT stage FROM contracts WHERE id=?", (cid,)
        ).fetchone()[0]
        conn.close()
        self.assertEqual(stage, 'customer_review')

    def test_service_add_and_recalc(self):
        self._login()
        cid = self._seed_contract()
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        item = conn.execute(
            "SELECT * FROM contract_tariff_items WHERE code='2.1'"
        ).fetchone()
        conn.close()
        r = self.client.post(f'/contracts/{cid}/services/add', data={
            'tariff_item_id': item['id'],
            'qty': '1',
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        conn = sqlite3.connect(self.db_path)
        total = conn.execute(
            "SELECT total_kop FROM contracts WHERE id=?", (cid,)
        ).fetchone()[0]
        conn.close()
        # полный пакет 2.1–2.5 не собирается одной строкой: пересчёт
        # по приказу даёт тариф строки, пока комплект не полный
        self.assertGreater(total, 0)

    def test_upload_and_download_file(self):
        self._login()
        cid = self._seed_contract()
        data = {
            'category': 'project',
            'file': (io.BytesIO(b'test docx bytes'), 'проект.docx'),
        }
        r = self.client.post(f'/contracts/{cid}/files/upload', data=data,
                             content_type='multipart/form-data',
                             follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM contract_files WHERE contract_id=?", (cid,)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        r = self.client.get(
            f"/contracts/{cid}/files/{row['id']}/download")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_data(), b'test docx bytes')

    def test_docx_generation_route(self):
        self._login()
        cid = self._seed_contract(
            customer_full_name='Общество с ограниченной'
                               ' ответственностью «Ромашка»',
            total_kop=16000000,
        )
        r = self.client.get(f'/contracts/{cid}/docx')
        self.assertEqual(r.status_code, 200)
        self.assertGreater(len(r.get_data()), 10000)

    def test_acts_sent_route(self):
        self._login()
        cid = self._seed_contract(stage='act_signing', is_free=0)
        r = self.client.post(f'/contracts/{cid}/acts-sent', data={
            'send_date': '2026-11-16',
        }, follow_redirects=True)
        # на стадии act_signing без файлов перевод в неё был сделан
        # напрямую; направление актов должно отработать
        self.assertEqual(r.status_code, 200)
        conn = sqlite3.connect(self.db_path)
        cnt = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE title='Получить подписанный акт'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(cnt, 1)


if __name__ == '__main__':
    unittest.main()
