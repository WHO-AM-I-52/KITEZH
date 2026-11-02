# -*- coding: utf-8 -*-
"""Тесты единого справочника адресатов (организации, люди, связи).

Работают на временной SQLite-БД через реальную последовательность
миграций приложения (migrations.migrate_db() + db.run_migrations()),
без production-приложения и без рабочей БД.
"""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, Blueprint, session

import db as db_module
import migrations as migrations_module
from services import contact_directory
from services.contact_directory import DirectoryError


def _stub_blueprints(app):
    requests_bp = Blueprint('requests', __name__)

    @requests_bp.route('/')
    def index():
        return 'requests-index'

    auth_bp = Blueprint('auth', __name__)

    @auth_bp.route('/login')
    def login():
        return 'login'

    app.register_blueprint(requests_bp)
    app.register_blueprint(auth_bp)


class DirectoryTestBase(unittest.TestCase):
    """Временная БД + тестовое Flask-приложение с phonebook_bp."""

    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db_path = os.path.join(self._tmpdir.name, 'test.db')

        db_module.DB_PATH = self.db_path
        migrations_module.DB_PATH = self.db_path
        db_module._migrated = False
        # Реальная последовательность запуска приложения (app.py).
        migrations_module.init_db()
        migrations_module.migrate_db()
        migrations_module.migrate_districts()
        db_module.run_migrations()

        self._seed_users()

        from routes.phonebook_routes import phonebook_bp
        from routes.phonebook_import import pb_import_bp
        from letters.routes import letters_bp
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.app = Flask(
            __name__, template_folder='templates', root_path=project_root
        )
        self.app.secret_key = 'test-secret'
        _stub_blueprints(self.app)
        self.app.register_blueprint(phonebook_bp)
        self.app.register_blueprint(pb_import_bp)
        self.app.register_blueprint(letters_bp)
        self.client = self.app.test_client()

    def _seed_users(self):
        from core.auth_utils import hash_pw
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        for uid, username, role in (
            (1, 'admin', 'admin'),
            (2, 'employee', 'employee'),
            (3, 'manager', 'manager'),
        ):
            conn.execute(
                'INSERT OR REPLACE INTO users (id, username, password, '
                'full_name, role, is_active) VALUES (?,?,?,?,?,1)',
                (uid, username, hash_pw('x'), username.title(), role),
            )
        conn.commit()
        conn.close()

    def _make_org(self, name='ООО Тест', inn=None):
        conn = self._conn()
        try:
            result = contact_directory.create_org(
                conn, name, address='адрес', inn=inn, user_id=2)
            conn.commit()
            if result['status'] == 'created':
                return result['org_id']
            return result['org']['id']
        finally:
            conn.close()

    def _login(self, user_id, role, perms=None):
        with self.client.session_transaction() as s:
            s['user_id'] = user_id
            s['role'] = role
            for key, value in (perms or {}).items():
                s[f'perm_{key}'] = value

    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        return conn

    def _query_one(self, sql, params=()):
        conn = self._conn()
        try:
            return conn.execute(sql, params).fetchone()
        finally:
            conn.close()


