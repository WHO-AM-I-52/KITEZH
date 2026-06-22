# ╔══════════════════════════════════════════════════════════════╗
# ║ ai_routes.py — ИИ-подбор площадок + OCR-загрузка анкет           ║
# ║ fix #64: flask_login → auth_utils; исправлены колонки БД           ║
# ║ fix #66 [2/2]: логирование ocr_error, маршрут /ai/ocr-upload   ║
# ║ feat #67 [2/3]: маршрут POST /ai/ocr-preview (без сохранения  ║
# ║             в БД), ocr-upload обновлён под Tuple[Dict,str,str]  ║
# ║ feat #68: GET /ai/ocr-status — панель статуса OCR-движка      ║
# ║           POST /ai/ocr-test  — тестовый запуск OCR            ║
# ║ fix: убрана проверка Tesseract — не используется в проекте    ║
# ║ fix: _save_and_parse — ext берётся до secure_filename()       ║
# ║ feat: _write_ocr_log() — каждый OCR пишется в ocr_log      ║
# ║ feat: POST /ai/ocr-install — фоновая установка + polling     ║
# ║ feat: GET  /ai/ocr-install-status — поллинг статуса pip       ║
# ║ feat: _get_site_requests_full() — полная выгрузка всех полей  ║
# ║       обращения для ИИ (площадь, ВЭД, районы, коммуникации)  ║
# ╚══════════════════════════════════════════════════════════════╝

import json
import os
import sys
import subprocess
import threading
import logging
import requests as http_requests
from datetime import datetime
from flask import Blueprint, request, jsonify, render_template, session
from werkzeug.utils import secure_filename

from db import get_db
from core.auth_utils import login_required, admin_required
from core.activity_log import log_action
from services.ocr_utils import extract_anketa_fields
from paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

ai_bp = Blueprint("ai", __name__, url_prefix="/ai")

OLLAMA_URL   = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5"

