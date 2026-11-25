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


class DeleteTests(RoutesTestBase):

    def _sql(self, sql, params=()):
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(sql, params).fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()

    def _delete_data(self, cid):
        r = self.client.get(f'/contracts/{cid}')
        self.assertEqual(r.status_code, 200)
        with self.client.session_transaction() as s:
            token = s['_contract_delete_token']
        number = self._sql(
            'SELECT inner_number FROM contracts WHERE id=?', (cid,)
        )[0][0]
        return {'delete_token': token, 'confirm_number': number or str(cid)}

    def _assert_retained(self, cid):
        self.assertEqual(len(self._sql(
            'SELECT id FROM contracts WHERE id=?', (cid,)
        )), 1)
        self.assertEqual(self._sql(
            "SELECT COUNT(*) FROM activity_log WHERE action='contract_delete'"
        )[0][0], 0)

    def test_delete_services_and_commit_audit(self):
        self._login()
        cid = self._seed_contract()
        other = self._seed_contract(inner_number='ДГ-2026-0002')
        self._sql('INSERT INTO contract_services (contract_id, name) VALUES (?, ?)',
                  (cid, 'Тестовая услуга'))
        r = self.client.post(f'/contracts/{cid}/delete', data=self._delete_data(cid))
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.location.endswith('/contracts/'))
        self.assertEqual(self._sql('SELECT id FROM contracts WHERE id=?', (cid,)), [])
        self.assertEqual(self._sql(
            'SELECT id FROM contract_services WHERE contract_id=?', (cid,)), [])
        self.assertEqual(len(self._sql('SELECT id FROM contracts WHERE id=?', (other,))), 1)
        audit = self._sql(
            "SELECT user_id, request_id, detail FROM activity_log WHERE action='contract_delete'"
        )
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0][0], self.executor_id)
        self.assertIsNone(audit[0][1])
        self.assertIn('ДГ-2026-0001', audit[0][2])
        self.assertEqual(self.client.get(f'/contracts/{cid}').status_code, 404)

    def test_get_and_anonymous_delete(self):
        cid = self._seed_contract()
        self.assertEqual(self.client.post(f'/contracts/{cid}/delete').status_code, 302)
        self._login()
        self.assertEqual(self.client.get(f'/contracts/{cid}/delete').status_code, 405)
        self._assert_retained(cid)

    def test_token_and_number_required(self):
        self._login()
        cid = self._seed_contract()
        valid = self._delete_data(cid)
        for token in ('', 'wrong', 'я'):
            with self.subTest(token=token):
                data = dict(valid, delete_token=token)
                self.assertEqual(self.client.post(
                    f'/contracts/{cid}/delete', data=data).status_code, 400)
                self._assert_retained(cid)
        for number in ('', 'wrong'):
            with self.subTest(number=number):
                r = self.client.post(f'/contracts/{cid}/delete',
                                     data=dict(valid, confirm_number=number))
                self.assertEqual(r.status_code, 302)
                self._assert_retained(cid)

    def test_permissions_and_hidden_form(self):
        self._login()
        cid = self._seed_contract()
        data = self._delete_data(cid)
        for denied in ('can_view_contracts', 'can_edit_contracts'):
            with self.subTest(permission=denied):
                self._login(perms={
                    'can_view_contracts': int(denied != 'can_view_contracts'),
                    'can_edit_contracts': int(denied != 'can_edit_contracts'),
                })
                self.assertEqual(self.client.post(
                    f'/contracts/{cid}/delete', data=data).status_code, 403)
                self._assert_retained(cid)
        r = self.client.get(f'/contracts/{cid}')
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('name="confirm_number"', r.get_data(as_text=True))

    def test_foreign_contract_requires_management(self):
        self._login()
        cid = self._seed_contract()
        data = self._delete_data(cid)
        self._sql(
            "INSERT INTO users (username, password, full_name, role) VALUES (?, ?, ?, ?)",
            ('other', 'x', 'Другой исполнитель', 'contract_executor'))
        uid = self._sql('SELECT id FROM users WHERE username=?', ('other',))[0][0]
        self._sql('UPDATE contracts SET executor_id=?, created_by=? WHERE id=?',
                  (uid, uid, cid))
        self._login(perms={'can_view_contracts': 1, 'can_edit_contracts': 1,
                           'can_view_all_contracts': 1})
        self.assertEqual(self.client.post(
            f'/contracts/{cid}/delete', data=data).status_code, 403)
        self._assert_retained(cid)
        self._login()
        self.assertEqual(self.client.post(
            f'/contracts/{cid}/delete', data=data).status_code, 302)
        self.assertEqual(self._sql('SELECT id FROM contracts WHERE id=?', (cid,)), [])

    def test_stage_state_registration_guards(self):
        self._login()
        cid = self._seed_contract()
        data = self._delete_data(cid)
        for column, value, original in (
                ('stage', 'in_work', 'draft'), ('state', 'completed', 'active'),
                ('reg_number', '81/26', None), ('reg_date', '2026-11-25', None)):
            with self.subTest(column=column):
                self._sql(f'UPDATE contracts SET {column}=? WHERE id=?', (value, cid))
                r = self.client.post(f'/contracts/{cid}/delete', data=data)
                self.assertEqual(r.status_code, 302)
                self._assert_retained(cid)
                html = self.client.get(f'/contracts/{cid}').get_data(as_text=True)
                self.assertIn('Удаление недоступно:', html)
                self.assertNotIn('name="confirm_number"', html)
                self._sql(f'UPDATE contracts SET {column}=? WHERE id=?', (original, cid))

    def test_related_records_block_deletion(self):
        self._login()
        cid = self._seed_contract()
        data = self._delete_data(cid)
        fixtures = (
            ('contract_stage_log', 'INSERT INTO contract_stage_log (contract_id, from_stage, to_stage) VALUES (?, ?, ?)', (cid, 'customer_review', 'draft')),
            ('contract_payments', 'INSERT INTO contract_payments (contract_id, amount_kop) VALUES (?, ?)', (cid, 100)),
            ('contract_reviews', 'INSERT INTO contract_reviews (contract_id, body) VALUES (?, ?)', (cid, 'Совет')),
            ('contract_activities', 'INSERT INTO contract_activities (contract_id, name) VALUES (?, ?)', (cid, 'Проверка')),
        )
        for table, sql, params in fixtures:
            with self.subTest(table=table):
                self._sql(sql, params)
                self.assertEqual(self.client.post(
                    f'/contracts/{cid}/delete', data=data).status_code, 302)
                self._assert_retained(cid)
                self.assertEqual(len(self._sql(
                    f'SELECT id FROM {table} WHERE contract_id=?', (cid,))), 1)
                self._sql(f'DELETE FROM {table} WHERE contract_id=?', (cid,))

    def test_uploaded_file_is_preserved(self):
        self._login()
        cid = self._seed_contract()
        r = self.client.post(f'/contracts/{cid}/files/upload', data={
            'category': 'project', 'file': (io.BytesIO(b'keep-me'), 'test.docx'),
        }, content_type='multipart/form-data')
        self.assertEqual(r.status_code, 302)
        data = self._delete_data(cid)
        html = self.client.get(f'/contracts/{cid}').get_data(as_text=True)
        self.assertIn('У договора есть прикреплённые файлы', html)
        self.assertNotIn('name="confirm_number"', html)
        self.assertEqual(self.client.post(
            f'/contracts/{cid}/delete', data=data).status_code, 302)
        self._assert_retained(cid)
        fid = self._sql('SELECT id FROM contract_files WHERE contract_id=?', (cid,))[0][0]
        r = self.client.get(f'/contracts/{cid}/files/{fid}/download')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_data(), b'keep-me')
        r.close()

    def test_linked_task_is_preserved(self):
        self._login()
        cid = self._seed_contract()
        data = self._delete_data(cid)
        self._sql('INSERT INTO tasks (title, created_by) VALUES (?, ?)',
                  ('Сохранить задачу', self.executor_id))
        tid = self._sql('SELECT id FROM tasks WHERE title=?', ('Сохранить задачу',))[0][0]
        self._sql('INSERT INTO contract_tasks (contract_id, task_id) VALUES (?, ?)', (cid, tid))
        self.assertEqual(self.client.post(
            f'/contracts/{cid}/delete', data=data).status_code, 302)
        self._assert_retained(cid)
        self.assertEqual(len(self._sql('SELECT id FROM tasks WHERE id=?', (tid,))), 1)
        self.assertEqual(len(self._sql(
            'SELECT id FROM contract_tasks WHERE contract_id=?', (cid,))), 1)

    def test_failed_audit_rolls_back_services_and_counter(self):
        from unittest.mock import patch
        self._login()
        cid = self._seed_contract()
        data = self._delete_data(cid)
        self._sql('INSERT INTO contract_services (contract_id, name) VALUES (?, ?)', (cid, 'Услуга'))
        settings = self._sql('SELECT key, value FROM contract_settings ORDER BY key')
        with patch('contracts.routes.log_action', return_value=False):
            r = self.client.post(f'/contracts/{cid}/delete', data=data)
        self.assertEqual(r.status_code, 302)
        self._assert_retained(cid)
        self.assertEqual(len(self._sql(
            'SELECT id FROM contract_services WHERE contract_id=?', (cid,))), 1)
        self.assertEqual(self._sql('SELECT key, value FROM contract_settings ORDER BY key'), settings)
        with patch('contracts.routes.log_action', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.client.post(f'/contracts/{cid}/delete', data=data)
        self._assert_retained(cid)
        self.assertEqual(self._sql('SELECT key, value FROM contract_settings ORDER BY key'), settings)

    def test_number_not_reused_after_middle_and_last_delete(self):
        from datetime import datetime
        from contracts import repository
        self._login()
        year = datetime.utcnow().year
        first = self._seed_contract(inner_number=f'ДГ-{year}-0001')
        last = self._seed_contract(inner_number=f'ДГ-{year}-0003')
        for cid in (first, last):
            r = self.client.post(f'/contracts/{cid}/delete', data=self._delete_data(cid))
            self.assertEqual(r.status_code, 302)
            self.assertEqual(self._sql('SELECT id FROM contracts WHERE id=?', (cid,)), [])
            conn = sqlite3.connect(self.db_path)
            try:
                number = repository.next_inner_number(conn)
                self.assertEqual(number, f'ДГ-{year}-0004')
                conn.rollback()
            finally:
                conn.close()

    def test_missing_and_repeated_delete(self):
        self._login()
        cid = self._seed_contract()
        data = self._delete_data(cid)
        self.assertEqual(self.client.post('/contracts/999999/delete', data=data).status_code, 404)
        self._assert_retained(cid)
        self.assertEqual(self.client.post(f'/contracts/{cid}/delete', data=data).status_code, 302)
        self.assertEqual(self.client.post(f'/contracts/{cid}/delete', data=data).status_code, 404)
        self.assertEqual(self._sql(
            "SELECT COUNT(*) FROM activity_log WHERE action='contract_delete'"
        )[0][0], 1)


if __name__ == '__main__':
    unittest.main()