class MigrationTest(DirectoryTestBase):
    def test_fresh_db_has_directory_schema(self):
        cols = {r[1] for r in self._conn().execute(
            'PRAGMA table_info(phonebook_employment)').fetchall()}
        self.assertIn('contact_id', cols)
        self.assertIn('org_id', cols)
        self.assertIn('position', cols)
        self.assertIn('archived', cols)

        for table in ('phonebook', 'phonebook_orgs'):
            cols = {r[1] for r in self._conn().execute(
                f'PRAGMA table_info({table})').fetchall()}
            self.assertIn('archived', cols)
            self.assertIn('created_by', cols)
            self.assertIn('created_at', cols)

        letter_cols = {r[1] for r in self._conn().execute(
            'PRAGMA table_info(letters)').fetchall()}
        for col in ('org_id', 'contact_id', 'employment_id',
                    'snapshot_org_name', 'snapshot_full_name',
                    'snapshot_position', 'snapshot_email', 'snapshot_phone'):
            self.assertIn(col, letter_cols)

    def test_backfill_creates_links_once_and_not_again(self):
        # Старая БД: полная схема предыдущей версии, без нового блока
        # db._migrate (контакт с org_id, без таблицы связей).
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old_path = os.path.join(tmp.name, 'old.db')
        db_module.DB_PATH = old_path
        migrations_module.DB_PATH = old_path
        db_module._migrated = False
        migrations_module.init_db()
        migrations_module.migrate_db()
        migrations_module.migrate_districts()

        def old_conn():
            c = sqlite3.connect(old_path)
            c.row_factory = sqlite3.Row
            return c

        conn = old_conn()
        cur = conn.execute(
            "INSERT INTO phonebook_orgs (name) VALUES ('Тестовая орг.')")
        org_id = cur.lastrowid
        conn.execute(
            "INSERT INTO phonebook (org_id, position, full_name) "
            "VALUES (?, 'Директор', 'Иванов Иван')", (org_id,))
        conn.commit()
        conn.close()

        # Первое обновление до новой схемы — backfill создаёт связь.
        db_module._migrated = False
        db_module.run_migrations()

        def q(sql):
            c = old_conn()
            try:
                return c.execute(sql).fetchone()[0]
            finally:
                c.close()

        links = q('SELECT COUNT(*) FROM phonebook_employment')
        self.assertEqual(links, 1)

        # Повторный запуск не создаёт дублей.
        db_module._migrated = False
        db_module.run_migrations()
        links = q('SELECT COUNT(*) FROM phonebook_employment')
        self.assertEqual(links, 1)

        # После удаления связи повторная миграция не восстанавливает её
        # (флаг однократного backfill).
        conn = old_conn()
        conn.execute('DELETE FROM phonebook_employment')
        conn.commit()
        conn.close()
        db_module._migrated = False
        db_module.run_migrations()
        links = q('SELECT COUNT(*) FROM phonebook_employment')
        self.assertEqual(links, 0)

    def test_old_letter_untouched_by_migration(self):
        conn = self._conn()
        conn.execute(
            "INSERT INTO counterparties (name) VALUES ('Старый контрагент')")
        conn.execute(
            "INSERT INTO letters (date, subject, created_by, created_at, "
            "counterparty_id) VALUES ('2026-01-01', 'Тест', 1, '2026', 1)")
        conn.commit()
        conn.close()

        row = self._query_one('SELECT * FROM letters WHERE id = 1')
        self.assertEqual(row['counterparty_id'], 1)
        self.assertIsNone(row['org_id'])
        self.assertIsNone(row['contact_id'])
        self.assertEqual(row['snapshot_org_name'] or '', '')


