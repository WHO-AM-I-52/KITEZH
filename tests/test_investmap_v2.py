import io
import unittest
from unittest.mock import patch

from flask import Flask

from routes import investmap_routes
from tools.investmap_analyzer import (
    V2_SUMMARY_SMS_MAX_LENGTH,
    build_summary_sms,
    build_v2_summary_sms,
)


class BuildV2SummarySmsTest(unittest.TestCase):
    def test_batch_summary_contains_counts_score_field_and_hint(self):
        results = [
            {
                'score': 80,
                'missing': [
                    {
                        'field': 'Форма собственности объекта',
                        'hint': 'Выберите форму собственности.',
                    },
                ],
            },
            {
                'score': 60,
                'missing': [
                    {
                        'field': 'Форма сделки',
                        'hint': 'Укажите форму сделки.',
                    },
                ],
            },
        ]
        source_rows = [
            {'global_id': '1001'},
            {'Название площадки': 'Индустриальный парк Тест'},
        ]

        summary = build_v2_summary_sms(results, source_rows)

        self.assertIsNotNone(summary)
        self.assertIn('Проверено площадок: 2.', summary)
        self.assertIn('Средний score: 70.0%.', summary)
        self.assertIn('ID 1001', summary)
        self.assertIn('Индустриальный парк Тест', summary)
        self.assertIn('Форма собственности объекта', summary)
        self.assertIn('Выберите форму собственности.', summary)

    def test_summary_uses_fallback_without_global_id(self):
        results = [
            {
                'score': 55,
                'missing': [
                    {
                        'field': 'Геопривязка',
                        'hint': 'Укажите координаты.',
                    },
                ],
            },
        ]

        summary = build_v2_summary_sms(results, [{}])

        self.assertIsNotNone(summary)
        self.assertIn('Площадка 1', summary)
        self.assertIn('Геопривязка', summary)

    def test_summary_without_missing_is_positive(self):
        results = [
            {'score': 100, 'missing': []},
            {'score': 90, 'missing': []},
        ]
        source_rows = [
            {'global_id': '2001'},
            {'global_id': '2002'},
        ]

        summary = build_v2_summary_sms(results, source_rows)

        self.assertIsNotNone(summary)
        self.assertIn('Проверено площадок: 2.', summary)
        self.assertIn('Средний score: 95.0%.', summary)
        self.assertIn('Площадок с незаполненными полями: 0.', summary)
        self.assertIn(
            'Все проверенные площадки заполнены: '
            'незаполненных полей не обнаружено.',
            summary,
        )

    def test_summary_limits_sites_and_fields(self):
        results = []
        source_rows = []

        for site_number in range(1, 26):
            results.append({
                'score': 50,
                'missing': [
                    {
                        'field': f'Поле {field_number}',
                        'hint': f'Подсказка {field_number}',
                    }
                    for field_number in range(1, 6)
                ],
            })
            source_rows.append({'global_id': str(site_number)})

        summary = build_v2_summary_sms(results, source_rows)

        self.assertIn('Площадок с незаполненными полями: 25.', summary)
        self.assertIn('Всего незаполненных полей: 125.', summary)
        self.assertIn('20. ID 20 — score: 50.0%.', summary)
        self.assertNotIn('21. ID 21 — score: 50.0%.', summary)
        self.assertEqual(summary.count('   • '), 60)
        self.assertNotIn('Поле 4 — Подсказка 4.', summary)

    def test_summary_does_not_exceed_4000_characters(self):
        results = []
        source_rows = []

        for site_number in range(1, 21):
            results.append({
                'score': 50,
                'missing': [
                    {
                        'field': f'Поле {field_number} ' + ('Ф' * 250),
                        'hint': f'Подсказка {field_number} ' + ('П' * 250),
                    }
                    for field_number in range(1, 4)
                ],
            })
            source_rows.append({'global_id': str(site_number)})

        summary = build_v2_summary_sms(results, source_rows)

        self.assertLessEqual(len(summary), V2_SUMMARY_SMS_MAX_LENGTH)
        self.assertIn('Список сокращён до лимита 4000 символов.', summary)

    def test_legacy_build_summary_sms_keeps_legacy_format(self):
        legacy_results = [
            {
                'sms': 'legacy marker',
                'id': 'LEGACY-1',
                'total': 80,
                'category': '🟡 Требует доработки',
                'missing_portal': ['Стоимость объекта'],
                'typo_warnings': [],
            },
        ]

        summary = build_summary_sms(legacy_results)

        self.assertIsNotNone(summary)
        self.assertIn(
            'Направляем сводный отчёт по заполняемости инвестплощадок '
            'на портале invest.gov.ru.',
            summary,
        )
        self.assertIn('▸ «LEGACY-1» — 80% (🟡 Требует доработки)', summary)
        self.assertIn(
            '1. Стоимость объекта — '
            'укажите стоимость аренды или покупки в рублях.',
            summary,
        )


