# -*- coding: utf-8 -*-
"""Тесты ФИО подписанта (contracts/person_names.py)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from contracts import person_names


class GenitiveFioTests(unittest.TestCase):

    def test_male_three_words(self):
        self.assertEqual(
            person_names.genitive_fio('Миронов Дмитрий Васильевич'),
            'Миронова Дмитрия Васильевича'
        )
        self.assertEqual(
            person_names.genitive_fio('Петров Петр Петрович'),
            'Петрова Петра Петровича'
        )

    def test_female_three_words(self):
        self.assertEqual(
            person_names.genitive_fio('Иванова Мария Петровна'),
            'Ивановой Марии Петровны'
        )

    def test_two_words(self):
        self.assertEqual(
            person_names.genitive_fio('Миронов Дмитрий'),
            'Миронова Дмитрия'
        )

    def test_extra_spaces_are_normalized(self):
        self.assertEqual(
            person_names.genitive_fio('  Миронов   Дмитрий  Васильевич '),
            'Миронова Дмитрия Васильевича'
        )

    def test_unchanged_cases(self):
        cases = {
            'Миронов': 'Миронов',
            'Миронов Д.В.': 'Миронов Д.В.',
            'Алиев Тофик Али оглы': 'Алиев Тофик Али оглы',
            '': '',
            '   ': '',
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(person_names.genitive_fio(raw), expected)
        self.assertEqual(person_names.genitive_fio(None), '')

    def test_falls_back_without_library(self):
        with mock.patch.dict(sys.modules, {'petrovich': None}):
            self.assertEqual(
                person_names.genitive_fio('Миронов Дмитрий Васильевич'),
                'Миронов Дмитрий Васильевич'
            )


class SignatureNameTests(unittest.TestCase):

    def test_full_name_to_initials(self):
        self.assertEqual(
            person_names.signature_name('Миронов Дмитрий Васильевич'),
            'Д.В.Миронов'
        )

    def test_two_words(self):
        self.assertEqual(person_names.signature_name('Миронов Дмитрий'),
                         'Д.Миронов')

    def test_one_word_and_empty(self):
        self.assertEqual(person_names.signature_name('Миронов'), 'Миронов')
        self.assertEqual(person_names.signature_name(''), '')
        self.assertEqual(person_names.signature_name(None), '')

    def test_existing_initials_are_kept(self):
        self.assertEqual(person_names.signature_name('Миронов Д.В.'),
                         'Д.В.Миронов')
        self.assertEqual(person_names.signature_name('Миронов д.в.'),
                         'Д.В.Миронов')

    def test_lowercase_and_spaces(self):
        self.assertEqual(
            person_names.signature_name('  Миронов   дмитрий  васильевич '),
            'Д.В.Миронов'
        )

    def test_only_two_words_after_surname(self):
        self.assertEqual(
            person_names.signature_name('Алиев Тофик Али оглы'),
            'Т.А.Алиев'
        )


if __name__ == '__main__':
    unittest.main()