# Путь от КОРНЯ проекта (paths.PROJECT_ROOT), а не от __file__ модуля:
# модуль живёт в routes/, поэтому __file__ указывал бы на routes/uploads/.
_UPLOAD_FOLDER = os.path.join(PROJECT_ROOT, "uploads", "ocr_tmp")
os.makedirs(_UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png"}

# Белый список: key = ид в deps, value = реальное имя pip-пакета
_OCR_INSTALL_WHITELIST = {
    'easyocr':    'easyocr',
    'pdfplumber':  'pdfplumber',
    'docx':        'python-docx',
    'pillow':      'Pillow',
}

# Хранилище фоновых заданий установки
# { pkg_key: {'running': bool, 'done': bool, 'ok': bool, 'output': str} }
_install_jobs: dict = {}
_install_lock = threading.Lock()

SYSTEM_PROMPT = (
    "Ты — ИИ-помощник CRM-системы SONAR (Нижегородская область). "
    "Тебе передаётся профиль инвестора и список обращений о подборе площадок из базы данных. "
    "Выбери топ-3 наиболее подходящих и объясни почему кратко. "
    "Отвечай строго на русском языке. "
    "Формат ответа — только JSON без лишнего текста: "
    "{\"matches\": [{\"id\": 1, \"name\": \"...\", \"score\": 85, \"reason\": \"...\"}]}"
)


def _safe(val) -> str:
    """Конвертирует любое значение БД в строку или пустую строку."""
    if val is None:
        return ""
    return str(val).strip()


def _get_site_requests():
    """Краткая выгрузка (устаревшая совместимость) — используйте _get_site_requests_full."""
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT r.id,
                   COALESCE(r.applicant_short_name, r.applicant_full_name) AS applicant_name,
                   r.request_date,
                   r.project_name   AS description,
                   r.preferred_districts AS district,
                   r.status,
                   st.name          AS subject_name
            FROM requests r
            LEFT JOIN subject_types st ON r.subject_type_id = st.id
            WHERE LOWER(st.name) LIKE '%подбор%'
              AND r.status NOT IN ('closed', 'draft')
            ORDER BY r.request_date DESC
            LIMIT 30
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _get_site_requests_full():
    """
    Полная выгрузка всех значимых полей обращения о подборе площадки для ИИ-подбора.
    Передаёт: параметры инвестора, требования к площади, коммуникации, ВЭД,
    предпочтительные районы, тип права, инвестиции, рабочие места и т.д.
    """
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT
                r.id,
                r.request_number,
                r.request_date,
                r.status,
                COALESCE(r.applicant_short_name, r.applicant_full_name) AS applicant_name,
                r.applicant_full_name,
                r.applicant_short_name,
                r.applicant_inn,
                r.applicant_legal_form,
                r.applicant_msp_category,
                r.applicant_okved_main,
                r.project_name,
                r.contact_person,
                r.contact_phone,
                r.contact_email,
                r.contact_position,

                -- Параметры запроса
                r.investment_total,
                r.jobs_total,
                r.site_area_ha_min,
                r.site_area_ha_max,
                r.site_build_area_m2_min,
                r.site_build_area_m2_max,
                r.site_right,
                r.preferred_districts,
                r.location_extra,

                -- Производство и продукция
                r.product_nomenclature,
                r.production_description,
                r.object_composition,
                r.construction_stages,
                r.raw_materials,
                r.raw_extra,

                -- Коммуникации (требования инвестора)
                r.elec_unit,
                r.heat_unit,
                r.gas_unit_h,
                r.gas_unit_y,
                r.water_unit,
                r.heat_source,
                r.gas_purpose,
                r.internet,
                r.engineering_extra,
                r.road_extra,
                r.railway_extra,
                r.transport_extra,

                -- Дополнительно
                r.hazard_class,
                r.site_shape,
                r.site_other,
                r.source_type,
                r.additional_info,
                r.answer_date,
                r.answer_method,
                r.answer_notes,

                -- Справочники
                st.name  AS subject_name,
                rt.name  AS result_name,
                ass.full_name AS assigned_to_name

            FROM requests r
            LEFT JOIN subject_types st  ON r.subject_type_id = st.id
            LEFT JOIN result_types  rt  ON r.result_type_id  = rt.id
            LEFT JOIN users         ass ON r.assigned_to     = ass.id
            WHERE LOWER(COALESCE(st.name, '')) LIKE '%подбор%'
              AND r.status NOT IN ('closed', 'draft')
            ORDER BY r.request_date DESC
            LIMIT 50
            """
        ).fetchall()

        result = []
        for r in rows:
            rec = {
                "id":                   r["id"],
                "request_number":       _safe(r["request_number"]),
                "request_date":         _safe(r["request_date"]),
                "status":               _safe(r["status"]),
                "subject_name":         _safe(r["subject_name"]),

                # Заявитель
                "applicant_name":       _safe(r["applicant_name"]),
                "applicant_full_name":  _safe(r["applicant_full_name"]),
                "applicant_inn":        _safe(r["applicant_inn"]),
                "applicant_legal_form": _safe(r["applicant_legal_form"]),
                "applicant_msp":        _safe(r["applicant_msp_category"]),
                "okved_main":           _safe(r["applicant_okved_main"]),

                # Проект
                "project_name":         _safe(r["project_name"]),
                "investment_mln":       r["investment_total"],
                "jobs_total":           r["jobs_total"],

                # Требования к площади
                "site_area_ha_min":     r["site_area_ha_min"],
                "site_area_ha_max":     r["site_area_ha_max"],
                "site_build_m2_min":    r["site_build_area_m2_min"],
                "site_build_m2_max":    r["site_build_area_m2_max"],
                "site_right":           _safe(r["site_right"]),

                # Географические предпочтения
                "preferred_districts":  _safe(r["preferred_districts"]),
                "location_extra":       _safe(r["location_extra"]),

                # Производство
                "product_nomenclature": _safe(r["product_nomenclature"]),
                "production_desc":      _safe(r["production_description"]),
                "object_composition":   _safe(r["object_composition"]),
                "construction_stages":  _safe(r["construction_stages"]),
                "raw_materials":        _safe(r["raw_materials"]),
                "raw_extra":            _safe(r["raw_extra"]),
                "hazard_class":         _safe(r["hazard_class"]),

                # Коммуникации (требования)
                "engineering_extra":    _safe(r["engineering_extra"]),
                "heat_source":          _safe(r["heat_source"]),
                "gas_purpose":          _safe(r["gas_purpose"]),
                "internet":             _safe(r["internet"]),
                "road_extra":           _safe(r["road_extra"]),
                "railway_extra":        _safe(r["railway_extra"]),
                "transport_extra":      _safe(r["transport_extra"]),

                # Единицы измерения (для понимания масштаба)
                "elec_unit":            _safe(r["elec_unit"]),
                "heat_unit":            _safe(r["heat_unit"]),
                "gas_unit_h":           _safe(r["gas_unit_h"]),
                "gas_unit_y":           _safe(r["gas_unit_y"]),
                "water_unit":           _safe(r["water_unit"]),

                # Прочее
                "source_type":          _safe(r["source_type"]),
                "additional_info":      _safe(r["additional_info"]),
                "answer_date":          _safe(r["answer_date"]),
                "result_name":          _safe(r["result_name"]),
                "assigned_to":          _safe(r["assigned_to_name"]),
            }
            # Убираем пустые строки чтобы не засорять контекст ИИ
            rec = {k: v for k, v in rec.items() if v not in ("", None)}
            result.append(rec)

        return result
    finally:
        conn.close()


def _ask_ollama(investor_profile: dict, site_requests: list) -> dict:
    user_message = (
        f"Профиль инвестора:\n{json.dumps(investor_profile, ensure_ascii=False)}\n\n"
        f"Доступные обращения по площадкам:\n{json.dumps(site_requests, ensure_ascii=False)}"
    )
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
    }
    resp = http_requests.post(OLLAMA_URL, json=payload, timeout=120)
    resp.raise_for_status()
    content = resp.json()["message"]["content"]
    start = content.find("{")
    end   = content.rfind("}") + 1
    return json.loads(content[start:end])


# ─── ВСПОМОГАТЕЛЬНЫЕ ────────────────────────────────────────────────────────────

def _save_and_parse(file_storage) -> tuple:
    ext = os.path.splitext(file_storage.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Неподдерживаемый формат {ext}. Допустимые: PDF, DOCX, DOC, JPG, PNG"
        )
    safe_name = secure_filename(file_storage.filename) or f"upload{ext}"
    tmp_path = os.path.join(_UPLOAD_FOLDER, safe_name)
    try:
        file_storage.save(tmp_path)
        fields, msg, raw_text = extract_anketa_fields(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return safe_name, fields, msg, raw_text


def _write_ocr_log(filename, raw_text, fields, msg, ok):
    try:
        conn = get_db()
        conn.execute(
            """
            INSERT INTO ocr_log (created_at, user_id, filename, raw_text, fields_json, msg, ok)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                session.get('user_id'),
                filename,
                raw_text or '',
                json.dumps(fields or {}, ensure_ascii=False),
                msg or '',
                1 if ok else 0,
            )
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("_write_ocr_log: %s", e)


