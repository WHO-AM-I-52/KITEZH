# ╔════════════════════════════════════════════════════════════════════════╗
# ║                         _updater.py                                     ║
# ║  Скачивает обновления KITEZH с GitHub одним zip-архивом (1 API-запрос)   ║
# ║  Режим --check:         сравнивает SHA и выходит без скачивания          ║
# ║  Режим --force:         перезаписывает ВСЕ файлы, игнорируя сравнение байт    ║
# ║  Режим --download-only: только скачать zip в _kitezh_update.zip        ║
# ║  Режим --apply-only:    применить уже скачанный _kitezh_update.zip       ║
# ║  Режим --stream-json:   JSON-строки прогресса в stdout для SSE-стрима   ║
# ║  Не трогает БД и файлы пользователя.                               ║
# ║  get_commits_between: список коммитов для панели обновлений          ║
# ╚════════════════════════════════════════════════════════════════════════╝

import urllib.request
import urllib.error
import json
import os
import sys
import zipfile
import shutil
import tempfile
from datetime import datetime

REPO_OWNER    = "WHO-AM-I-52"
REPO_NAME     = "KITEZH"
BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
API_BASE      = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"
COMMIT_FILE   = os.path.join(BASE_DIR, "_last_commit.txt")
BRANCH_FILE   = os.path.join(BASE_DIR, "_branch.txt")
ZIP_PATH      = os.path.join(BASE_DIR, "_kitezh_update.zip")
FALLBACK_KB   = 600

# ── Флаг --stream-json: JSON-строки прогресса в stdout для SSE-стрима ────────
STREAM_JSON = "--stream-json" in sys.argv

def _sjson(obj: dict):
    """Выводит JSON-строку в stdout если включён --stream-json.
    Использует flush=True чтобы буфер не задерживал данные.
    Без --stream-json — полный no-op, поведение не меняется.
    """
    if STREAM_JSON:
        print(json.dumps(obj, ensure_ascii=False), flush=True)


# ── Читаем активную ветку из _branch.txt (по умолчанию main) ───────────────────────────────────────────────
def load_branch() -> str:
    if os.path.exists(BRANCH_FILE):
        try:
            val = open(BRANCH_FILE, encoding="utf-8").read().strip()
            if val in ("main", "dev"):
                return val
        except Exception:
            pass
    return "main"

BRANCH = load_branch()

BAT_NAME = "start KITEZH.bat"

# update.bat намеренно НЕ защищён — обновляется автоматически как обычный файл
# _updater.py защищён — самообновление небезопасно во время работы
PROTECTED_DIRS  = {"uploads", "reports", "WPy", "Bacup", "db"}
PROTECTED_FILES = {"_updater.py", ".env"}

SPINNER = ["||", "|/", "--", "\\/"]


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
    h = {"User-Agent": "KITEZH-Updater", "Accept": "application/vnd.github+json"}
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


# ─── Список коммитов между двумя SHA ────────────────────────────────────────────────────────────────────────────────────

def get_commits_between(local_sha: str, remote_sha: str) -> list:
    """Возвращает список коммитов между local_sha и remote_sha (до 20 шт.).
    Каждый элемент: {'sha': str, 'message': str, 'date': str}.
    Используется панелью обновлений в changelog.html.
    """
    try:
        data = get_json(f"{API_BASE}/compare/{local_sha}...{remote_sha}")
        commits = []
        for c in data.get("commits", [])[:20]:
            msg      = c.get("commit", {}).get("message", "").split("\n")[0]
            sha      = c.get("sha", "")[:7]
            date_raw = c.get("commit", {}).get("author", {}).get("date", "")
            date_str = date_raw[:10] if date_raw else ""
            commits.append({"sha": sha, "message": msg, "date": date_str})
        commits.reverse()  # новые сверху
        return commits
    except Exception as e:
        print(f"  [Внимание] Не удалось получить список коммитов: {e}")
        return []