class ServiceTest(DirectoryTestBase):
    def test_org_without_person_and_person_without_org(self):
        org_id = self._make_org('ООО Без людей')
        self.assertIsNotNone(org_id)

        conn = self._conn()
        try:
            result = contact_directory.create_contact(
                conn, 'Сидоров Сидор', user_id=2)
            conn.commit()
            self.assertIsNotNone(result['contact_id'])
            self.assertIsNone(result['employment_id'])
        finally:
            conn.close()

    def test_create_org_inn_match_returns_existing(self):
        first = self._make_org('ООО Первая', inn='1234567890')
        second = self._make_org('ООО Вторая с тем же ИНН', inn='1234567890')
        self.assertEqual(first, second)

    def test_two_simultaneous_employments(self):
        org1 = self._make_org('ООО Основная')
        org2 = self._make_org('ООО Совместительство')
        conn = self._conn()
        try:
            created = contact_directory.create_contact(
                conn, 'Петров Пётр', org_id=org1, position='Инженер', user_id=2)
            conn.commit()
            contact_id = created['contact_id']

            # Второе одновременное место работы.
            emp2 = contact_directory.set_main_employment(
                conn, contact_id, org2, position='Совместитель', user_id=2)
            conn.commit()

            links = conn.execute(
                'SELECT * FROM phonebook_employment WHERE contact_id = ? '
                'ORDER BY id', (contact_id,)).fetchall()
            self.assertEqual(len(links), 2)
            self.assertEqual(links[0]['org_id'], org1)
            self.assertEqual(links[1]['org_id'], org2)
            # Совместимые поля указывают на основную (последнюю) работу.
            contact = conn.execute(
                'SELECT * FROM phonebook WHERE id = ?', (contact_id,)).fetchone()
            self.assertEqual(contact['org_id'], org2)
            self.assertEqual(contact['position'], 'Совместитель')
        finally:
            conn.close()

    def test_job_change_preserves_old_link(self):
        org1 = self._make_org('ООО Прошлая')
        org2 = self._make_org('ООО Новая')
        conn = self._conn()
        try:
            created = contact_directory.create_contact(
                conn, 'Фёдоров Фёдор', org_id=org1, position='Старшая', user_id=2)
            conn.commit()
            contact_id = created['contact_id']

            contact_directory.set_main_employment(
                conn, contact_id, org2, position='Новая должность', user_id=2)
            conn.commit()

            links = conn.execute(
                'SELECT * FROM phonebook_employment WHERE contact_id = ?',
                (contact_id,)).fetchall()
            self.assertEqual(len(links), 2)
            self.assertEqual(links[0]['org_id'], org1)
            self.assertIsNone(links[0]['end_date'])  # прежняя связь не закрыта
        finally:
            conn.close()

    def test_letter_snapshot_validation(self):
        org = self._make_org('ООО Снимок')
        conn = self._conn()
        try:
            created = contact_directory.create_contact(
                conn, 'Смирнов Смирн', org_id=org, position='Мастер',
                email='smirnov@example.ru', user_id=2)
            conn.commit()

            snap = contact_directory.letter_snapshot(
                conn, org_id=org, contact_id=created['contact_id'])
            self.assertEqual(snap['snapshot_org_name'], 'ООО Снимок')
            self.assertEqual(snap['snapshot_full_name'], 'Смирнов Смирн')
            self.assertEqual(snap['snapshot_position'], 'Мастер')
            self.assertEqual(snap['snapshot_email'], 'smirnov@example.ru')

            with self.assertRaises(DirectoryError):
                contact_directory.letter_snapshot(conn, org_id=9999)
            with self.assertRaises(DirectoryError):
                contact_directory.letter_snapshot(
                    conn, contact_id=9999, org_id=org)
            # Чужая связь отклоняется.
            with self.assertRaises(DirectoryError):
                contact_directory.letter_snapshot(
                    conn, org_id=org, contact_id=created['contact_id'],
                    employment_id=9999)
        finally:
            conn.close()

    def test_archived_recipient_rejected_in_snapshot(self):
        org = self._make_org('ООО Архив')
        conn = self._conn()
        try:
            created = contact_directory.create_contact(
                conn, 'Архивов Архив', org_id=org, user_id=2)
            conn.commit()
            contact_directory.archive_contact(conn, created['contact_id'])
            conn.commit()

            with self.assertRaises(DirectoryError):
                contact_directory.letter_snapshot(
                    conn, org_id=org, contact_id=created['contact_id'])
        finally:
            conn.close()

    def test_delete_with_dependencies_refused(self):
        org = self._make_org('ООО Зависимости')
        conn = self._conn()
        try:
            created = contact_directory.create_contact(
                conn, 'Зависимов Зависим', org_id=org, user_id=2)
            conn.commit()
            contact_id = created['contact_id']

            # У организации есть контакт — удаление запрещено.
            with self.assertRaises(DirectoryError):
                contact_directory.delete_org(conn, org)
            # Архивирование разрешено.
            contact_directory.archive_org(conn, org)
            conn.commit()

            # Письмо ссылается на контакт — удаление контакта запрещено.
            conn.execute(
                "INSERT INTO letters (date, subject, created_by, created_at, "
                "contact_id, org_id) VALUES ('2026-01-02', 'x', 1, '2026', ?, ?)",
                (contact_id, org))
            conn.commit()
            with self.assertRaises(DirectoryError):
                contact_directory.delete_contact(conn, contact_id)
        finally:
            conn.close()