def _log_ocr_error(filename: str, detail: str) -> None:
    try:
        conn = get_db()
        log_action(conn, session.get('user_id'), 'ocr_error', None,
                   f"OCR ошибка: {filename} | {detail[:200]}")
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("_log_ocr_error: %s", e)


# ─── МАРШРУТЫ ИИ-ПОДБОРА ──────────────────────────────────────────────────────────

@ai_bp.route("/match", methods=["GET"])
@login_required
def match_form():
    return render_template("ai_match.html")


@ai_bp.route("/match", methods=["POST"])
@login_required
def match_result():
    investor = {
        "industry":    request.form.get("industry", ""),
        "area_needed": request.form.get("area_needed", ""),
        "district":    request.form.get("district", ""),
        "budget":      request.form.get("budget", ""),
        "notes":       request.form.get("notes", ""),
    }
    # Используем полную выгрузку всех полей
    site_requests = _get_site_requests_full()
    if not site_requests:
        return jsonify({"error": "Нет доступных обращений по площадкам"}), 404

    try:
        result = _ask_ollama(investor, site_requests)
    except (http_requests.RequestException, json.JSONDecodeError) as e:
        return jsonify({"error": f"Ошибка ИИ-подбора: {e}"}), 503

    conn = get_db()
    log_action(conn, session['user_id'], 'ai_match', None,
               f"ИИ-подбор площадки: {investor.get('industry', '—')}")
    conn.commit()
    conn.close()
    return jsonify(result)


