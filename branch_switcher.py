# ╔══════════════════════════════════════════════════════════════╗
# ║  branch_switcher.py                                          ║
# ║  Переключение ветки main/dev через веб-интерфейс.            ║
# ║  Запускается как subprocess из settings_routes.py.           ║
# ║  После применения создаёт _restart.flag → авторестарт.       ║
# ╚══════════════════════════════════════════════════════════════╝

import os
import sys
import subprocess

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
BRANCH_FILE  = os.path.join(BASE_DIR, "_branch.txt")
RESTART_FLAG = os.path.join(BASE_DIR, "_restart.flag")
LOCK_FILE    = os.path.join(BASE_DIR, "_updating.lock")
LOG_FILE     = os.path.join(BASE_DIR, "_switch_log.txt")


def switch(target_branch: str):
    """Переключает ветку и запускает _updater.py. Вызывается из subprocess."""
    if target_branch not in ("main", "dev"):
        print(f"[ОШИБКА] Недопустимая ветка: {target_branch}")
        sys.exit(1)

    # Записываем выбранную ветку
    with open(BRANCH_FILE, "w", encoding="utf-8") as f:
        f.write(target_branch)
    print(f"[OK] Ветка переключена на: {target_branch}")

    # Запускаем _updater.py — он прочитает _branch.txt и скачает нужную ветку
    updater_path = os.path.join(BASE_DIR, "_updater.py")
    result = subprocess.run(
        [sys.executable, updater_path],
        cwd=BASE_DIR,
        capture_output=False,
    )

    if result.returncode not in (0, 2):
        # Ошибка — откатываем _branch.txt на предыдущее значение
        prev = "main" if target_branch == "dev" else "dev"
        with open(BRANCH_FILE, "w", encoding="utf-8") as f:
            f.write(prev)
        print(f"[ОШИБКА] _updater.py завершился с кодом {result.returncode}. Ветка откатана на {prev}.")
        sys.exit(result.returncode)

    # Создаём _restart.flag → run_server.py увидит его и вернёт exit(42)
    # → start SONAR.bat перезапустит сервер автоматически
    with open(RESTART_FLAG, "w") as f:
        f.write("restart")
    print("[OK] _restart.flag создан — сервер перезапустится автоматически.")

    # Убираем lock
    if os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)

    sys.exit(0)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Использование: python branch_switcher.py <main|dev>")
        sys.exit(1)
    switch(sys.argv[1])
