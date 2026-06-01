# ╔══════════════════════════════════════════════════════════════╗
# ║                       egrul_api.py                           ║
# ║  Публичный API для фронта: поиск юрлица по ИНН через         ║
# ║  бесплатный сервис egrul.org (без токена, 100 req/сутки).    ║
# ║                                                              ║
# ║  Маршрут:                                                    ║
# ║    GET /api/egrul/lookup?inn=<ИНН>                           ║
# ║                                                              ║
# ║  Возвращает JSON:                                            ║
# ║    {                                                         ║
# ║      "full_name":   "ООО «Ромашка»",                        ║
# ║      "short_name":  "ООО «Ромашка»",                        ║
# ║      "inn":         "7709356120",                            ║
# ║      "ogrn":        "1027700132195",                         ║
# ║      "kpp":         "770901001",                             ║
# ║      "legal_address": "123456, г. Москва, ул. ...",          ║
# ║      "director":    "Иванов Иван Иванович",                  ║
# ║      "okved":       "10.11",                                 ║
# ║      "status":      "active"                                 ║
# ║    }                                                         ║
# ║  Или {"error": "<причина>"} при неудаче.                     ║
# ╚══════════════════════════════════════════════════════════════╝

import re
import requests as http

from flask import Blueprint, request, jsonify, session

from auth_utils import login_required
from activity_log import log_action

# ─── НАСТРОЙКИ ───────────────────────────────────────────────────────────────

EGRUL_URL    = "https://egrul.org/{inn}.json"
REQUEST_TIMEOUT = 6   # секунд

egrul_api_bp = Blueprint('egrul_api', __name__, url_prefix='/api/egrul')


# ─── ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ─────────────────────────────────────────────────

def _validate_inn(inn: str) -> str | None:
    """Возвращает очищенный ИНН или None если формат неверный."""
    inn = inn.strip()
    if not re.fullmatch(r'\d{10}|\d{12}', inn):
        return None
    return inn


def _parse_egrul(data: dict) -> dict:
    """Извлекает нужные поля из ответа egrul.org."""

    def _get(*keys):
        """Безопасное вложенное получение значения."""
        obj = data
        for k in keys:
            if not isinstance(obj, dict):
                return ''
            obj = obj.get(k) or ''
        return str(obj).strip() if obj else ''

    full_name  = _get('ЮЛ', 'НаимЮЛПолн') or _get('ИП', 'ФИОПолн') or ''
    short_name = _get('ЮЛ', 'НаимЮЛСокр') or full_name
    inn        = _get('ЮЛ', 'ИННЮЛ')      or _get('ИП', 'ИННФЛ') or ''
    ogrn       = _get('ЮЛ', 'ОГРН')       or _get('ИП', 'ОГРНИП') or ''
    kpp        = _get('ЮЛ', 'КПП') or ''
    director   = _get('ЮЛ', 'РуководительФИО') or ''
    okved      = _get('ОснОКВЭД', 'КодОКВЭД') or ''
    status_raw = _get('СведСтатусЮЛ', 'НаимСтатусЮЛ') or ''
    status     = 'active' if 'действующ' in status_raw.lower() else (status_raw or 'unknown')

    # Адрес: пробуем несколько путей — egrul.org возвращает разные структуры
    addr = (
        _get('АдрЮЛЛокГАР', 'АдресПолн')
        or _get('СвАдрЮЛ', 'АдресПолн')
        or _get('АдрЮЛЛокГАР', 'АдресРФ')
        or _get('СвАдрЮЛ', 'АдресРФ')
        or ''
    )

    return {
        'full_name':     full_name,
        'short_name':    short_name,
        'inn':           inn,
        'ogrn':          ogrn,
        'kpp':           kpp,
        'legal_address': addr,
        'director':      director,
        'okved':         okved,
        'status':        status,
    }


# ─── МАРШРУТ ─────────────────────────────────────────────────────────────────

@egrul_api_bp.route('/lookup')
@login_required
def egrul_lookup():
    """
    Поиск юридического лица / ИП по ИНН через egrul.org.

    Query-параметры:
      inn  — ИНН (10 цифр для юрлица, 12 для ИП). Обязателен.

    Возвращает JSON с реквизитами или {"error": "..."}.
    """
    raw_inn = request.args.get('inn', '').strip()
    inn = _validate_inn(raw_inn)

    if not inn:
        return jsonify({'error': 'Некорректный ИНН — ожидается 10 или 12 цифр'}), 400

    try:
        resp = http.get(
            EGRUL_URL.format(inn=inn),
            timeout=REQUEST_TIMEOUT,
            headers={'User-Agent': 'SONAR/2.4 (+internal)'},
        )
    except http.exceptions.Timeout:
        log_action('egrul_lookup_error', detail=f'inn={inn} timeout')
        return jsonify({'error': 'Сервис egrul.org не ответил за отведённое время'}), 504
    except http.exceptions.RequestException as exc:
        log_action('egrul_lookup_error', detail=f'inn={inn} {exc}')
        return jsonify({'error': 'Ошибка подключения к egrul.org'}), 502

    if resp.status_code == 404:
        log_action('egrul_lookup_not_found', detail=f'inn={inn}')
        return jsonify({'error': 'Организация с таким ИНН не найдена'}), 404

    if resp.status_code != 200:
        log_action('egrul_lookup_error', detail=f'inn={inn} status={resp.status_code}')
        return jsonify({'error': f'egrul.org вернул ошибку {resp.status_code}'}), 502

    try:
        data = resp.json()
    except ValueError:
        log_action('egrul_lookup_error', detail=f'inn={inn} bad json')
        return jsonify({'error': 'Не удалось разобрать ответ от egrul.org'}), 502

    result = _parse_egrul(data)

    if not result.get('full_name'):
        log_action('egrul_lookup_empty', detail=f'inn={inn}')
        return jsonify({'error': 'Данные по ИНН не найдены'}), 404

    log_action('egrul_lookup', detail=f'inn={inn} name={result["full_name"]!r}')
    return jsonify(result)