# ─── Проверка обновлений по SHA ───────────────────────────────────────────────────────────────────

def get_remote_sha() -> str | None:
    try:
        data = get_json(f"{API_BASE}/commits/{BRANCH}")
        return data.get("sha", "")
    except Exception as e:
        print(f"  [ОШИБКА] Не удалось получить SHA с GitHub: {e}")
        return None

def load_local_sha() -> str:
    if os.path.exists(COMMIT_FILE):
        try:
            return open(COMMIT_FILE, encoding="utf-8").read().strip()
        except Exception:
            pass
    return ""

def save_local_sha(sha: str):
    try:
        with open(COMMIT_FILE, "w", encoding="utf-8") as f:
            f.write(sha)
    except Exception as e:
        print(f"  [Внимание] Не удалось сохранить SHA: {e}")


def check_for_updates() -> int:
    print()
    print("  ================================================")
    print(f"   KITEZH - Проверка обновлений (ветка: {BRANCH})")
    print("  ================================================")
    print()
    print("  Подключаемся к GitHub...")
    if TOKEN:
        print("  Токен найден — лимит 5000 запросов/час")
    else:
        print("  Токен не найден — лимит 60 запросов/час")
    print()

    remote_sha = get_remote_sha()
    if remote_sha is None:
        return 2

    local_sha = load_local_sha()

    if not local_sha:
        print("  Локальная версия не определена — рекомендуется скачать архив обновления.")
        print(f"  Последний коммит GitHub: {remote_sha[:12]}...")
        return 1

    if remote_sha == local_sha:
        print(f"  Актуальная версия: {remote_sha[:12]}...")
        print("  Обновлений нет.")
        return 0
    else:
        print(f"  Локальная версия : {local_sha[:12]}...")
        print(f"  GitHub версия    : {remote_sha[:12]}...")
        print("  Доступны обновления!")
        return 1


# ─── Размер архива ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def get_zip_size_kb() -> int:
    url = f"{API_BASE}/zipball/{BRANCH}"
    try:
        req = urllib.request.Request(url, headers=_headers(), method="HEAD")
        with urllib.request.urlopen(req, timeout=15) as r:
            cl = r.headers.get("Content-Length")
            if cl and int(cl) > 0:
                return int(cl) // 1024
    except Exception:
        pass
    try:
        req = urllib.request.Request(API_BASE, headers=_headers())
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
            size_kb = data.get("size", 0)
            if size_kb > 0:
                return max(int(size_kb * 0.65), 50)
    except Exception:
        pass
    return FALLBACK_KB


def _print_progress(downloaded: int, estimated_kb: int, spinner_idx: int):
    size_kb = downloaded // 1024
    if estimated_kb > 0 and downloaded <= estimated_kb * 1024:
        pct    = downloaded / (estimated_kb * 1024) * 100
        filled = int(pct / 5)
        bar    = "█" * filled + "░" * (20 - filled)
        # Текстовый прогресс в консоль (bat-окно)
        print(f"  [{bar}] {pct:4.0f}%  {size_kb} / ~{estimated_kb} КБ", end="\r", flush=True)
        # JSON-прогресс для SSE-стрима
        _sjson({
            "type":          "download_pct",
            "pct":           round(min(pct, 100), 1),
            "downloaded_mb": round(downloaded / 1048576, 2),
            "total_mb":      round(estimated_kb / 1024, 2),
        })
    else:
        spin = SPINNER[spinner_idx % len(SPINNER)]
        print(f"  [{spin}] Скачано: {size_kb} КБ...", end="\r", flush=True)
        # Если размер неизвестен — отдаём pct=-1 как сигнал «неопределённо»
        _sjson({
            "type":          "download_pct",
            "pct":           -1,
            "downloaded_mb": round(downloaded / 1048576, 2),
            "total_mb":      0,
        })


