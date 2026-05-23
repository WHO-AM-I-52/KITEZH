import urllib.request
import urllib.parse
import urllib.error
import json
import os
import sys
from datetime import datetime

REPO_OWNER = "WHO-AM-I-52"
REPO_NAME  = "SONAR"
BRANCH     = "main"
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
API_BASE   = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"

BAT_NAME = "start SONAR.bat"

PROTECTED = {"db", "uploads", "reports", "WPy", "Bacup",
             "_updater.py", "update.bat", ".env",
             "database.db", "database.db-shm", "database.db-wal"}

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
    h = {"User-Agent": "SONAR-Updater", "Accept": "application/vnd.github+json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h

def get_json(url):
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode())
        show_rate_limit(r.headers)
        return data

def post_json(url, payload):
    body = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        url, data=body, headers={**_headers(), "Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

def fetch_raw(url):
    req = urllib.request.Request(url, headers={"User-Agent": "SONAR-Updater"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()

def make_raw_url(path):
    encoded = urllib.parse.quote(path, safe="/")
    return (f"https://raw.githubusercontent.com/"
            f"{REPO_OWNER}/{REPO_NAME}/{BRANCH}/{encoded}")

def download_file(path, dest_path):
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    data = fetch_raw(make_raw_url(path))
    with open(dest_path, "wb") as f:
        f.write(data)

def get_tree():
    data = get_json(f"{API_BASE}/git/trees/{BRANCH}?recursive=1")
    return data.get("tree", [])

def show_rate_limit(headers):
    remaining = headers.get("X-RateLimit-Remaining")
    limit     = headers.get("X-RateLimit-Limit")
    reset_ts  = headers.get("X-RateLimit-Reset")
    if remaining is None:
        return
    reset_str = ""
    if reset_ts:
        try:
            reset_str = datetime.fromtimestamp(int(reset_ts)).strftime("%H:%M")
        except Exception:
            pass
    print(f"  Лимит API: {remaining}/{limit} осталось" +
          (f" (сброс в {reset_str})" if reset_str else ""))

def load_changelog():
    changelog_path = os.path.join(BASE_DIR, "changelog.py")
    if not os.path.exists(changelog_path):
        return None, None
    try:
        ns = {}
        with open(changelog_path, encoding="utf-8") as f:
            exec(f.read(), ns)
        cl = ns.get("CHANGELOG", [])
        if not cl:
            return None, None
        latest = cl[0]
        version = latest.get("version", "")
        changes = latest.get("changes", [])
        body = "\n".join(f"- {c}" for c in changes)
        return version, body
    except Exception as e:
        print(f"  [Внимание] Не удалось прочитать changelog.py: {e}")
        return None, None

def ensure_github_release():
    if not TOKEN:
        print("  [Релиз] Токен не найден — автосоздание релиза пропущено.")
        return

    version, body = load_changelog()
    if not version:
        print("  [Релиз] Не удалось определить версию — пропуск.")
        return

    tag = f"v{version}"

    try:
        req = urllib.request.Request(
            f"{API_BASE}/releases/tags/{tag}",
            headers=_headers()
        )
        with urllib.request.urlopen(req, timeout=15):
            print(f"  [Релиз] {tag} уже существует — пропуск.")
            return
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"  [Релиз] Ошибка проверки: {e.code}")
            return

    print(f"  [Релиз] Создаю {tag} на GitHub...")
    status, resp = post_json(
        f"{API_BASE}/releases",
        {
            "tag_name":         tag,
            "target_commitish": BRANCH,
            "name":             tag,
            "body":             body,
            "draft":            False,
            "prerelease":       False,
        }
    )
    if status == 201:
        print(f"  [Релиз] v{tag} успешно создан: {resp.get('html_url', '')}")
    else:
        msg = resp.get("message", "неизвестная ошибка")
        print(f"  [Релиз] Не удалось создать {tag}: {msg}")

def main():
    print("  Подключаемся к GitHub...")
    if TOKEN:
        print("  Токен найден — лимит 5000 запросов/час")
    else:
        print("  Токен не найден — лимит 60 запросов/час")

    try:
        tree = get_tree()
    except urllib.error.HTTPError as e:
        if e.code == 403:
            reset_ts  = e.headers.get("X-RateLimit-Reset")
            reset_str = ""
            if reset_ts:
                try:
                    reset_str = datetime.fromtimestamp(int(reset_ts)).strftime("%H:%M")
                except Exception:
                    pass
            print(f"  [ОШИБКА] Rate limit исчерпан." +
                  (f" Сброс в {reset_str}." if reset_str else " Подожди и повтори."))
        else:
            print(f"  [ОШИБКА] {e}")
        sys.exit(1)
    except Exception as e:
        print(f"  [ОШИБКА] Не удалось получить данные с GitHub: {e}")
        sys.exit(1)

    print("  Распаковываем...")

    updated     = 0
    skipped     = 0
    bat_updated = False

    for item in tree:
        path      = item["path"]
        item_type = item["type"]

        top_level = path.split("/")[0]
        if top_level in PROTECTED:
            skipped += 1
            continue

        if "__pycache__" in path or path.endswith(".pyc"):
            continue

        if item_type == "blob":
            dest = os.path.join(BASE_DIR, path.replace("/", os.sep))
            try:
                if path == BAT_NAME:
                    new_content = fetch_raw(make_raw_url(path))
                    old_content = b""
                    if os.path.exists(dest):
                        with open(dest, "rb") as f:
                            old_content = f.read()
                    if new_content != old_content:
                        with open(dest, "wb") as f:
                            f.write(new_content)
                        bat_updated = True
                        print(f"  [OK] {path} (ОБНОВЛЕН)")
                    else:
                        print(f"  [--] {path} (без изменений)")
                else:
                    download_file(path, dest)
                    print(f"  [OK] {path}")
                updated += 1
            except Exception as e:
                print(f"  [!]  {path} — ошибка: {e}")

    print()
    print(f"  Обновлено файлов: {updated}")
    print(f"  Пропущено (защищённые): {skipped}")
    print()

    ensure_github_release()

    print()
    print("  Обновление завершено. База данных и файлы пользователей не тронуты.")

    if bat_updated:
        print()
        print("  [!] start SONAR.bat был обновлён.")
        print("  [!] Закрой это окно и запусти start SONAR.bat заново вручную.")
        print()
        # Возвращаем 0 — НЕ запускаем второе окно
        sys.exit(0)

if __name__ == "__main__":
    main()