class RoutesTest(DirectoryTestBase):
    def _csrf(self, token='a' * 64):
        with self.client.session_transaction() as s:
            s['dir_csrf'] = token
        return token

    def test_employee_can_create_contact_and_org(self):
        self._login(2, 'employee')
        token = self._csrf()

        resp = self.client.post('/phonebook/api/orgs', json={
            'name': 'ООО Маршрут', 'address': 'улица 1',
        }, headers={'X-Dir-CSRF': token})
        self.assertEqual(resp.status_code, 201)
        org_id = resp.get_json()['org_id']

        resp = self.client.post('/phonebook/api/contacts', json={
            'full_name': 'Маршрутов Маршрут', 'org_id': org_id,
            'position': 'Водитель',
        }, headers={'X-Dir-CSRF': token})
        self.assertEqual(resp.status_code, 201)
        data = resp.get_json()
        self.assertIsNotNone(data['employment_id'])

        # Связь создана.
        links = self._query_one(
            'SELECT COUNT(*) AS c FROM phonebook_employment')['c']
        self.assertEqual(links, 1)

    def test_api_create_requires_csrf(self):
        self._login(2, 'employee')
        resp = self.client.post('/phonebook/api/orgs', json={'name': 'Без токена'})
        self.assertEqual(resp.status_code, 400)

    def test_api_create_unauthorized_rejected(self):
        token = self._csrf()
        resp = self.client.post('/phonebook/api/orgs', json={'name': 'Аноним'},
                                headers={'X-Dir-CSRF': token})
        self.assertIn(resp.status_code, (301, 302))

    def test_api_duplicate_contact_returns_existing(self):
        self._login(2, 'employee')
        token = self._csrf()
        resp = self.client.post('/phonebook/api/orgs', json={'name': 'ООО Дубль'},
                                headers={'X-Dir-CSRF': token})
        org_id = resp.get_json()['org_id']

        resp = self.client.post('/phonebook/api/contacts', json={
            'full_name': 'Дублёв Дубль', 'org_id': org_id,
        }, headers={'X-Dir-CSRF': token})
        self.assertEqual(resp.status_code, 201)

        resp = self.client.post('/phonebook/api/contacts', json={
            'full_name': 'Дублёв Дубль', 'org_id': org_id,
        }, headers={'X-Dir-CSRF': token})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()['status'], 'exists')

        # Новый контакт не создан.
        contacts = self._query_one(
            "SELECT COUNT(*) AS c FROM phonebook WHERE full_name = 'Дублёв Дубль'")['c']
        self.assertEqual(contacts, 1)

    def test_api_search_hides_personal_phones_and_archived(self):
        self._login(2, 'employee')
        token = self._csrf()
        resp = self.client.post('/phonebook/api/orgs', json={'name': 'ООO Поиск'},
                                headers={'X-Dir-CSRF': token})
        org_id = resp.get_json()['org_id']
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO phonebook (org_id, full_name, phone_personal, "
                "phone_work, email) VALUES (?, 'Ищущий Иск', '89001234567', "
                "'+78000000000', 'ish@example.ru')", (org_id,))
            conn.commit()
        finally:
            conn.close()

        resp = self.client.get('/phonebook/api/search?q=Ищущий')
        self.assertEqual(resp.status_code, 200)
        items = resp.get_json()
        self.assertTrue(items)
        data = json_dumps = None
        import json as _json
        raw = _json.dumps(items)
        self.assertNotIn('89001234567', raw)
        self.assertNotIn('phone_personal', raw)

        # Архивный контакт не предлагается.
        conn = self._conn()
        try:
            conn.execute("UPDATE phonebook SET archived = 1 "
                         "WHERE full_name = 'Ищущий Иск'")
            conn.commit()
        finally:
            conn.close()
        resp = self.client.get('/phonebook/api/search?q=Ищущий')
        self.assertEqual(resp.get_json(), [])

    def test_edit_requires_admin(self):
        self._login(2, 'employee')
        resp = self.client.post('/phonebook/edit', data={
            'contact_id': '1', 'full_name': 'Взлом', 'org_id': '',
        })
        # admin_required перенаправляет на requests.index (302).
        self.assertEqual(resp.status_code, 302)
        row = self._query_one("SELECT COUNT(*) AS c FROM phonebook "
                              "WHERE full_name = 'Взлом'")
        self.assertEqual(row['c'], 0)

    def test_delete_org_with_contacts_refused(self):
        self._login(1, 'admin')
        token = self._csrf()
        resp = self.client.post('/phonebook/api/orgs', json={'name': 'ООО Нельзя'},
                                headers={'X-Dir-CSRF': token})
        org_id = resp.get_json()['org_id']
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO phonebook (org_id, full_name) VALUES (?, 'Человек')",
                (org_id,))
            conn.commit()
        finally:
            conn.close()

        resp = self.client.post('/phonebook/orgs/delete', data={'org_id': org_id})
        self.assertEqual(resp.status_code, 302)
        # Организация не удалена (каскада больше нет).
        row = self._query_one('SELECT archived FROM phonebook_orgs WHERE id = ?',
                              (org_id,))
        self.assertEqual(row['archived'], 0)

    def test_archive_and_restore_org(self):
        self._login(1, 'admin')
        conn = self._conn()
        try:
            cur = conn.execute("INSERT INTO phonebook_orgs (name) VALUES ('ООО Арх')")
            org_id = cur.lastrowid
            conn.commit()
        finally:
            conn.close()

        resp = self.client.post('/phonebook/orgs/archive', data={'org_id': org_id})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self._query_one(
            'SELECT archived FROM phonebook_orgs WHERE id = ?', (org_id,))['archived'], 1)

        resp = self.client.post('/phonebook/orgs/restore', data={'org_id': org_id})
        self.assertEqual(self._query_one(
            'SELECT archived FROM phonebook_orgs WHERE id = ?', (org_id,))['archived'], 0)

    def test_archive_contact_requires_admin(self):
        self._login(2, 'employee')
        resp = self.client.post('/phonebook/archive', data={'contact_id': '1'})
        self.assertEqual(resp.status_code, 302)

    def test_phonebook_add_by_employee(self):
        self._login(2, 'employee')
        resp = self.client.post('/phonebook/add', data={
            'full_name': 'Сотрудник Сотруд', 'org_id': '', 'position': '',
            'room': '', 'phone_work': '', 'phone_ext': '',
            'phone_personal': '', 'email': '', 'inn': '', 'notes': '',
        })
        self.assertEqual(resp.status_code, 302)
        row = self._query_one("SELECT created_by FROM phonebook "
                              "WHERE full_name = 'Сотрудник Сотруд'")
        self.assertEqual(row['created_by'], 2)

    def test_phonebook_add_duplicate_same_org_not_created(self):
        self._login(1, 'admin')
        self._csrf()
        conn = self._conn()
        try:
            cur = conn.execute("INSERT INTO phonebook_orgs (name) VALUES ('ООО Дубль2')")
            org_id = cur.lastrowid
            conn.execute(
                "INSERT INTO phonebook (org_id, full_name) VALUES (?, 'Двойников Двой')",
                (org_id,))
            conn.commit()
        finally:
            conn.close()

        resp = self.client.post('/phonebook/add', data={
            'full_name': 'Двойников Двой', 'org_id': str(org_id),
            'position': '', 'room': '', 'phone_work': '', 'phone_ext': '',
            'phone_personal': '', 'email': '', 'inn': '', 'notes': '',
        })
        self.assertEqual(resp.status_code, 302)
        count = self._query_one(
            "SELECT COUNT(*) AS c FROM phonebook WHERE full_name = 'Двойников Двой'")['c']
        self.assertEqual(count, 1)

    def test_sync_request_to_phonebook_creates_via_service(self):
        from routes.phonebook_routes import sync_request_to_phonebook
        conn = self._conn()
        try:
            with self.app.test_request_context('/'):
                session['user_id'] = 2
                sync_request_to_phonebook(conn, {
                    'applicant_short_name': 'ООО Синхрон',
                    'legal_address': 'адрес синхрона',
                    'applicant_inn': '1112223334',
                    'contact_person': 'Синхронов Синх',
                    'contact_position': 'Менеджер',
                    'contact_phone': '+78001112233',
                    'contact_email': 'sync@example.ru',
                }, request_id=5, user_id=2)
                conn.commit()
        finally:
            conn.close()

        org = self._query_one("SELECT * FROM phonebook_orgs WHERE name = 'ООО Синхрон'")
        self.assertIsNotNone(org)
        self.assertEqual(org['inn'], '1112223334')

        contact = self._query_one(
            "SELECT * FROM phonebook WHERE full_name = 'Синхронов Синх'")
        self.assertIsNotNone(contact)
        self.assertEqual(contact['org_id'], org['id'])

        link = self._query_one(
            'SELECT * FROM phonebook_employment WHERE contact_id = ?',
            (contact['id'],))
        self.assertIsNotNone(link)
        self.assertEqual(link['position'], 'Менеджер')

        # Повторный sync не создаёт дублей.
        conn = self._conn()
        try:
            with self.app.test_request_context('/'):
                session['user_id'] = 2
                sync_request_to_phonebook(conn, {
                    'applicant_short_name': 'ООО Синхрон',
                    'applicant_inn': '1112223334',
                    'contact_person': 'Синхронов Синх',
                    'contact_position': 'Менеджер',
                }, request_id=5, user_id=2)
                conn.commit()
        finally:
            conn.close()
        count = self._query_one(
            "SELECT COUNT(*) AS c FROM phonebook WHERE full_name = 'Синхронов Синх'")['c']
        self.assertEqual(count, 1)



