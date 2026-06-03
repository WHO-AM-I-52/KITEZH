# ai_routes.py — Blueprint ИИ-подбора площадок, Issue #38
# WIP: не подключён к app.py, не активен в системе
# Для активации: добавить в app.py:
#   from ai_routes import ai_bp
#   app.register_blueprint(ai_bp)   # в блок регистрации Blueprint'ов

import json
import requests as http_requests
from flask import Blueprint, request, jsonify, render_template
from flask_login import login_required

import db
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
    conn = db.get_db()
    rows = conn.execute(
        """
        SELECT r.id, r.applicant_name, r.request_date, r.description,
               r.district, r.status, st.name AS subject_name
        FROM requests r
        LEFT JOIN subject_types st ON r.subject_type_id = st.id
        WHERE LOWER(st.name) LIKE '%подбор%'
          AND r.status NOT IN ('closed', 'rejected')
        ORDER BY r.request_date DESC
        LIMIT 30
        """
    ).fetchall()
    return [dict(r) for r in rows]


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

    log_action("ai_match", f"ИИ-подбор площадки: {investor.get('industry', '—')}")
    return jsonify(result)