def download_zip(zip_path: str):
    print(f"  Определяем размер архива обновления (ветка: {BRANCH})...")
    estimated_kb = get_zip_size_kb()
    print(f"  Ожидаемый размер архива: ~{estimated_kb} КБ")

    url = f"{API_BASE}/zipball/{BRANCH}"
    req = urllib.request.Request(url, headers=_headers())
    print("  Скачиваем архив обновления...")
    with urllib.request.urlopen(req, timeout=60) as r:
        show_rate_limit(r.headers)
        cl = r.headers.get("Content-Length")
        if cl and int(cl) > 0:
            estimated_kb = int(cl) // 1024
            print(f"  Точный размер архива: {estimated_kb} КБ")
        downloaded  = 0
        spinner_idx = 0
        chunk_size  = 8192
        with open(zip_path, "wb") as f:
            while True:
                chunk = r.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded  += len(chunk)
                spinner_idx += 1
                _print_progress(downloaded, estimated_kb, spinner_idx)
    print()
    size_kb = os.path.getsize(zip_path) // 1024
    print(f"  Архив обновления скачан: {size_kb} КБ")
    # Финальный 100% после завершения скачивания
    _sjson({
        "type":          "download_pct",
        "pct":           100,
        "downloaded_mb": round(size_kb / 1024, 2),
        "total_mb":      round(size_kb / 1024, 2),
    })


