# ╔══════════════════════════════════════════════════════════════╗
# ║ ai_routes.py — ИИ-подбор площадок + OCR-загрузка анкет           ║
# ║ fix #64: flask_login → auth_utils; исправлены колонки БД           ║
# ║ fix #66 [2/2]: логирование ocr_error, маршрут /ai/ocr-upload   ║
# ╚══════════════════════════════════════════════════════════════╝

import json
import os
import logging
import requests as http_requests
from flask import Blueprint, request, jsonify, render_template, session
from werkzeug.utils import secure_filename

from db import get_db
from auth_utils import login_required
from activity_log import log_action
from ocr_utils import extract_anketa_fields

logger = logging.getLogger(__name__)

ai_bp = Blueprint("ai", __name__, url_prefix="/ai")

OLLAMA_URL   = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5"

# Папка для временных OCR-файлов
_UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads", "ocr_tmp")
os.makedirs(_UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".doc", ".jpg", ".jpeg", ".png"}

SYSTEM_PROMPT = (
    "Ты — ИИ-помощник CRM-системы SONAR (Нижегородская область). "
    "Тебе передаётся профиль инвестора и список обращений о подборе площадок из базы данных. "
    "Выбери топ-3 наиболее подходящих и объясни почему кратко. "
    "Отвечай строго на русском языке. "
    "Формат ответа — только JSON без лишнего текста: "
    "{\"matches\": [{\"id\": 1, \"name\": \"...\", \"score\": 85, \"reason\": \"...\"}]}"
)


def _get_site_requests():
    """Возвращает обращения типа 'Подбор з/у' и 'Подбор здания / помещения'."""
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


def _ask_ollama(investor_profile: dict, site_requests: list) -> dict:
    """Отправляет запрос к локальному Ollama/qwen2.5."""
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


# ─── МАРШРУТЫ ИИ-ПОДБОРА ───────────────────────────────────────────────

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
    site_requests = _get_site_requests()
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


# ─── МАРШРУТ OCR-ЗАГРУЗКИ АНКЕТЫ ────────────────────────────────────────

@ai_bp.route("/ocr-upload", methods=["POST"])
@login_required
def ocr_upload():
    """
    POST /ai/ocr-upload
    Принимает файл анкеты, запускает OCR, возвращает JSON:
      — успех: {"ok": true,  "fields": {...}, "msg": "..."}
      — ошибка: {"ok": false, "error": "..."}
    Ошибки логируются в activity_log с типом ocr_error.
    """
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "Файл не передан"}), 400

    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Пустое имя файла"}), 400

    filename = secure_filename(f.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return jsonify({
            "ok": False,
            "error": f"Неподдерживаемый формат {ext}. Допустимые: PDF, DOCX, DOC, JPG, PNG"
        }), 400

    tmp_path = os.path.join(_UPLOAD_FOLDER, filename)
    try:
        f.save(tmp_path)
        fields, msg = extract_anketa_fields(tmp_path)
    except Exception as e:
        logger.error("OCR /ocr-upload: неожиданная ошибка '%s': %s", filename, e)
        # fix #66: логировать неожиданные OCR-ошибки в activity_log
        _log_ocr_error(filename, str(e))
        return jsonify({"ok": False, "error": f"Ошибка обработки: {e}"}), 500
    finally:
        # Удаляем временный файл после обработки
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

    if not fields:
        # fix #66: пустой результат — тоже ошибка, логируем
        _log_ocr_error(filename, msg)
        return jsonify({"ok": False, "error": msg or "Не удалось распознать структуру анкеты."}), 422

    return jsonify({"ok": True, "fields": fields, "msg": msg})


def _log_ocr_error(filename: str, detail: str) -> None:
    """Записывает ocr_error в activity_log. Не бросает исключения."""
    try:
        conn = get_db()
        log_action(
            conn,
            session.get('user_id'),
            'ocr_error',
            None,
            f"OCR ошибка: {filename} | {detail[:200]}"
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("_log_ocr_error: не удалось записать в activity_log: %s", e)
