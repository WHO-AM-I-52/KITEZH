# ╔══════════════════════════════════════════════════════════════╗
# ║                       egrul_api.py                           ║
# ║  Публичный API для фронта: поиск юрлица по ИНН через         ║
# ║  бесплатный сервис egrul.org (без токена, 100 req/сутки).    ║
# ║                                                              ║
# ║  Маршрут:                                                    ║
# ║    GET /api/egrul/lookup?inn=<ИНН>                           ║
# ║                                                              ║
# ║  Возвращает JSON:                                            ║
# ║    {"ok": true, "data": {                                    ║
# ║      "applicant_full_name":  "ООО «Ромашка»",               ║
# ║      "applicant_short_name": "ООО «Ромашка»",               ║
# ║      "legal_address":       "123456, г. Москва, ...",        ║
# ║      "applicant_okved_main": "10.11"                         ║
# ║    }}                                                        ║
# ║  Или {"ok": false, "error": "<причина>"} при неудаче.        ║
# ╚══════════════════════════════════════════════════════════════╝

import re
import requests as http

from flask import Blueprint, request, jsonify, session

from db import get_db
from core.auth_utils import login_required
from core.activity_log import log_action

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


def _build_address(addr_rf: dict) -> str:
    """Собирает строку адреса из блока АдресРФ."""
    if not isinstance(addr_rf, dict):
        return ''
    attrs  = addr_rf.get('@attributes', {})
    parts  = []
    index  = attrs.get('Индекс', '')
    if index:
        parts.append(index)
    region = (addr_rf.get('Регион') or {}).get('@attributes', {}).get('НаимРегион', '')
    if region:
        parts.append(region)
    city = (addr_rf.get('Город') or {}).get('@attributes', {})
    if city:
        parts.append(f"{city.get('ТипГород', 'г.')} {city.get('НаимГород', '')}".strip())
    street = (addr_rf.get('Улица') or {}).get('@attributes', {})
    if street:
        parts.append(f"{street.get('ТипУлица', '')} {street.get('НаимУлица', '')}".strip())
    for key in ('Дом', 'Корпус', 'Кварт'):
        val = attrs.get(key, '')
        if val:
            parts.append(val)
    return ', '.join(p for p in parts if p)


def _parse_egrul(data: dict) -> dict:
    """
    Извлекает нужные поля из ответа egrul.org.

    egrul.org отдаёт ФНС-структуру с ключом СвЮЛ (юрлицо)
    или СвИП (индивидуальный предприниматель).
    Все атрибуты XML лежат под ключом @attributes.
    """
    sv    = data.get('СвЮЛ') or {}
    attrs = sv.get('@attributes') or {}

    # ── Наименование ────────────────────────────────────────────
    sv_naim = sv.get('СвНаимЮЛ') or {}
    full_name = (
        (sv_naim.get('@attributes') or {}).get('НаимЮЛПолн', '')
        or attrs.get('НаимЮЛПолн', '')
    )
    sv_sokr = sv_naim.get('СвНаимЮЛСокр') or {}
    short_name = (
        (sv_sokr.get('@attributes') or {}).get('НаимСокр', '')
        or full_name
    )

    # ── ИП: если СвЮЛ пуст, ищем в СвИП ────────────────────────
    if not full_name:
        sv_ip      = data.get('СвИП') or {}
        sv_fl      = sv_ip.get('СвФЛ') or {}
        fl_attrs   = sv_fl.get('@attributes') or {}
        full_name  = ' '.join(filter(None, [
            fl_attrs.get('Фамилия', ''),
            fl_attrs.get('Имя', ''),
            fl_attrs.get('Отчество', ''),
        ]))
        short_name = full_name

    # ── Реквизиты ───────────────────────────────────────────────
    inn_val = attrs.get('ИНН', '')
    ogrn    = attrs.get('ОГРН', '')
    kpp     = attrs.get('КПП', '')

    # ── Руководитель ────────────────────────────────────────────
    dir_fl    = (sv.get('СведДолжнФЛ') or {})
    dir_attrs = (dir_fl.get('СвФЛ') or {}).get('@attributes') or {}
    director  = ' '.join(filter(None, [
        dir_attrs.get('Фамилия', ''),
        dir_attrs.get('Имя', ''),
        dir_attrs.get('Отчество', ''),
    ]))

    # ── ОКВЭД ───────────────────────────────────────────────────
    okved = (
        ((sv.get('СвОКВЭД') or {}).get('СвОКВЭДОсн') or {})
        .get('@attributes', {})
        .get('КодОКВЭД', '')
    )

    # ── Адрес ───────────────────────────────────────────────────
    sv_addr  = sv.get('СвАдресЮЛ') or {}
    addr_rf  = sv_addr.get('АдресРФ') or {}
    addr     = _build_address(addr_rf)
    if not addr:
        # fallback: регион из СвМНЮЛ
        mn = sv_addr.get('СвМНЮЛ') or {}
        addr = mn.get('НаимРегион', '')

    status = 'active' if full_name else 'unknown'

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