def extract_and_apply(zip_path: str, force: bool = False):
    """Распаковывает архив, копирует только изменившиеся файлы.
    force=True — перезаписывает ВСЕ файлы (кроме защищённых), не сравнивая содержимое.
    При STREAM_JSON=True пишет JSON-строки прогресса в stdout.
    """
    updated     = 0
    unchanged   = 0
    skipped     = 0
    errors      = 0
    bat_updated = False

    if force:
        print("  [FORCE] Режим принудительного обновления — все файлы будут перезаписаны.")

    with tempfile.TemporaryDirectory() as tmp_dir:
        print("  Распаковываем архив обновления...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_dir)

        entries = os.listdir(tmp_dir)
        if not entries:
            print("  [ОШИБКА] Архив пустой.")
            return 0, 0, 0, False
        repo_root = os.path.join(tmp_dir, entries[0])

        # ── Предварительный подсчёт файлов для прогресс-бара установки ──
        all_files = []
        for dirpath, dirnames, filenames in os.walk(repo_root):
            for fname in filenames:
                rel_dir  = os.path.relpath(dirpath, repo_root)
                rel_path = fname if rel_dir == "." else os.path.join(rel_dir, fname)
                all_files.append((dirpath, fname, rel_path))
        total_files = max(len(all_files), 1)  # защита от деления на ноль

        print("  Применяем обновления...")
        processed = 0
        for dirpath, fname, rel_path in all_files:
            processed += 1
            rel_path_fwd = rel_path.replace("\\", "/")

            if should_skip(rel_path_fwd):
                skipped += 1
                _sjson({"type": "apply_file", "status": "skipped", "path": rel_path_fwd})
                _sjson({
                    "type":    "apply_pct",
                    "pct":     round(processed / total_files * 100, 1),
                    "current": processed,
                    "total":   total_files,
                })
                continue

            src  = os.path.join(dirpath, fname)
            dest = os.path.join(BASE_DIR, rel_path)

            try:
                os.makedirs(os.path.dirname(dest), exist_ok=True)

                new_content = open(src, "rb").read()
                old_content = b""
                if os.path.exists(dest):
                    old_content = open(dest, "rb").read()

                if not force and new_content == old_content:
                    print(f"  [--] {rel_path_fwd}")
                    unchanged += 1
                    _sjson({"type": "apply_file", "status": "unchanged", "path": rel_path_fwd})
                else:
                    shutil.copy2(src, dest)
                    updated += 1
                    if rel_path_fwd == BAT_NAME:
                        bat_updated = True
                        print(f"  [OK] {rel_path_fwd} (ОБНОВЛЕН)")
                    else:
                        label = "(FORCE)" if force and new_content == old_content else ""
                        print(f"  [OK] {rel_path_fwd} {label}".rstrip())
                    _sjson({"type": "apply_file", "status": "updated", "path": rel_path_fwd})

            except Exception as e:
                errors += 1
                print(f"  [!!] {rel_path_fwd} — ошибка: {e}")
                _sjson({"type": "apply_file", "status": "error", "path": rel_path_fwd})

            _sjson({
                "type":    "apply_pct",
                "pct":     round(processed / total_files * 100, 1),
                "current": processed,
                "total":   total_files,
            })

    return updated, unchanged, skipped, bat_updated


def load_changelog():
    """Читает CHANGELOG из changelog.py через exec в изолированном namespace.
    Устойчиво к комментариям, любым кавычкам и форматированию файла.
    """
    changelog_path = os.path.join(BASE_DIR, "changelog.py")
    if not os.path.exists(changelog_path):
        return None, None
    try:
        ns: dict = {}
        with open(changelog_path, encoding="utf-8") as f:
            code = f.read()
        exec(compile(code, changelog_path, "exec"), ns, ns)

        cl = ns.get("CHANGELOG")
        if not cl:
            return None, None

        latest  = cl[0]
        version = latest.get("version", "")
        body    = "\n".join(f"- {c}" for c in latest.get("changes", []))
        return version, body

    except Exception as e:
        print(f"  [Внимание] Не удалось прочитать changelog.py: {e}")
        return None, None


def ensure_github_release():
    if not TOKEN:
        print("  [Релиз] Токен не найден — автосоздание релиза пропущено.")
        return

    if BRANCH != "main":
        print(f"  [Релиз] Ветка {BRANCH} — автосоздание релиза пропущено.")
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


def run_sync_changelog():
    """Синхронизирует changelog.py с GitHub Releases после обновления."""
    sync_path = os.path.join(BASE_DIR, "sync_changelog.py")
    if not os.path.exists(sync_path):
        print("  [Changelog] sync_changelog.py не найден — пропуск.")
        return
    print("  Синхронизация changelog с GitHub...")
    try:
        import importlib.util
        spec   = importlib.util.spec_from_file_location("sync_changelog", sync_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.main()
    except Exception as e:
        print(f"  [Changelog] Ошибка синхронизации: {e}")


# ─── Режим --download-only ────────────────────────────────────────────────────────────────────────────────────────────
# Только скачивает zip-архив в ZIP_PATH; не применяет файлы.
# Выход: 0 = успех, 1 = ошибка
def _cmd_download_only():
    print("  Подключаемся к GitHub...")
    if TOKEN:
        print("  Токен найден — лимит 5000 запросов/час")
    else:
        print("  Токен не найден — лимит 60 запросов/час")
    print(f"  Активная ветка: {BRANCH}")
    try:
        download_zip(ZIP_PATH)
        print("  Архив готов к установке.")
        sys.exit(0)
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
            print(f"  [ОШИБКА] HTTP {e.code}: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"  [ОШИБКА] Не удалось скачать архив: {e}")
        sys.exit(1)


# ─── Режим --apply-only ─────────────────────────────────────────────────────────────────────────────────────────────
# Применяет уже скачанный ZIP_PATH; удаляет архив после установки.
# Выход: 0 = успех, 1 = ошибка, 2 = успех + обновлён bat (нужен ручной рестарт)
def _cmd_apply_only(force: bool = False):
    if not os.path.exists(ZIP_PATH):
        print(f"  [ОШИБКА] Архив {ZIP_PATH} не найден. Сначала выполни --download-only.")
        sys.exit(1)

    remote_sha = load_local_sha()  # SHA был сохранён при скачивании нет; читаем с GitHub
    # Для --apply-only SHA считываем заново (zip уже скачан, но SHA надо сохранить)
    remote_sha = get_remote_sha()

    apply_ok = False
    try:
        updated, unchanged, skipped, bat_updated = extract_and_apply(ZIP_PATH, force=force)
        apply_ok = True
    except Exception as e:
        print(f"  [ОШИБКА] Не удалось применить обновление: {e}")
        sys.exit(1)
    finally:
        if os.path.exists(ZIP_PATH):
            os.remove(ZIP_PATH)
            print("  Архив обновления удалён.")

    if apply_ok and remote_sha:
        save_local_sha(remote_sha)
        print(f"  Версия сохранена: {remote_sha[:12]}...")

    print()
    print(f"  Обновлено файлов     : {updated}")
    print(f"  Без изменений        : {unchanged}")
    print(f"  Пропущено (защита)   : {skipped}")
    print()

    # JSON-итог для SSE-стрима (отправляется до ensure_github_release чтобы UI получил раньше)
    _sjson({
        "type":      "done",
        "updated":   updated,
        "unchanged": unchanged,
        "skipped":   skipped,
        "errors":    0,
        "message":   f"Обновлено: {updated} | Без изменений: {unchanged} | Пропущено: {skipped}",
    })

    ensure_github_release()
    run_sync_changelog()

    mode_label = " [FORCE]" if force else ""
    print(f"  Обновление завершено{mode_label} (ветка: {BRANCH}). База данных и файлы пользователей не тронуты.")

    if bat_updated:
        print()
        print("  [!] start KITEZH.bat был обновлён.")
        print("  [!] Требуется перезапуск через start KITEZH.bat.")
        print()
        sys.exit(2)

    sys.exit(0)


def main():
    force_mode = "--force" in sys.argv

    if "--check" in sys.argv:
        code = check_for_updates()
        sys.exit(code)

    if "--download-only" in sys.argv:
        _cmd_download_only()
        return  # sys.exit внутри

    if "--apply-only" in sys.argv:
        _cmd_apply_only(force=force_mode)
        return  # sys.exit внутри

    # ── Обычный режим: скачать + применить за один запуск ──
    print("  Подключаемся к GitHub...")
    if TOKEN:
        print("  Токен найден — лимит 5000 запросов/час")
    else:
        print("  Токен не найден — лимит 60 запросов/час")
    print(f"  Активная ветка: {BRANCH}")
    if force_mode:
        print("  [FORCE] Принудительное обновление: все файлы будут перезаписаны.")

    remote_sha = get_remote_sha()

    try:
        download_zip(ZIP_PATH)
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
        print(f"  [ОШИБКа] Не удалось скачать архив обновления: {e}")
        sys.exit(1)

    apply_ok = False
    try:
        updated, unchanged, skipped, bat_updated = extract_and_apply(ZIP_PATH, force=force_mode)
        apply_ok = True
    except Exception as e:
        print(f"  [ОШИБКА] Не удалось применить обновление: {e}")
        sys.exit(1)
    finally:
        if os.path.exists(ZIP_PATH):
            os.remove(ZIP_PATH)
            print("  Архив обновления удалён.")

    if apply_ok and remote_sha:
        save_local_sha(remote_sha)
        print(f"  Версия сохранена: {remote_sha[:12]}...")

    print()
    print(f"  Обновлено файлов     : {updated}")
    print(f"  Без изменений        : {unchanged}")
    print(f"  Пропущено (защита)   : {skipped}")
    print()

    ensure_github_release()
    run_sync_changelog()

    print()
    mode_label = " [FORCE]" if force_mode else ""
    print(f"  Обновление завершено{mode_label} (ветка: {BRANCH}). База данных и файлы пользователей не тронуты.")

    if bat_updated:
        print()
        print("  [!] start KITEZH.bat был обновлён.")
        print("  [!] Закрой это окно и запусти start KITEZH.bat заново вручную.")
        print()
        sys.exit(2)


if __name__ == "__main__":
    main()