class LettersFlowTest(DirectoryTestBase):
    """Права и снимки писем: прямые POST к маршрутам журнала."""

    def _make_letter(self, user_id=2, org=None, contact=None, subject='Тема'):
        self._login(user_id, 'employee' if user_id == 2 else 'admin')
        data = {'date': '2026-01-01', 'subject': subject,
                'direction': 'out', 'tags': ''}
        if org:
            data['org_id'] = str(org)
        if contact:
            data['contact_id'] = str(contact)
        return self.client.post('/letters/create', data=data)

    def _seed_recipient(self):
        org = self._make_org('ООО Письмо')
        conn = self._conn()
        try:
            created = contact_directory.create_contact(
                conn, 'Письменов Письм', org_id=org, position='Секретарь',
                user_id=2)
            conn.commit()
            return org, created['contact_id']
        finally:
            conn.close()

    def test_letter_permissions_direct_post(self):
        org, contact = self._seed_recipient()
        self._make_letter(user_id=2, org=org, contact=contact,
                          subject='Письмо сотрудника')
        letter = self._query_one(
            "SELECT * FROM letters WHERE subject = 'Письмо сотрудника'")
        self.assertIsNotNone(letter)
        letter_id = letter['id']

        # Manager не редактирует и не удаляет чужое письмо.
        self._login(3, 'manager')
        resp = self.client.post(f'/letters/{letter_id}/edit', data={
            'date': '2026-01-02', 'subject': 'Взлом менеджером',
            'direction': 'out', 'tags': '', 'counterparty_id': '',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            self._query_one('SELECT subject FROM letters WHERE id = ?',
                            (letter_id,))['subject'], 'Письмо сотрудника')

        resp = self.client.post(f'/letters/{letter_id}/delete')
        self.assertEqual(resp.status_code, 302)
        self.assertIsNotNone(
            self._query_one('SELECT id FROM letters WHERE id = ?', (letter_id,)))

        # Автор редактирует своё письмо.
        self._login(2, 'employee')
        resp = self.client.post(f'/letters/{letter_id}/edit', data={
            'date': '2026-01-02', 'subject': 'Правка автора',
            'direction': 'out', 'tags': '', 'counterparty_id': '',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            self._query_one('SELECT subject FROM letters WHERE id = ?',
                            (letter_id,))['subject'], 'Правка автора')

        # Администратор удаляет письмо автора.
        self._login(1, 'admin')
        resp = self.client.post(f'/letters/{letter_id}/delete')
        self.assertEqual(resp.status_code, 302)
        self.assertIsNone(
            self._query_one('SELECT id FROM letters WHERE id = ?', (letter_id,)))

    def test_snapshot_preserved_on_tag_edit(self):
        org, contact = self._seed_recipient()
        self._make_letter(user_id=2, org=org, contact=contact,
                          subject='Снимок сохраняется')
        letter = self._query_one(
            "SELECT * FROM letters WHERE subject = 'Снимок сохраняется'")
        letter_id = letter['id']

        self._login(2, 'employee')
        resp = self.client.post(f'/letters/{letter_id}/edit', data={
            'date': '2026-01-03', 'subject': 'Снимок сохраняется',
            'direction': 'out', 'tags': 'важно', 'counterparty_id': '',
            'org_id': str(org), 'contact_id': str(contact),
            'employment_id': '', 'recipient_update': '',
        })
        self.assertEqual(resp.status_code, 302)

        after = self._query_one('SELECT * FROM letters WHERE id = ?', (letter_id,))
        self.assertEqual(after['org_id'], org)
        self.assertEqual(after['contact_id'], contact)
        self.assertEqual(after['snapshot_full_name'], 'Письменов Письм')
        self.assertEqual(after['snapshot_position'], 'Секретарь')
        tag = self._query_one(
            'SELECT t.name FROM letter_tags t JOIN letter_tag_links l '
            'ON l.tag_id = t.id WHERE l.letter_id = ?', (letter_id,))
        self.assertEqual(tag['name'], 'важно')

    def test_legacy_letter_fallback(self):
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO counterparties (name, created_at) VALUES (?, ?)",
                ('ООО Старый контрагент', '2025-01-01'))
            cp_id = conn.execute('SELECT id FROM counterparties').fetchone()['id']
            cur = conn.execute(
                "INSERT INTO letters (date, number, subject, created_by, "
                "created_at, direction, counterparty_id) "
                "VALUES ('2025-02-02', 'Исх.1', 'Старое письмо', 2, "
                "'2025-02-02T10:00', 'out', ?)", (cp_id,))
            letter_id = cur.lastrowid
            conn.commit()
        finally:
            conn.close()

        self._login(2, 'employee')
        data = self.client.get(f'/letters/{letter_id}/edit').get_json()
        self.assertEqual(data['counterparty_name'], 'ООО Старый контрагент')
        self.assertEqual(data['counterparty_id'], cp_id)
        self.assertEqual(data['recipient_name'], '')

        rows = self.client.get('/letters/api/list').get_json()
        row = next(r for r in rows if r['id'] == letter_id)
        self.assertEqual(row['counterparty_name'], 'ООО Старый контрагент')

    def test_invalid_recipient_id_rejected(self):
        org, contact = self._seed_recipient()
        self._login(2, 'employee')

        # Создание с некорректным ID не создаёт письмо молча.
        resp = self.client.post('/letters/create', data={
            'date': '2026-01-01', 'subject': 'Плохой ID',
            'direction': 'out', 'tags': '', 'org_id': 'abc',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(
            self._query_one("SELECT COUNT(*) AS c FROM letters "
                            "WHERE subject = 'Плохой ID'")['c'], 0)

        # Редактирование с некорректным ID не меняет письмо.
        self._make_letter(user_id=2, org=org, contact=contact,
                          subject='Не трогать')
        letter = self._query_one(
            "SELECT * FROM letters WHERE subject = 'Не трогать'")
        resp = self.client.post(f"/letters/{letter['id']}/edit", data={
            'date': '2026-01-01', 'subject': 'Не трогать',
            'direction': 'out', 'tags': '', 'counterparty_id': '',
            'org_id': 'xyz', 'recipient_update': '1',
        })
        self.assertEqual(resp.status_code, 302)
        after = self._query_one(
            "SELECT * FROM letters WHERE subject = 'Не трогать'")
        self.assertEqual(after['org_id'], org)
        self.assertEqual(after['contact_id'], contact)


class ExcelFlowTest(DirectoryTestBase):
    """Двухлистовый Excel: кругооборот, история занятости, откат пакета."""

    def _seed(self):
        org1 = self._make_org('ООО Первая')
        org2 = self._make_org('ООО Вторая')
        conn = self._conn()
        try:
            created = contact_directory.create_contact(
                conn, 'Сидоров Сидр', org_id=org1, position='Старшая',
                user_id=2)
            conn.commit()
            return org1, org2, created['contact_id']
        finally:
            conn.close()

    def _org_row(self, row, **kw):
        base = {'action': 'auto', 'id': None, 'name': '', 'address': '',
                'inn': '', 'row': row}
        base.update(kw)
        return base

    def _contact_row(self, row, **kw):
        base = {'action': 'auto', 'id': None, 'org_id': None, 'org_name': '',
                'full_name': '', 'position': '', 'room': '', 'phone_work': '',
                'phone_ext': '', 'phone_personal': '', 'email': '', 'inn': '',
                'notes': '', 'row': row}
        base.update(kw)
        return base

    def test_excel_roundtrip_zero_changes(self):
        self._seed()

        from services.phonebook_excel import (
            export_phonebook_workbook, parse_phonebook_workbook)
        conn = self._conn()
        try:
            buf = export_phonebook_workbook(conn)
            parsed = parse_phonebook_workbook(buf)
            self.assertEqual(parsed['errors'], [])

            from services.phonebook_excel_plan import build_phonebook_plan
            plan = build_phonebook_plan(conn, parsed)
            self.assertTrue(plan['can_apply'])
            for kind in ('organizations', 'contacts'):
                summary = plan['summary'][kind]
                self.assertEqual(summary.get('create', 0), 0)
                self.assertEqual(summary.get('update', 0), 0)
                self.assertEqual(summary.get('delete', 0), 0)
                for item in plan[kind]:
                    self.assertEqual(item['action'], 'unchanged')
        finally:
            conn.close()

    def test_excel_job_change_preserves_history(self):
        from core.activity_log import log_action
        from services.phonebook_excel_apply import apply_phonebook_plan
        from services.phonebook_excel_plan import build_phonebook_plan
        org1, org2, contact_id = self._seed()

        parsed = {
            'organizations': [
                self._org_row(2, id=org1, name='ООО Первая'),
                self._org_row(3, id=org2, name='ООО Вторая'),
            ],
            'contacts': [
                self._contact_row(2, id=contact_id, org_id=org2,
                                  org_name='ООО Вторая',
                                  full_name='Сидоров Сидр',
                                  position='Новая должность'),
            ],
            'errors': [], 'warnings': [],
        }
        conn = self._conn()
        try:
            plan = build_phonebook_plan(conn, parsed)
            self.assertTrue(plan['can_apply'])
            item = plan['contacts'][0]
            self.assertEqual(item['action'], 'update')
            self.assertIn('org_id', item['changes'])
            self.assertIn('position', item['changes'])

            counts = apply_phonebook_plan(
                conn, parsed, plan, 2, log_action)
            self.assertEqual(counts['contacts']['update'], 1)

            links = conn.execute(
                'SELECT * FROM phonebook_employment WHERE contact_id = ?',
                (contact_id,)).fetchall()
            self.assertEqual(len(links), 2)
            self.assertIsNone(links[0]['end_date'])

            row = conn.execute(
                'SELECT org_id, position FROM phonebook WHERE id = ?',
                (contact_id,)).fetchone()
            self.assertEqual(row['org_id'], org2)
            self.assertEqual(row['position'], 'Новая должность')
        finally:
            conn.close()

    def test_excel_delete_refused_full_rollback(self):
        from core.activity_log import log_action
        from services.phonebook_excel_apply import (
            PhonebookApplyError, apply_phonebook_plan)
        from services.phonebook_excel_plan import build_phonebook_plan
        org, _org2, contact_id = self._seed()

        # Письмо ссылается на контакт: удаление запрещено правилами.
        conn = self._conn()
        try:
            snapshot = contact_directory.letter_snapshot(
                conn, org_id=org, contact_id=contact_id)
            conn.execute(
                "INSERT INTO letters (date, number, subject, created_by, "
                "created_at, direction, org_id, contact_id, "
                "snapshot_org_name, snapshot_full_name) "
                "VALUES ('2026-01-05', 'Исх.9', 'Связка', 2, "
                "'2026-01-05T09:00', 'out', ?, ?, 'ООО Первая', "
                "'Сидоров Сидр')",
                (snapshot['org_id'], snapshot['contact_id']))
            conn.commit()
        finally:
            conn.close()

        parsed = {
            'organizations': [
                self._org_row(2, name='ООО Новая из пакета'),
            ],
            'contacts': [
                self._contact_row(2, action='delete', id=contact_id,
                                 full_name='Сидоров Сидр'),
            ],
            'errors': [], 'warnings': [],
        }
        conn = self._conn()
        try:
            plan = build_phonebook_plan(conn, parsed)
            self.assertTrue(plan['can_apply'])

            with self.assertRaises(PhonebookApplyError):
                apply_phonebook_plan(conn, parsed, plan, 2, log_action)
        finally:
            conn.close()

        # Пакет не применён целиком: ни новый орг, ни удаление контакта.
        self.assertEqual(
            self._query_one("SELECT COUNT(*) AS c FROM phonebook_orgs "
                            "WHERE name = 'ООО Новая из пакета'")['c'], 0)
        self.assertIsNotNone(
            self._query_one('SELECT id FROM phonebook WHERE id = ?',
                            (contact_id,)))

    def test_old_one_sheet_import_via_service(self):
        import routes.phonebook_import as pbi
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        pbi.IMPORT_TEMP_DIR = tmp.name

        rows = [{
            'org_name': 'ООО Импорт', 'full_name': 'Импортов Имп',
            'position': 'Специалист', 'room': '5', 'phone_work': '',
            'phone_ext': '', 'phone_personal': '', 'email': '', 'notes': '',
        }]
        token = pbi._save_import_session({
            'rows': rows, 'parse_errors': [], 'unknown_orgs': ['ООО Импорт'],
            'org_mapping': {'ООО Импорт': {
                'action': 'create', 'map_id': None, 'new_name': 'ООО Импорт'}},
        })
        self._login(1, 'admin')
        with self.client.session_transaction() as s:
            s['import_token'] = token

        resp = self.client.post('/phonebook/import/confirm',
                                data={'update_duplicates': ''})
        self.assertEqual(resp.status_code, 302)

        org = self._query_one(
            "SELECT * FROM phonebook_orgs WHERE name = 'ООО Импорт'")
        self.assertIsNotNone(org)
        contact = self._query_one(
            "SELECT * FROM phonebook WHERE full_name = 'Импортов Имп'")
        self.assertIsNotNone(contact)
        self.assertEqual(contact['org_id'], org['id'])
        link = self._query_one(
            'SELECT * FROM phonebook_employment WHERE contact_id = ?',
            (contact['id'],))
        self.assertIsNotNone(link)
        self.assertEqual(link['org_id'], org['id'])
        self.assertEqual(link['position'], 'Специалист')


if __name__ == '__main__':
    unittest.main()
