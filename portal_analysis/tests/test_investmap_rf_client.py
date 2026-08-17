import json
import socket
import unittest
import urllib.error
from unittest.mock import patch

from services.investmap_rf_client import (
    InvestmapRfAccessError,
    InvestmapRfClientError,
    InvestmapRfRateLimitedError,
    fetch_card,
)


class _Response:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self._body


class InvestmapRfClientTest(unittest.TestCase):
    def test_fetch_card_returns_validated_payload(self):
        payload = {
            'id': 2461092,
            'fillingLevel': 91,
            'regionCode': 52,
            'regions': ['Нижегородская область'],
        }

        with patch(
            'services.investmap_rf_client.urllib.request.urlopen',
            return_value=_Response(200, json.dumps(payload).encode()),
        ) as urlopen:
            card = fetch_card(2461092)

        self.assertEqual(card.global_id, 2461092)
        self.assertEqual(card.filling_level, 91)
        self.assertEqual(card.region_code, 52)
        self.assertEqual(card.regions, ['Нижегородская область'])
        self.assertEqual(card.payload, payload)
        self.assertIn(
            'investmentPlatformId=2461092',
            urlopen.call_args.args[0].full_url,
        )
        self.assertNotIn('Cookie', urlopen.call_args.args[0].headers)

    def test_fetch_card_accepts_missing_filling_level(self):
        payload = {'id': 2461092, 'regions': 'invalid'}

        with patch(
            'services.investmap_rf_client.urllib.request.urlopen',
            return_value=_Response(200, json.dumps(payload).encode()),
        ):
            card = fetch_card(2461092)

        self.assertIsNone(card.filling_level)
        self.assertEqual(card.regions, [])

    def test_fetch_card_rejects_unexpected_card_id(self):
        payload = {'id': 1}

        with patch(
            'services.investmap_rf_client.urllib.request.urlopen',
            return_value=_Response(200, json.dumps(payload).encode()),
        ):
            with self.assertRaisesRegex(
                InvestmapRfClientError,
                'другим идентификатором',
            ):
                fetch_card(2461092)

    def test_fetch_card_rejects_invalid_json(self):
        with patch(
            'services.investmap_rf_client.urllib.request.urlopen',
            return_value=_Response(200, b'not-json'),
        ):
            with self.assertRaisesRegex(
                InvestmapRfClientError,
                'некорректный JSON',
            ):
                fetch_card(2461092)

    def test_fetch_card_maps_access_error(self):
        error = urllib.error.HTTPError(
            'https://example.test',
            403,
            'Forbidden',
            {},
            None,
        )

        with patch(
            'services.investmap_rf_client.urllib.request.urlopen',
            side_effect=error,
        ):
            with self.assertRaises(InvestmapRfAccessError):
                fetch_card(2461092)

    def test_fetch_card_maps_rate_limit_error(self):
        error = urllib.error.HTTPError(
            'https://example.test',
            429,
            'Too Many Requests',
            {},
            None,
        )

        with patch(
            'services.investmap_rf_client.urllib.request.urlopen',
            side_effect=error,
        ):
            with self.assertRaises(InvestmapRfRateLimitedError):
                fetch_card(2461092)

    def test_fetch_card_maps_network_error(self):
        with patch(
            'services.investmap_rf_client.urllib.request.urlopen',
            side_effect=urllib.error.URLError(socket.timeout()),
        ):
            with self.assertRaisesRegex(
                InvestmapRfClientError,
                'Не удалось подключиться',
            ):
                fetch_card(2461092)

    def test_fetch_card_rejects_invalid_global_id(self):
        with self.assertRaisesRegex(ValueError, 'положительным'):
            fetch_card(0)


if __name__ == '__main__':
    unittest.main()