# ─── API: получить все обращения для ИИ (JSON) ────────────────────────────────

@ai_bp.route("/site-requests", methods=["GET"])
@login_required
def get_site_requests_api():
    """
    GET /ai/site-requests — возвращает полный список обращений о подборе
    площадок со всеми полями. Используется при отправке данных ИИ текстом.

    Query params:
        limit — макс. кол-во записей (по умолчанию 50)
        status — фильтр по статусу (опционально)
    """
    limit = min(int(request.args.get("limit", 50)), 200)
    status_filter = request.args.get("status", "")

    conn = get_db()
    try:
        q = """
            SELECT
                r.id, r.request_number, r.request_date, r.status,
                COALESCE(r.applicant_short_name, r.applicant_full_name) AS applicant_name,
                r.applicant_full_name, r.applicant_short_name,
                r.applicant_inn, r.applicant_legal_form,
                r.applicant_msp_category, r.applicant_okved_main,
                r.project_name,
                r.contact_person, r.contact_phone, r.contact_email, r.contact_position,
                r.investment_total, r.jobs_total,
                r.site_area_ha_min, r.site_area_ha_max,
                r.site_build_area_m2_min, r.site_build_area_m2_max,
                r.site_right, r.preferred_districts, r.location_extra,
                r.product_nomenclature, r.production_description,
                r.object_composition, r.construction_stages,
                r.raw_materials, r.raw_extra,
                r.hazard_class, r.site_shape, r.site_other,
                r.elec_unit, r.heat_unit, r.gas_unit_h, r.gas_unit_y, r.water_unit,
                r.heat_source, r.gas_purpose, r.internet,
                r.engineering_extra, r.road_extra, r.railway_extra, r.transport_extra,
                r.source_type, r.additional_info,
                r.incoming_number, r.answer_date, r.answer_method, r.answer_notes,
                r.applicant_feedback, r.taken_under_supervision,
                st.name  AS subject_name,
                rt.name  AS result_name,
                ass.full_name AS assigned_to_name
            FROM requests r
            LEFT JOIN subject_types st  ON r.subject_type_id = st.id
            LEFT JOIN result_types  rt  ON r.result_type_id  = rt.id
            LEFT JOIN users         ass ON r.assigned_to     = ass.id
            WHERE LOWER(COALESCE(st.name, '')) LIKE '%подбор%'
              AND r.status NOT IN ('draft')
        """
        params = []
        if status_filter:
            q += " AND r.status = ?"
            params.append(status_filter)
        q += " ORDER BY r.request_date DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(q, params).fetchall()
        data = []
        for r in rows:
            rec = dict(r)
            # Убираем None для чистоты JSON
            rec = {k: v for k, v in rec.items() if v not in (None, "")}
            data.append(rec)
    finally:
        conn.close()

    return jsonify({
        "total": len(data),
        "requests": data,
    })


# ─── OCR-ЗАГРУЗКА ─────────────────────────────────────────────────────────────────────

