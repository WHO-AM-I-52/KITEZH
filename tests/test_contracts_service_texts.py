# -*- coding: utf-8 -*-
"""Тесты формулировок услуг для п. 1.1 (contracts/service_texts.py)."""
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contracts import service_texts

T = service_texts.SERVICE_TEXTS
PACKAGE_ORDER = ['2.1', '2.2', '2.3', '2.4', '2.5', '3.1']


def row(code=None, name='', qty=None):
    data = {'name': name}
    if code is not None:
        data['code'] = code
    if qty is not None:
        data['qty'] = qty
    return data


def texts(items):
    return [text for text, _qty in items]


class PackageExpansionTests(unittest.TestCase):

    def test_package_content_order(self):
        self.assertEqual(list(service_texts.PACKAGE_CONTENT), PACKAGE_ORDER)
        for code in PACKAGE_ORDER:
            with self.subTest(code=code):
                self.assertTrue(T[code])

    def test_package_expands_into_six_items_by_codes(self):
        items = service_texts.contract_service_items([
            row('2.6', 'Полный пакет документов (2.1-2.5), справочная позиция', '1')
        ])
        self.assertEqual(texts(items), [T[c] for c in PACKAGE_ORDER])
        self.assertTrue(all(qty is None for _t, qty in items))

    def test_package_code_with_spaces(self):
        items = service_texts.contract_service_items([row(' 2.6 ', 'x')])
        self.assertEqual(len(items), 6)

    def test_package_does_not_duplicate_selected_items(self):
        after = service_texts.contract_service_items(
            [row('2.6', 'x'), row('2.3', 'y')])
        self.assertEqual(texts(after), [T[c] for c in PACKAGE_ORDER])
        before = service_texts.contract_service_items(
            [row('2.3', 'y'), row('2.6', 'x')])
        self.assertEqual(len(before), 6)
        self.assertEqual(texts(before)[0], T['2.3'])


class SingleItemTests(unittest.TestCase):

    def test_known_code_uses_full_text_without_qty(self):
        items = service_texts.contract_service_items(
            [row('2.5', 'Подготовка бизнес-плана', '1')])
        self.assertEqual(items, [('Подготовка бизнес-плана.', None)])

    def test_separate_items_keep_selection_order(self):
        items = service_texts.contract_service_items(
            [row('2.5', 'a'), row('2.2', 'b')])
        self.assertEqual(texts(items), [T['2.5'], T['2.2']])

    def test_unknown_code_keeps_name_and_qty(self):
        name = 'Подготовка комплексной градостроительной справки земельного участка'
        items = service_texts.contract_service_items(
            [row('1.1', name, '1,3333')])
        self.assertEqual(items, [(name, '1,3333')])

    def test_row_without_code_uses_name(self):
        items = service_texts.contract_service_items(
            [row(None, 'Подготовка бизнес-плана.')])
        self.assertEqual(items, [('Подготовка бизнес-плана.', None)])

    def test_empty_input_and_empty_name(self):
        self.assertEqual(service_texts.contract_service_items([]), [])
        self.assertEqual(
            service_texts.contract_service_items([row('9.9', '')]), [])

    def test_sqlite_row_is_supported(self):
        conn = sqlite3.connect(':memory:')
        self.addCleanup(conn.close)
        conn.row_factory = sqlite3.Row
        package = conn.execute(
            "SELECT '2.6' AS code, 'Полный пакет' AS name, '1' AS qty"
        ).fetchone()
        other = conn.execute(
            "SELECT '5.1' AS code, 'Информация' AS name, '2' AS qty"
        ).fetchone()
        items = service_texts.contract_service_items([package, other])
        self.assertEqual(texts(items)[:6], [T[c] for c in PACKAGE_ORDER])
        self.assertEqual(items[6], ('Информация', '2'))


class WordingTests(unittest.TestCase):
    """Ключевые фразы из текста договора не должны потеряться."""

    def test_council_wording(self):
        for code in ('2.1', '3.1'):
            with self.subTest(code=code):
                self.assertIn(
                    'по земельным и имущественным отношениям '
                    'при Правительстве Нижегородской области', T[code])

    def test_selected_phrases(self):
        self.assertIn('размещения объекта в масштабе 1:500', T['2.2'])
        self.assertIn('1:1000 или 1:2000', T['2.2'])
        self.assertIn('с планом размещения объекта и благоустройством', T['2.4'])
        self.assertIn('от 02.07.2018 № 151-од', T['3.1'])
        self.assertIn('ГИСОГД НО', T['3.1'])

    def test_no_broken_characters(self):
        for code, text in T.items():
            with self.subTest(code=code):
                self.assertNotIn('\ufffd', text)
                self.assertTrue(text.endswith('.'))


if __name__ == '__main__':
    unittest.main()
