import urllib.request
import json
import re
import os
from datetime import datetime

REPO_OWNER = "WHO-AM-I-52"
REPO_NAME = "SONAR"
BRANCH = "main"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHANGELOG_PATH = os.path.join(BASE_DIR, "changelog.py")

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
    h = {"User-Agent": "SONAR-Sync", "Accept": "application/vnd.github+json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h

def get_json(url):
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

def get_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": "SONAR-Sync"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode("utf-8")

def fetch_releases():
    data = get_json(
        f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases?per_page=100"
    )
    changelog = []
    for r in data:
        # Пропускаем draft-релизы и релизы без даты публикации
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
            if line.startswith(("-", "*", "–")):
                changes.append(line.lstrip("-*– ").strip())
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
        "В работе":      "in_progress",
        "Запланировано": "planned",
        "Идеи":          "idea",
        "Реализовано":   "done",
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

def write_changelog(changelog, roadmap):
    lines = ["CHANGELOG = [\n"]
    for entry in changelog:
        lines.append("    {\n")
        lines.append(f'        "version": "{entry["version"]}",\n')
        lines.append(f'        "date": "{entry["date"]}",\n')
        lines.append('        "changes": [\n')
        for c in entry["changes"]:
            lines.append(f'            "{c.replace(chr(34), chr(39))}",\n')
        lines.append("        ],\n")
        lines.append("    },\n")
    lines.append("]\n\n")

    lines.append("ROADMAP = [\n")
    for entry in roadmap:
        lines.append("    {\n")
        lines.append(f'        "title": "{entry["title"]}",\n')
        lines.append(f'        "status": "{entry["status"]}",\n')
        lines.append('        "points": [\n')
        for p in entry["points"]:
            lines.append(f'            "{p.replace(chr(34), chr(39))}",\n')
        lines.append("        ],\n")
        lines.append("    },\n")
    lines.append("]\n")

    with open(CHANGELOG_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)

def main():
    print("  Sinhronizaciya changelog s GitHub...")
    if TOKEN:
        print("  Токен найден — лимит 5000 запросов/час")
    else:
        print("  Токен не найден — лимит 60 запросов/час")
    try:
        releases = fetch_releases()
        print(f"  Релизов найдено: {len(releases)}")

        roadmap = fetch_roadmap()
        print(f"  ROADMAP записей: {len(roadmap)}")

        if not releases:
            print("  [!] Релизов нет — CHANGELOG не обновляется.")
            from changelog import CHANGELOG as existing
            releases = [{"version": e["version"],
                         "date": e["date"],
                         "changes": e["changes"]} for e in existing]

        write_changelog(releases, roadmap)
        print("  changelog.py обновлён.")
    except Exception as e:
        print(f"  [OSHIBKA] {e}")

if __name__ == "__main__":
    main()