@ai_bp.route("/ocr-upload", methods=["POST"])
@login_required
def ocr_upload():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Файл не передан"}), 400
    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Пустое имя файла"}), 400
    try:
        filename, fields, msg, raw_text = _save_and_parse(f)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        filename = secure_filename(f.filename) if f.filename else "unknown"
        logger.error("OCR /ocr-upload: '%s': %s", filename, e)
        _log_ocr_error(filename, str(e))
        _write_ocr_log(filename, '', {}, str(e), ok=False)
        return jsonify({"ok": False, "error": f"Ошибка обработки: {e}"}), 500
    if not fields:
        _log_ocr_error(filename, msg)
        _write_ocr_log(filename, raw_text or '', {}, msg, ok=False)
        return jsonify({"ok": False, "error": msg or "Не удалось распознать структуру анкеты."}), 422
    _write_ocr_log(filename, raw_text or '', fields, msg, ok=True)
    return jsonify({"ok": True, "fields": fields, "msg": msg})


@ai_bp.route("/ocr-preview", methods=["POST"])
@login_required
def ocr_preview():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Файл не передан"}), 400
    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Пустое имя файла"}), 400
    try:
        filename, fields, msg, raw_text = _save_and_parse(f)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        filename = secure_filename(f.filename) if f.filename else "unknown"
        logger.error("OCR /ocr-preview: '%s': %s", filename, e)
        _write_ocr_log(filename, '', {}, str(e), ok=False)
        return jsonify({"ok": False, "error": f"Ошибка обработки: {e}"}), 500
    if not fields and not raw_text:
        _write_ocr_log(filename, '', {}, msg, ok=False)
        return jsonify({"ok": False, "error": msg or "Не удалось распознать структуру анкеты."}), 422
    _write_ocr_log(filename, raw_text or '', fields, msg, ok=True)
    return jsonify({"ok": True, "raw_text": raw_text, "fields": fields, "msg": msg})


# ─── OCR-СТАТУС (#68) ─────────────────────────────────────────────────────────────

def _check_ocr_deps() -> dict:
    status = {}
    for key, mod, attr in [
        ('easyocr',    'easyocr',    '__version__'),
        ('pdfplumber', 'pdfplumber', '__version__'),
        ('docx',       'docx',       '__version__'),
        ('pillow',     'PIL',        '__version__'),
    ]:
        try:
            m = __import__(mod)
            status[key] = {'ok': True, 'version': getattr(m, attr, '—')}
        except ImportError:
            pip_name = _OCR_INSTALL_WHITELIST.get(key, key)
            status[key] = {'ok': False, 'error': f'Не установлен (pip install {pip_name})'}
        except Exception as e:
            status[key] = {'ok': False, 'error': str(e)}
    return status


