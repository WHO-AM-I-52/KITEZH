# ╔══════════════════════════════════════════════════════════════╗
# ║                       egrul_api.py                           ║
# ║  Публичный API для фронта: поиск юрлица по ИНН через         ║
# ║  бесплатный сервис egrul.org (без токена, 100 req/сутки).    ║
# ║                                                              ║
# ║  Маршрут:                                                    ║
# ║    GET /api/egrul/lookup?inn=<ИНН>                           ║
# ║                                                              ║
# ║  Возвращает JSON:                                          ║
# ║    {"ok": true, "data": {                                     ║
# ║      "applicant_full_name":  "ООО «Ромашка»",              ║
# ║      "applicant_short_name": "ООО «ромашка»",              ║
# ║      "legal_address":       "123456, г. Москва, ...",      ║
# ║      "applicant_okved_main": "10.11"                          ║
# ║    }}                                                         ║
# ║  Или {"ok": false, "error": "<причина>"} при неудаче.         ║
# ╚══════════════════════════════════════════════════════════════╝

import re
import requests as http

from flask import Blueprint, request, jsonify, session

from db import get_db
from auth_utils import login_required
from activity_log import log_action

# ─── НАСТРОЙКИ ──────────────────────────────────────────────────────────────

EGRUL_URL       = 'https://egrul.org/{inn}.json'
REQUEST_TIMEOUT = 6

egrul_api_bp = Blueprint('egrul_api', __name__, url_prefix='/api/egrul')


# ─── ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ───────────────────────────────────────────────

def _validate_inn(inn: str) -> str | None:
    """Returns cleaned INN or None if format is invalid."""
    inn = inn.strip()
    if not re.fullmatch(r'\d{10}|\d{12}', inn):
        return None
    return inn


def _parse_egrul(data: dict) -> dict:
    """
    Извлекает нужные поля из ответа egrul.org.
    Поддерживает два формата:
      - вложенные русскоязычные ключи (структура ФНС XML)
      - плоский JSON (egrul.org REST)
    """

    def _get(*keys):
        obj = data
        for k in keys:
            if not isinstance(obj, dict):
                return ''
            obj = obj.get(k) or ''
        return str(obj).strip() if obj else ''

    # Наименование: сначала вложенные ключи ФНС, затем плоские egrul.org
    full_name = (
        _get('ЮЛ', 'НаимЮЛПолн') or _get('ИП', 'ФИОПолн')
        or data.get('full_name') or data.get('name') or ''
    )
    short_name = (
        _get('ЮЛ', 'НаимЮЛСокр') or data.get('short_name') or full_name
    )
    inn_val = (
        _get('ЮЛ', 'ИННЮЛ') or _get('ИП', 'ИННФЛ')
        or data.get('inn') or ''
    )
    ogrn = (
        _get('ЮЛ', 'ОГРН') or _get('ИП', 'ОГРНИП')
        or data.get('ogrn') or ''
    )
    kpp = _get('ЮЛ', 'КПП') or data.get('kpp') or ''
    director = _get('ЮЛ', 'РуководителЬФИО') or data.get('director') or ''

    # ОКВЭД: может быть строкой или {"code": "..."}
    okved_raw = (
        _get('ОснОКВЭД', 'КодОКВЭД')
        or data.get('okved') or data.get('okved_main') or ''
    )
    if isinstance(okved_raw, dict):
        okved_raw = okved_raw.get('code', '')
    okved = str(okved_raw).strip()

    # Адрес: несколько вариантов структуры
    addr = (
        _get('АдрЮЛЛокГАР', 'АдресПолн')
        or _get('СвАдрЮЛ', 'АдресПолн')
        or _get('АдрЮЛЛокГАР', 'АдресРФ')
        or _get('СвАдрЮЛ', 'АдресРФ')
        or data.get('address') or data.get('legal_address')
        or (data.get('address_details') or {}).get('full_address', '')
        or ''
    )

    status_raw = _get('СведСтатусЮЛ', 'НаимСтатусЮЛ') or data.get('status') or ''
    status = 'active' if 'действующ' in status_raw.lower() else (status_raw or 'unknown')

    return {
        'applicant_full_name':  full_name,
        'applicant_short_name': short_name,
        'inn':                  inn_val,
        'ogrn':                 ogrn,
        'kpp':                  kpp,
        'legal_address':        addr,
        'director':             director,
        'applicant_okved_main': okved,
        'status':               status,
    }


def _log(action: str, inn: str, detail: str = ''):
    """Helper: логирует действие с правильной сигнатурой log_action(conn, user_id, action, detail)."""
    try:
        conn = get_db()
        log_action(
            conn,
            user_id=session.get('user_id'),
            action=action,
            detail=f'inn={inn}{" " + detail if detail else ""}',
        )
        conn.commit()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ─── МАРШРУТ ───────────────────────────────────────────────────────────────────

@egrul_api_bp.route('/lookup')
@login_required
def egrul_lookup():
    """
    Поиск юридического лица / ИП по ИНН через egrul.org.

    Query-параметры:
      inn — ИНН (10 цифр для юрлица, 12 для ИП). Обязателен.

    Возвращает JSON:
      {"ok": true,  "data": { <реквизиты> }}
      {"ok": false, "error": "..."}  при ошибке
    """
    raw_inn = request.args.get('inn', '').strip()
    inn = _validate_inn(raw_inn)

    if not inn:
        return jsonify(ok=False, error='Некорректный ИНН — ожидается 10 или 12 цифр'), 400

    try:
        resp = http.get(
            EGRUL_URL.format(inn=inn),
            timeout=REQUEST_TIMEOUT,
            headers={'User-Agent': 'SONAR/2.4 (+internal)'},
        )
    except http.exceptions.Timeout:
        _log('egrul_lookup_error', inn, 'timeout')
        return jsonify(ok=False, error='Сервис egrul.org не ответил за отведённое время'), 504
    except http.exceptions.RequestException as exc:
        _log('egrul_lookup_error', inn, str(exc))
        return jsonify(ok=False, error='Ошибка подключения к egrul.org'), 502

    if resp.status_code == 404:
        _log('egrul_lookup_not_found', inn)
        return jsonify(ok=False, error='Организация с таким ИНН не найдена'), 404

    if resp.status_code != 200:
        _log('egrul_lookup_error', inn, f'status={resp.status_code}')
        return jsonify(ok=False, error=f'egrul.org вернул ошибку {resp.status_code}'), 502

    try:
        raw_data = resp.json()
    except ValueError:
        _log('egrul_lookup_error', inn, 'bad json')
        return jsonify(ok=False, error='Не удалось разобрать ответ от egrul.org'), 502

    result = _parse_egrul(raw_data)

    if not result.get('applicant_full_name'):
        _log('egrul_lookup_empty', inn)
        return jsonify(ok=False, error='Данные по ИНН не найдены'), 404

    _log('egrul_lookup', inn, f'name={result["applicant_full_name"]!r}')
    return jsonify(ok=True, data=result)
