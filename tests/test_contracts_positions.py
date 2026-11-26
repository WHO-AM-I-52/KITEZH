# -*- coding: utf-8 -*-
"""Тесты справочника должностей подписанта (contracts/positions.py)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contracts import positions


class SignaturePositionTests(unittest.TestCase):

    def test_known_position_to_nominative(self):
        self.assertEqual(
            positions.signature_position('генерального директора'),
            'Генеральный директор'
        )
        self.assertEqual(
            positions.signature_position(
                'заместителя генерального директора'),
            'Заместитель генерального директора'
        )
        self.assertEqual(
            positions.signature_position('индивидуального предпринимателя'),
            'Индивидуальный предприниматель'
        )

    def test_ignores_case_and_extra_spaces(self):
        for raw in ('  Генерального   ДИРЕКТОРА ',
                    'ГЕНЕРАЛЬНОГО ДИРЕКТОРА',
                    'генерального\tдиректора'):
            with self.subTest(raw=raw):
                self.assertEqual(positions.signature_position(raw),
                                 'Генеральный директор')

    def test_unknown_position_is_capitalized_as_is(self):
        self.assertEqual(
            positions.signature_position('начальника отдела'),
            'Начальника отдела'
        )
        # остальной текст не меняется, лишние пробелы убираются
        self.assertEqual(
            positions.signature_position(' начальника   ОТО '),
            'Начальника ОТО'
        )

    def test_empty_values(self):
        for raw in (None, '', '   ', '\n'):
            with self.subTest(raw=raw):
                self.assertEqual(positions.signature_position(raw), '')

    def test_is_known_position(self):
        self.assertTrue(positions.is_known_position('директора'))
        self.assertTrue(positions.is_known_position(' ДИРЕКТОРА '))
        self.assertFalse(positions.is_known_position('начальника отдела'))
        self.assertFalse(positions.is_known_position(None))
        self.assertFalse(positions.is_known_position(''))


class PositionsDictionaryTests(unittest.TestCase):
    """Защита от ошибок при ручной правке словаря POSITIONS."""

    def test_keys_are_normalized(self):
        for key in positions.POSITIONS:
            with self.subTest(key=key):
                self.assertEqual(key, ' '.join(key.split()).casefold())

    def test_values_start_with_capital(self):
        for key, value in positions.POSITIONS.items():
            with self.subTest(key=key):
                self.assertTrue(value)
                self.assertEqual(value, value.strip())
                self.assertTrue(value[0].isupper())

    def test_every_key_resolves_to_its_value(self):
        for key, value in positions.POSITIONS.items():
            with self.subTest(key=key):
                self.assertEqual(positions.signature_position(key), value)


if __name__ == '__main__':
    unittest.main()