class InvestmapV2RouteTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY='investmap-v2-test-secret',
        )
        self.app.register_blueprint(investmap_routes.investmap_bp)

        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = 1
            session['role'] = 'admin'

    def _post_v2(self, export_result, score_results):
        with (
            patch.object(
                investmap_routes,
                'convert_excel_to_text',
                return_value=export_result,
            ),
            patch.object(
                investmap_routes,
                'calc_portal_score_v2',
                side_effect=score_results,
            ),
            patch.object(
                investmap_routes,
                'get_db',
                return_value=object(),
            ),
            patch.object(investmap_routes, 'log_action'),
        ):
            return self.client.post(
                '/investmap/v2',
                data={
                    'file': (
                        io.BytesIO(b'test-xlsx-content'),
                        'investmap.xlsx',
                    ),
                },
                content_type='multipart/form-data',
            )

    def test_format_2_has_texts_without_text(self):
        export_result = {
            'format': 2,
            'count': 2,
            'data': [
                {'global_id': '3001'},
                {'Название площадки': 'Площадка без ID'},
            ],
            'text': 'Этот единый текст не должен попасть в V2 batch export.',
            'texts': ['Текст площадки 1', 'Текст площадки 2'],
        }
        score_results = [
            {
                'score': 80,
                'missing': [
                    {
                        'field': 'Геопривязка',
                        'hint': 'Укажите координаты.',
                    },
                ],
            },
            {
                'score': 100,
                'missing': [],
            },
        ]

        response = self._post_v2(export_result, score_results)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertEqual(payload['count'], 2)
        self.assertIsNotNone(payload['summary_sms'])
        self.assertEqual(payload['export']['format'], 2)
        self.assertEqual(
            payload['export']['texts'],
            ['Текст площадки 1', 'Текст площадки 2'],
        )
        self.assertNotIn('text', payload['export'])

    def test_format_1_and_3_have_text_and_single_item_texts(self):
        for export_format in (1, 3):
            with self.subTest(export_format=export_format):
                export_result = {
                    'format': export_format,
                    'count': 1,
                    'data': {'global_id': '4001'},
                    'text': f'Текст формата {export_format}',
                    'texts': ['Старое значение не должно использоваться'],
                }
                score_results = [
                    {
                        'score': 80,
                        'missing': [
                            {
                                'field': 'Геопривязка',
                                'hint': 'Укажите координаты.',
                            },
                        ],
                    },
                ]

                response = self._post_v2(export_result, score_results)

                self.assertEqual(response.status_code, 200)
                payload = response.get_json()

                self.assertIsNone(payload['summary_sms'])
                self.assertEqual(payload['export']['format'], export_format)
                self.assertEqual(
                    payload['export']['text'],
                    f'Текст формата {export_format}',
                )
                self.assertEqual(
                    payload['export']['texts'],
                    [f'Текст формата {export_format}'],
                )

    def test_format_2_with_one_site_has_no_summary(self):
        export_result = {
            'format': 2,
            'count': 1,
            'data': [{'global_id': '5001'}],
            'texts': ['Текст площадки 5001'],
        }
        score_results = [
            {
                'score': 80,
                'missing': [
                    {
                        'field': 'Геопривязка',
                        'hint': 'Укажите координаты.',
                    },
                ],
            },
        ]

        response = self._post_v2(export_result, score_results)

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()

        self.assertIsNone(payload['summary_sms'])
        self.assertEqual(payload['export']['texts'], ['Текст площадки 5001'])
        self.assertNotIn('text', payload['export'])


if __name__ == '__main__':
    unittest.main()