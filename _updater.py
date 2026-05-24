# ╔══════════════════════════════════════════════════════════════════════════╗
# ║                         _updater.py                                     ║
# ║  Скачивает обновления SONAR с GitHub одним zip-архивом (1 API-запрос)   ║
# ║  Не трогает БД и файлы пользователя.                                    ║
# ╚══════════════════════════════════════════════════════════════════════════╝

import urllib.request
import urllib.error
import json
import os
import sys
import zipfile
import shutil
import tempfile
from datetime import datetime

REPO_OWNER = "WHO-AM-I-52"
REPO_NAME  = "SONAR"
BRANCH     = "main"
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
API_BASE   = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"

BAT_NAME = "start SONAR.bat"

# ─── Защищённые пути (никогда не перезаписываются) ───────────────────────────
PROTECTED_DIRS  = {"uploads", "reports", "WPy", "Bacup", "db"}
PROTECTED_FILES = {"_updater.py", "update.bat", ".env"}


def should_skip(rel_path: str) -> bool:
    p    = rel_path.replace("\\", "/").strip("/")
    top  = p.split("/")[0]
    base = os.path.basename(p)
    if top in PROTECTED_DIRS or top in PROTECTED_FILES:
        return True
    if base in {"database.db", "database.db-wal", "database.db-shm"}:
        return True
    if "__pycache__" in p or p.endswith(".pyc"):
        return True
    return False


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
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read().decode())
        show_rate_limit(r.headers)
        return data

def post_json(url, payload):
    body = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        url, data=body,
        headers={**_headers(), "Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

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


def _print_progress(downloaded: int, total: int):
    """Выводит прогресс-бар в одну строку."""
    if total > 0:
        pct   = downloaded / total * 100
        filled = int(pct / 5)  # 20 блоков = 100%
        bar   = "█" * filled + "░" * (20 - filled)
        size_kb = downloaded // 1024
        total_kb = total // 1024
        print(f"  [{bar}] {pct:5.1f}%  {size_kb} / {total_kb} КБ", end="\r", flush=True)
    else:
        size_kb = downloaded // 1024
        print(f"  Скачано: {size_kb} КБ...", end="\r", flush=True)


def download_zip(zip_path: str):
    """Скачивает весь репозиторий одним архивом с прогресс-баром."""
    url = f"{API_BASE}/zipball/{BRANCH}"
    req = urllib.request.Request(url, headers=_headers())
    print(f"  Скачиваем архив репозитория...")
    with urllib.request.urlopen(req, timeout=60) as r:
        show_rate_limit(r.headers)
        total = int(r.headers.get("Content-Length", 0))
        downloaded = 0
        chunk_size = 8192
        with open(zip_path, "wb") as f:
            while True:
                chunk = r.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                _print_progress(downloaded, total)
    print()  # перевод строки после прогресс-бара
    size_kb = os.path.getsize(zip_path) // 1024
    print(f"  Архив скачан: {size_kb} КБ")


def extract_and_apply(zip_path: str):
    """Распаковывает архив во временную папку, копирует файлы в BASE_DIR."""
    updated     = 0
    skipped     = 0
    bat_updated = False

    with tempfile.TemporaryDirectory() as tmp_dir:
        print("  Распаковываем архив...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_dir)

        # GitHub кладёт файлы в подпапку вида "WHO-AM-I-52-SONAR-<sha>/"
        entries = os.listdir(tmp_dir)
        if not entries:
            print("  [ОШИБКА] Архив пустой.")
            return 0, 0, False
        repo_root = os.path.join(tmp_dir, entries[0])

        print("  Применяем обновления...")
        for dirpath, dirnames, filenames in os.walk(repo_root):
            rel_dir = os.path.relpath(dirpath, repo_root)

            for fname in filenames:
                if rel_dir == ".":
                    rel_path = fname
                else:
                    rel_path = os.path.join(rel_dir, fname)

                rel_path_fwd = rel_path.replace("\\", "/")

                if should_skip(rel_path_fwd):
                    skipped += 1
                    continue

                src  = os.path.join(dirpath, fname)
                dest = os.path.join(BASE_DIR, rel_path)
                os.makedirs(os.path.dirname(dest), exist_ok=True)

                if rel_path_fwd == BAT_NAME:
                    new_content = open(src, "rb").read()
                    old_content = b""
                    if os.path.exists(dest):
                        old_content = open(dest, "rb").read()
                    if new_content != old_content:
                        shutil.copy2(src, dest)
                        bat_updated = True
                        print(f"  [OK] {rel_path_fwd} (ОБНОВЛЕН)")
                    else:
                        print(f"  [--] {rel_path_fwd} (без изменений)")
                else:
                    shutil.copy2(src, dest)
                    print(f"  [OK] {rel_path_fwd}")

                updated += 1

    return updated, skipped, bat_updated


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
        latest  = cl[0]
        version = latest.get("version", "")
        changes = latest.get("changes", [])
        body    = "\n".join(f"- {c}" for c in changes)
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
            f"{API_BASE}/releases/tags/{tag}", headers=_headers()
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
        print(f"  [Релиз] {tag} успешно создан: {resp.get('html_url', '')}")
    else:
        msg = resp.get("message", "неизвестная ошибка")
        print(f"  [Релиз] Не удалось создать {tag}: {msg}")


def main():
    print("  Подключаемся к GitHub...")
    if TOKEN:
        print("  Токен найден — лимит 5000 запросов/час")
    else:
        print("  Токен не найден — лимит 60 запросов/час")

    zip_path = os.path.join(BASE_DIR, "_sonar_update.zip")

    try:
        download_zip(zip_path)
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
        print(f"  [ОШИБКА] Не удалось скачать архив: {e}")
        sys.exit(1)

    try:
        updated, skipped, bat_updated = extract_and_apply(zip_path)
    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)
            print("  Архив удалён.")

    print()
    print(f"  Обновлено файлов : {updated}")
    print(f"  Пропущено (защита): {skipped}")
    print()

    ensure_github_release()

    print()
    print("  Обновление завершено. База данных и файлы пользователей не тронуты.")

    if bat_updated:
        print()
        print("  [!] start SONAR.bat был обновлён.")
        print("  [!] Закрой это окно и запусти start SONAR.bat заново вручную.")
        print()
        sys.exit(2)


if __name__ == "__main__":
    main()