@ai_bp.route("/ocr-status", methods=["GET"])
@admin_required
def ocr_status():
    deps = _check_ocr_deps()
    conn = get_db()
    try:
        errors_7d = conn.execute(
            "SELECT COUNT(*) FROM activity_log "
            "WHERE action='ocr_error' AND created_at >= datetime('now','-7 days')"
        ).fetchone()[0]
        recent_errors = conn.execute(
            "SELECT al.created_at, al.detail, u.full_name "
            "FROM activity_log al LEFT JOIN users u ON al.user_id = u.id "
            "WHERE al.action='ocr_error' ORDER BY al.created_at DESC LIMIT 10"
        ).fetchall()
        recent_errors = [dict(r) for r in recent_errors]
        last_ocr = conn.execute(
            "SELECT created_at FROM activity_log "
            "WHERE action IN ('ocr_upload','ocr_preview') "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        last_ocr_dt = last_ocr['created_at'] if last_ocr else None
        ocr_logs = conn.execute(
            """
            SELECT ol.id, ol.created_at, ol.filename, ol.msg, ol.ok,
                   ol.raw_text, ol.fields_json, u.full_name AS user_name
            FROM ocr_log ol LEFT JOIN users u ON ol.user_id = u.id
            ORDER BY ol.created_at DESC LIMIT 20
            """
        ).fetchall()
        ocr_logs = [dict(r) for r in ocr_logs]
    finally:
        conn.close()
    all_ok = all(v['ok'] for v in deps.values())
    return render_template(
        'ocr_status.html',
        deps=deps,
        errors_7d=errors_7d,
        recent_errors=recent_errors,
        last_ocr_dt=last_ocr_dt,
        all_ok=all_ok,
        checked_at=datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
        ocr_logs=ocr_logs,
    )


@ai_bp.route("/ocr-test", methods=["POST"])
@admin_required
def ocr_test():
    try:
        import easyocr, numpy as np
        img = np.ones((50, 200, 3), dtype=np.uint8) * 255
        reader = easyocr.Reader(['ru', 'en'], gpu=False, verbose=False)
        result = reader.readtext(img, detail=0)
        return jsonify({'ok': True,
                        'result': f"easyocr запущен успешно. {result or '(пусто — норма)'}" })
    except ImportError:
        return jsonify({'ok': False, 'error': 'easyocr не установлен'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


# ─── OCR-УСТАНОВКА: ФОН ПОТОК + POLLING ────────────────────────────────────

def _run_pip_install(pkg_key: str, pip_package: str, user_id) -> None:
    """Запускается в отдельном потоке. Обновляет _install_jobs по завершении."""
    try:
        proc = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', pip_package],
            capture_output=True, text=True, timeout=300,
        )
        output = (proc.stdout or '') + (proc.stderr or '')
        ok = proc.returncode == 0
    except subprocess.TimeoutExpired:
        output = 'Ошибка: таймаут (300 сек). Установите вручную.'
        ok = False
    except Exception as e:
        output = f'Ошибка: {e}'
        ok = False

    with _install_lock:
        _install_jobs[pkg_key] = {
            'running': False, 'done': True,
            'ok': ok, 'output': output.strip()
        }

    # Логируем в activity_log (без app_context не работает session,
    # поэтому передаём user_id явно)
    try:
        conn = get_db()
        conn.execute(
            "INSERT INTO activity_log (user_id, action, entity_id, detail, created_at) "
            "VALUES (?, 'ocr_install', NULL, ?, datetime('now'))",
            (user_id, f"pip install {pip_package} — {'ok' if ok else 'error'} | {output[:200]}")
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("_run_pip_install: лог не записан: %s", e)


@ai_bp.route("/ocr-install", methods=["POST"])
@admin_required
def ocr_install():
    """
    Запускает pip install в фоновом потоке и немедленно возвращает started=True.
    Браузер опрашивает статус через GET /ai/ocr-install-status?pkg=<key>.
    """
    data = request.get_json(silent=True) or {}
    pkg_key = data.get('package', '').strip()

    if pkg_key not in _OCR_INSTALL_WHITELIST:
        return jsonify({'ok': False,
                        'error': f'Пакет «{pkg_key}» не дозволен.'}), 400

    with _install_lock:
        job = _install_jobs.get(pkg_key, {})
        if job.get('running'):
            return jsonify({'ok': True, 'started': True, 'already': True})
        _install_jobs[pkg_key] = {'running': True, 'done': False, 'ok': False, 'output': ''}

    pip_package = _OCR_INSTALL_WHITELIST[pkg_key]
    user_id = session.get('user_id')
    t = threading.Thread(
        target=_run_pip_install,
        args=(pkg_key, pip_package, user_id),
        daemon=True,
    )
    t.start()

    return jsonify({'ok': True, 'started': True})


@ai_bp.route("/ocr-install-status", methods=["GET"])
@admin_required
def ocr_install_status():
    """Возвращает текущий статус фоновой установки."""
    pkg_key = request.args.get('pkg', '').strip()
    with _install_lock:
        job = _install_jobs.get(pkg_key)
    if job is None:
        return jsonify({'running': False, 'done': False, 'ok': False, 'output': ''})
    return jsonify(job)
