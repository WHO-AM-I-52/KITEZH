"""Read-only клиент API Инвестиционной карты РФ."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


API_URL = (
    'https://investmapapi.economy.gov.ru/'
    'investrussia/map/v1/investmentplatform/getfullinfo'
)
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_RETRY_DELAYS_SECONDS = (1.0, 3.0, 8.0)
_RETRYABLE_HTTP_CODES = {500, 502, 503, 504}


class InvestmapRfClientError(RuntimeError):
    """Базовая ошибка read-only клиента Инвестиционной карты РФ."""


class InvestmapRfRateLimitedError(InvestmapRfClientError):
    """Внешний API ограничил частоту запросов."""


class InvestmapRfAccessError(InvestmapRfClientError):
    """Внешний API запретил доступ к карточке."""


class InvestmapRfNotFoundError(InvestmapRfClientError):
    """Внешний API не нашёл карточку."""


@dataclass(frozen=True)
class InvestmapRfCard:
    """Проверенный ответ API по одной инвестиционной площадке."""

    global_id: int
    filling_level: int | None
    region_code: int | None
    regions: list[str]
    payload: dict[str, Any]


def _build_url(global_id: int) -> str:
    try:
        normalized_id = int(global_id)
    except (TypeError, ValueError) as exc:
        raise ValueError('global_id должен быть целым числом.') from exc

    if normalized_id <= 0:
        raise ValueError('global_id должен быть положительным числом.')

    return f'{API_URL}?investmentPlatformId={normalized_id}'


def _parse_payload(raw_body: bytes, global_id: int) -> InvestmapRfCard:
    try:
        payload = json.loads(raw_body.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvestmapRfClientError(
            'Внешний API вернул некорректный JSON.'
        ) from exc

    if not isinstance(payload, dict):
        raise InvestmapRfClientError(
            'Внешний API вернул неожиданный формат карточки.'
        )

    response_id = payload.get('id')
    try:
        response_id = int(response_id)
    except (TypeError, ValueError) as exc:
        raise InvestmapRfClientError(
            'Внешний API не вернул корректный идентификатор карточки.'
        ) from exc

    if response_id != global_id:
        raise InvestmapRfClientError(
            'Внешний API вернул карточку с другим идентификатором.'
        )

    filling_level = payload.get('fillingLevel')
    if filling_level is not None:
        try:
            filling_level = int(filling_level)
        except (TypeError, ValueError) as exc:
            raise InvestmapRfClientError(
                'Внешний API вернул некорректный fillingLevel.'
            ) from exc

    region_code = payload.get('regionCode')
    if region_code is not None:
        try:
            region_code = int(region_code)
        except (TypeError, ValueError) as exc:
            raise InvestmapRfClientError(
                'Внешний API вернул некорректный код региона.'
            ) from exc

    regions = payload.get('regions', [])
    if not isinstance(regions, list):
        regions = []

    return InvestmapRfCard(
        global_id=response_id,
        filling_level=filling_level,
        region_code=region_code,
        regions=[str(item) for item in regions],
        payload=payload,
    )


def _error_details(exc: urllib.error.HTTPError) -> str:
    raw_body = exc.read()[:2000]
    body = raw_body.decode('utf-8', errors='replace').strip()
    request_id = (
        exc.headers.get('X-Request-ID')
        or exc.headers.get('Request-ID')
        or exc.headers.get('Trace-ID')
    )

    details: list[str] = []
    if request_id:
        details.append(f'request_id={request_id}')
    if body:
        details.append(f'ответ={body!r}')

    return f' ({", ".join(details)})' if details else ''


def _make_request(
    request: urllib.request.Request,
    *,
    timeout_seconds: int,
) -> tuple[int, bytes]:
    with urllib.request.urlopen(
        request,
        timeout=timeout_seconds,
    ) as response:
        return response.status, response.read()


def fetch_card(
    global_id: int,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    retry_delays_seconds: tuple[float, ...] = DEFAULT_RETRY_DELAYS_SECONDS,
) -> InvestmapRfCard:
    """Получает одну карточку с повторами временных ошибок API."""
    url = _build_url(global_id)
    normalized_id = int(global_id)
    request = urllib.request.Request(
        url,
        headers={
            'Accept': 'application/json',
            'User-Agent': 'KITEZH/1.0 read-only monitor',
        },
        method='GET',
    )

    attempts_count = len(retry_delays_seconds) + 1

    for attempt_number in range(attempts_count):
        try:
            status_code, raw_body = _make_request(
                request,
                timeout_seconds=timeout_seconds,
            )
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise InvestmapRfNotFoundError(
                    'Внешний API не нашёл карточку.'
                ) from exc
            if exc.code == 429:
                raise InvestmapRfRateLimitedError(
                    'Инвестиционная карта РФ ограничила частоту запросов.'
                ) from exc
            if exc.code in {401, 403}:
                raise InvestmapRfAccessError(
                    'Внешний API запретил доступ к карточке.'
                ) from exc

            can_retry = (
                exc.code in _RETRYABLE_HTTP_CODES
                and attempt_number < len(retry_delays_seconds)
            )
            if can_retry:
                time.sleep(retry_delays_seconds[attempt_number])
                continue

            raise InvestmapRfClientError(
                f'Внешний API вернул HTTP {exc.code} '
                f'после {attempt_number + 1} попыток.'
                f'{_error_details(exc)}'
            ) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            if attempt_number < len(retry_delays_seconds):
                time.sleep(retry_delays_seconds[attempt_number])
                continue

            raise InvestmapRfClientError(
                'Не удалось подключиться к API Инвестиционной карты РФ '
                f'после {attempt_number + 1} попыток: {exc}'
            ) from exc

        if status_code != 200:
            raise InvestmapRfClientError(
                f'Внешний API вернул HTTP {status_code}.'
            )

        return _parse_payload(raw_body, normalized_id)

    raise AssertionError('Непредвиденное завершение цикла повторных попыток.')
