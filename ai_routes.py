# ╔══════════════════════════════════════════════════════════════╗
# ║ ai_routes.py — ИИ-подбор площадок (через Ollama)        ║
# ║ fix #64: flask_login → auth_utils; исправлены колонки БД     ║
# ║           log_action — правильная сигнатура           ║
# ╚═════════════════════════════════════════════════════════════╝

import json
import requests as http_requests
from flask import Blueprint, request, jsonify, render_template, session

from db import get_db
from auth_utils import login_required
from activity_log import log_action

ai_bp = Blueprint("ai", __name__, url_prefix="/ai")

OLLAMA_URL   = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen2.5"

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

    # fix #64: правильная сигнатура log_action(conn, user_id, action, request_id, detail)
    conn = get_db()
    log_action(conn, session['user_id'], 'ai_match', None,
               f"ИИ-подбор площадки: {investor.get('industry', '—')}")
    conn.commit()
    conn.close()

    return jsonify(result)
