import urllib.request
import json
import os
import shutil
import sys

REPO_OWNER = "WHO-AM-I-52"
REPO_NAME = "SONAR"
BRANCH = "main"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
API_BASE = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"

# Папки и файлы, которые НЕ трогаем
PROTECTED = {"db", "uploads", "reports", "WPy", "Bacup",
             "_updater.py", "update.bat", ".env",
             "database.db", "database.db-shm", "database.db-wal"}

def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "SONAR-Updater"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

def download_file(download_url, dest_path):
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    req = urllib.request.Request(download_url, headers={"User-Agent": "SONAR-Updater"})
    with urllib.request.urlopen(req, timeout=15) as r:
        with open(dest_path, "wb") as f:
            f.write(r.read())

def get_tree():
    data = get_json(f"{API_BASE}/git/trees/{BRANCH}?recursive=1")
    return data.get("tree", [])

def main():
    print("  Подключаемся к GitHub...")
    try:
        tree = get_tree()
    except Exception as e:
        print(f"  [ОШИБКА] Не удалось получить данные с GitHub: {e}")
        print("  Проверьте подключение к интернету.")
        sys.exit(1)

    updated = 0
    skipped = 0

    for item in tree:
        path = item["path"]
        item_type = item["type"]

        top_level = path.split("/")[0]
        if top_level in PROTECTED:
            skipped += 1
            continue

        if "__pycache__" in path or path.endswith(".pyc"):
            continue

        if item_type == "blob":
            dest = os.path.join(BASE_DIR, path.replace("/", os.sep))
            download_url = (
                f"https://raw.githubusercontent.com/"
                f"{REPO_OWNER}/{REPO_NAME}/{BRANCH}/{path}"
            )
            try:
                download_file(download_url, dest)
                print(f"  [OK] {path}")
                updated += 1
            except Exception as e:
                print(f"  [!]  {path} — ошибка: {e}")

    print()
    print(f"  Обновлено файлов: {updated}")
    print(f"  Пропущено (защищённые): {skipped}")
    print()
    print("  Обновление завершено. База данных и файлы пользователей не тронуты."

if __name__ == "__main__":
    main()
