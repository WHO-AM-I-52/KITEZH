import urllib.request
import json
import re
import os
import sys
from datetime import datetime

REPO_OWNER = "WHO-AM-I-52"
REPO_NAME = "KITEZH"
BRANCH = "main"
# Корень проекта — из paths.py (единый источник правды), устойчиво к переносу в updater/.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in (_THIS_DIR, os.path.dirname(_THIS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from paths import PROJECT_ROOT as BASE_DIR
from paths import CHANGELOG_PATH, ROADMAP_PATH
# CHANGELOG_PATH → <корень>/changelog.py (читается как `from changelog import CHANGELOG`).
# ROADMAP_PATH  → <корень>/services/roadmap.py — ИМЕННО тот файл, что читает
# приложение (`from services.roadmap import ROADMAP`). Раньше генератор писал в корень
# (roadmap.py), который после переноса в services/ перестал читаться — отсюда десинхрон.


def load_token():
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GITHUB_TOKEN="):
                    return line.split("=", 1)[1].strip()
    return None


TOKEN = load_token()


def _headers():
    h = {"User-Agent": "KITEZH-Sync", "Accept": "application/vnd.github+json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def get_json(url):
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def get_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "KITEZH-Sync"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8")


def _py_str(value: str) -> str:
    """Возвращает корректный Python-строковый литерал для любого текста.

    Использует repr() вместо ручной замены кавычек — безопасно экранирует
    одинарные кавычки, двойные кавычки, переносы строк и спецсимволы.
    fix #33
    """
    return repr(str(value))


def fetch_releases():
    data = get_json(
        f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases?per_page=100"
    )
    changelog = []
    for r in data:
        published_at = r.get("published_at")
        if not published_at:
            continue
        version = r["tag_name"].lstrip("v")
        try:
            date = datetime.strptime(published_at, "%Y-%m-%dT%H:%M:%SZ").strftime("%d.%m.%Y")
        except Exception:
            date = ""
        body = r.get("body", "") or ""
        changes = []
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            # строки с ведущим маркером: - * –
            if line.startswith(("-", "*", "\u2013")):
                changes.append(line.lstrip("-*\u2013 ").strip())
                continue
            # строки вида "#N — текст" или "текст — текст" (формат KITEZH-релизов)
            if re.match(r"^#?\d", line) and " \u2014 " in line:
                changes.append(line)
                continue
            # строки вида "текст → текст" (формат исправлений)
            if " \u2192 " in line and not line.startswith(("#", "!")):
                changes.append(line)
                continue
        if not changes:
            changes = [r.get("name", f"Release {version}")]
        changelog.append({"version": version, "date": date, "changes": changes})
    return changelog


def fetch_roadmap():
    url = (f"https://raw.githubusercontent.com/"
           f"{REPO_OWNER}/{REPO_NAME}/{BRANCH}/ROADMAP.md")
    content = get_text(url)
    roadmap = []

    STATUS_MAP = {
        "\u0412 \u0440\u0430\u0431\u043e\u0442\u0435":      "in_progress",
        "\u0417\u0430\u043f\u043b\u0430\u043d\u0438\u0440\u043e\u0432\u0430\u043d\u043e": "planned",
        "\u0418\u0434\u0435\u0438":          "idea",
        "\u0420\u0435\u0430\u043b\u0438\u0437\u043e\u0432\u0430\u043d\u043e":   "done",
    }

    current_status = "planned"
    current_entry = None

    for line in content.splitlines():
        if re.match(r"^##\s+", line):
            for key, val in STATUS_MAP.items():
                if key in line:
                    current_status = val
                    break
            if current_entry:
                roadmap.append(current_entry)
                current_entry = None
            continue

        title_match = re.match(r"^###\s+(.+)", line)
        if title_match:
            if current_entry:
                roadmap.append(current_entry)
            current_entry = {
                "version": "",
                "title": title_match.group(1).strip(),
                "status": current_status,
                "eta": "",
                "points": [],
            }
            continue

        if current_entry and re.match(r"^[-*]\s+", line):
            point = re.sub(r"^[-*]\s+", "", line).strip()
            current_entry["points"].append(point)

    if current_entry:
        roadmap.append(current_entry)

    roadmap = [r for r in roadmap if r["status"] != "done"]
    return roadmap


def write_changelog(changelog):
    """Пишет только CHANGELOG в changelog.py."""
    lines = ["CHANGELOG = [\n"]
    for entry in changelog:
        lines.append("    {\n")
        lines.append(f'        "version": {_py_str(entry["version"])},\n')
        lines.append(f'        "date": {_py_str(entry["date"])},\n')
        lines.append('        "changes": [\n')
        for c in entry["changes"]:
            lines.append(f'            {_py_str(c)},\n')
        lines.append("        ],\n")
        lines.append("    },\n")
    lines.append("]\n")

    with open(CHANGELOG_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


def write_roadmap(roadmap):
    """Пишет только ROADMAP в roadmap.py."""
    lines = ["ROADMAP = [\n"]
    for entry in roadmap:
        lines.append("    {\n")
        lines.append(f'        "version": {_py_str(entry.get("version", ""))},\n')
        lines.append(f'        "title": {_py_str(entry["title"])},\n')
        lines.append(f'        "status": {_py_str(entry["status"])},\n')
        lines.append(f'        "eta": {_py_str(entry.get("eta", ""))},\n')
        lines.append('        "points": [\n')
        for p in entry["points"]:
            lines.append(f'            {_py_str(p)},\n')
        lines.append("        ],\n")
        lines.append("    },\n")
    lines.append("]\n")

    with open(ROADMAP_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)


def main(log_fn=print):
    # Заголовок убран — выводит _updater.py один раз через _log()
    if TOKEN:
        log_fn("  Токен найден — лимит 5000 запросов/час")
    else:
        log_fn("  Токен не найден — лимит 60 запросов/час")
    try:
        releases = fetch_releases()
        log_fn(f"  Релизов найдено: {len(releases)}")

        roadmap = fetch_roadmap()
        log_fn(f"  ROADMAP записей: {len(roadmap)}")

        if not releases:
            log_fn("  [!] Релизов нет — CHANGELOG не обновляется.")
            from changelog import CHANGELOG as existing
            releases = [{"version": e["version"],
                         "date": e["date"],
                         "changes": e["changes"]} for e in existing]

        write_changelog(releases)
        log_fn("  changelog.py обновлён.")

        write_roadmap(roadmap)
        log_fn("  roadmap.py обновлён.")
    except Exception as e:
        log_fn(f"  [ОШИБКА] {e}")


if __name__ == "__main__":
    main()
