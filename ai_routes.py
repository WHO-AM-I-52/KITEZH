# ╔══════════════════════════════════════════════════════════════╗
# ║ ai_routes.py — ИИ-подбор площадок + OCR-загрузка анкет           ║
# ║ fix #64: flask_login → auth_utils; исправлены колонки БД           ║
# ║ fix #66 [2/2]: логирование ocr_error, маршрут /ai/ocr-upload   ║
# ║ feat #67 [2/3]: маршрут POST /ai/ocr-preview (без сохранения  ║
# ║             в БД), ocr-upload обновлён под Tuple[Dict,str,str]  ║
# ║ feat #68: GET /ai/ocr-status — панель статуса OCR-движка      ║
# ║           POST /ai/ocr-test  — тестовый запуск OCR            ║
# ╚══════════════════════════════════════════════════════════════╝

import json
import os
import logging
import requests as http_requests
from datetime import datetime
from flask import Blueprint, request, jsonify, render_template, session
from werkzeug.utils import secure_filename

from db import get_db
from auth_utils import login_required, admin_required
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


# ─── ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ: сохранить + разобрать файл ──────────────────

def _save_and_parse(file_storage) -> tuple:
    """
    Сохраняет файл в tmp, запускает OCR, удаляет tmp.
    Возвращает: (filename, fields, msg, raw_text)
    Бросает Exception при ошибке сохранения или OCR.
    """
    filename = secure_filename(file_storage.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Неподдерживаемый формат {ext}. Допустимые: PDF, DOCX, DOC, JPG, PNG"
        )
    tmp_path = os.path.join(_UPLOAD_FOLDER, filename)
    try:
        file_storage.save(tmp_path)
        fields, msg, raw_text = extract_anketa_fields(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return filename, fields, msg, raw_text


# ─── МАРШРУТЫ ИИ-ПОДБОРА ──────────────────────────────────────────

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


# ─── МАРШРУТ OCR-ЗАГРУЗКИ АНКЕТЫ ───────────────────────────────────

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

    try:
        filename, fields, msg, _ = _save_and_parse(f)  # raw_text не нужен здесь
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        filename = secure_filename(f.filename) if f.filename else "unknown"
        logger.error("OCR /ocr-upload: неожиданная ошибка '%s': %s", filename, e)
        _log_ocr_error(filename, str(e))
        return jsonify({"ok": False, "error": f"Ошибка обработки: {e}"}), 500

    if not fields:
        _log_ocr_error(filename, msg)
        return jsonify({"ok": False, "error": msg or "Не удалось распознать структуру анкеты."}), 422

    return jsonify({"ok": True, "fields": fields, "msg": msg})


# ─── МАРШРУТ OCR-PREVIEW (#67) ───────────────────────────────────────

@ai_bp.route("/ocr-preview", methods=["POST"])
@login_required
def ocr_preview():
    """
    POST /ai/ocr-preview
    Принимает файл, возвращает JSON без сохранения в БД:
      — успех: {"ok": true, "raw_text": "...", "fields": {...}, "msg": "..."}
      — ошибка: {"ok": false, "error": "..."}
    Используется для ocr_preview.html — просмотр и редактирование
    распознанных полей до переноса в форму обращения.
    """
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
        logger.error("OCR /ocr-preview: неожиданная ошибка '%s': %s", filename, e)
        return jsonify({"ok": False, "error": f"Ошибка обработки: {e}"}), 500

    if not fields and not raw_text:
        return jsonify({
            "ok": False,
            "error": msg or "Не удалось распознать структуру анкеты."
        }), 422

    return jsonify({
        "ok": True,
        "raw_text": raw_text,
        "fields": fields,
        "msg": msg,
    })


# ─── OCR-СТАТУС (#68) ─────────────────────────────────────────────────

def _check_ocr_deps() -> dict:
    """Проверяет наличие и версии всех OCR-зависимостей."""
    status = {}

    # easyocr
    try:
        import easyocr
        status['easyocr'] = {'ok': True, 'version': getattr(easyocr, '__version__', '—')}
    except ImportError:
        status['easyocr'] = {'ok': False, 'error': 'Не установлен (pip install easyocr)'}
    except Exception as e:
        status['easyocr'] = {'ok': False, 'error': str(e)}

    # pytesseract / Tesseract binary
    try:
        import pytesseract
        v = pytesseract.get_tesseract_version()
        status['tesseract'] = {'ok': True, 'version': str(v)}
    except ImportError:
        status['tesseract'] = {'ok': False, 'error': 'pytesseract не установлен'}
    except Exception as e:
        status['tesseract'] = {'ok': False, 'error': str(e)}

    # pdfplumber
    try:
        import pdfplumber
        status['pdfplumber'] = {'ok': True, 'version': getattr(pdfplumber, '__version__', '—')}
    except ImportError:
        status['pdfplumber'] = {'ok': False, 'error': 'Не установлен (pip install pdfplumber)'}
    except Exception as e:
        status['pdfplumber'] = {'ok': False, 'error': str(e)}

    # python-docx
    try:
        import docx
        status['docx'] = {'ok': True, 'version': getattr(docx, '__version__', '—')}
    except ImportError:
        status['docx'] = {'ok': False, 'error': 'Не установлен (pip install python-docx)'}
    except Exception as e:
        status['docx'] = {'ok': False, 'error': str(e)}

    # Pillow
    try:
        import PIL
        status['pillow'] = {'ok': True, 'version': getattr(PIL, '__version__', '—')}
    except ImportError:
        status['pillow'] = {'ok': False, 'error': 'Не установлен (pip install Pillow)'}
    except Exception as e:
        status['pillow'] = {'ok': False, 'error': str(e)}

    return status


@ai_bp.route("/ocr-status", methods=["GET"])
@admin_required
def ocr_status():
    """
    GET /ai/ocr-status
    Страница администратора: статус OCR-зависимостей + статистика ошибок.
    """
    deps = _check_ocr_deps()

    # Ошибки OCR за последние 7 дней
    conn = get_db()
    try:
        errors_7d = conn.execute(
            "SELECT COUNT(*) FROM activity_log "
            "WHERE action='ocr_error' "
            "AND created_at >= datetime('now','-7 days')"
        ).fetchone()[0]

        # Последние 10 ошибок OCR
        recent_errors = conn.execute(
            "SELECT al.created_at, al.detail, u.full_name "
            "FROM activity_log al "
            "LEFT JOIN users u ON al.user_id = u.id "
            "WHERE al.action='ocr_error' "
            "ORDER BY al.created_at DESC LIMIT 10"
        ).fetchall()
        recent_errors = [dict(r) for r in recent_errors]

        # Последняя успешная OCR-активность
        last_ocr = conn.execute(
            "SELECT created_at FROM activity_log "
            "WHERE action IN ('ocr_upload','ocr_preview') "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        last_ocr_dt = last_ocr['created_at'] if last_ocr else None
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
    )


@ai_bp.route("/ocr-test", methods=["POST"])
@admin_required
def ocr_test():
    """
    POST /ai/ocr-test
    Создаёт тестовый PNG 1×1 и прогоняет через easyocr.
    Возвращает JSON: {"ok": true/false, "result": "...", "error": "..."}
    """
    try:
        import easyocr
        import numpy as np

        # Минимальный тестовый массив (белое изображение)
        img = np.ones((50, 200, 3), dtype=np.uint8) * 255
        reader = easyocr.Reader(['ru', 'en'], gpu=False, verbose=False)
        result = reader.readtext(img, detail=0)
        return jsonify({
            'ok': True,
            'result': f"easyocr запущен успешно. Результат: {result or '(пусто — норма для белого изображения)'}" 
        })
    except ImportError:
        return jsonify({'ok': False, 'error': 'easyocr не установлен'})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)})


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
