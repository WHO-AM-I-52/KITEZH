# ╔══════════════════════════════════════════════════════════════╗
# ║                     github_utils.py                           ║
# ║  Общие утилиты для работы с GitHub API                     ║
# ║  Используется в: _updater.py, sync_changelog.py,          ║
# ║                  publish_release.py                            ║
# ╚══════════════════════════════════════════════════════════════╝

import urllib.request
import urllib.error
import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def load_token() -> str | None:
    """Читает GITHUB_TOKEN из .env рядом со скриптом."""
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GITHUB_TOKEN="):
                    return line.split("=", 1)[1].strip()
    return None


def get_headers(agent: str = "SONAR") -> dict:
    """Возвращает заголовки для GitHub API. Токен подставляется автоматически."""
    token = load_token()
    h = {
        "User-Agent": agent,
        "Accept": "application/vnd.github+json",
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def get_json(url: str, agent: str = "SONAR") -> dict:
    """GET-запрос к GitHub API, возвращает распарсенный JSON."""
    req = urllib.request.Request(url, headers=get_headers(agent))
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def post_json(url: str, payload: dict, agent: str = "SONAR") -> tuple[int, dict]:
    """POST-запрос к GitHub API. Возвращает (status_code, response_dict)."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={**get_headers(agent), "Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())
