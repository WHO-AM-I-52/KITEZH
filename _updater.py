import urllib.request
import urllib.parse
import json
import os
import sys

REPO_OWNER = "WHO-AM-I-52"
REPO_NAME  = "SONAR"
BRANCH     = "main"
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
API_BASE   = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"

BAT_NAME = "start SONAR.bat"

# Папки и файлы, которые НЕ трогаем
PROTECTED = {"db", "uploads", "reports", "WPy", "Bacup",
             "_updater.py", "update.bat", ".env",
             "database.db", "database.db-shm", "database.db-wal"}

def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "SONAR-Updater"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

def fetch_raw(url):
    req = urllib.request.Request(url, headers={"User-Agent": "SONAR-Updater"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()

def make_raw_url(path):
    """URL с кодированием пробелов и спецсимволов в имени файла"""
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

def main():
    print("  Подключаемся к GitHub...")
    try:
        tree = get_tree()
    except Exception as e:
        print(f"  [ОШИБКа] Не удалось получить данные с GitHub: {e}")
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
    print("  Обновление завершено. База данных и файлы пользователей не тронуты.")

    if bat_updated:
        sys.exit(2)

if __name__ == "__main__":
    main()
