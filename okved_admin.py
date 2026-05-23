# ╔══════════════════════════════════════════════════════════════╗
# ║                       okved_admin.py                         ║
# ║  Админка ОКВЭД:                                              ║
# ║  - загрузка справочника ОКВЭД из CSV                        ║
# ║  - синхронизация с API ФНС «Мой ОКВЭД»                       ║
# ╚══════════════════════════════════════════════════════════════╝

from flask import Blueprint, request, redirect, url_for, flash
from werkzeug.utils import secure_filename
import csv
import io
from datetime import datetime

import requests  # HTTP-запросы к API ФНС

from db import get_db
from auth_utils import login_required, admin_required


# ─── НАСТРОЙКИ BLUEPRINT И API ───────────────────────────────────────────────

# Админский раздел для работы со справочником ОКВЭД
okved_bp = Blueprint('okved', __name__, url_prefix='/admin/okved')

# Базовый URL публичного API ФНС «Мой ОКВЭД»
BASE_OKVED_API = "https://service.nalog.ru/okved/api/v1"
# Таймаут HTTP-запросов к ФНС, секунд
OKVED_TIMEOUT = 15


# ─── ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ─────────────────────────────────────────────────

def set_setting(conn, key, value):
    """
    Сохраняет значение настройки в таблице settings.
    Если ключ уже существует — обновляет его.
    Используем для хранения времени последней синхронизации ОКВЭД.
    """
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value)
    )


def fetch_okved_reference():
    """
    Получает актуальный полный справочник ОКВЭД из API ФНС «Мой ОКВЭД».

    Использует метод:
      GET /reference/okveds

    Возвращает список словарей:
      [{group, code, name, description?, selectable?}, ...]
    """
    url = f"{BASE_OKVED_API}/reference/okveds"
    resp = requests.get(url, timeout=OKVED_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data.get("okveds", [])


def normalize_okved_item(item):
    """
    Приводит запись из API ФНС к формату нашей таблицы okved.
    Используем поля:
      - code       → okved.code
      - name       → okved.name
      - group      → okved.parent_code (группа/раздел)
      - is_active  → всегда 1 (активен)
    """
    code = (item.get("code") or "").strip()
    name = (item.get("name") or "").strip()
    group = (item.get("group") or "").strip() or None

    if not code or not name:
        return None

    return {
        "code": code,
        "name": name,
        "parent_code": group,
        "is_active": 1,
    }


def sync_okveds_from_fns():
    """
    Полная перезаливка таблицы okved из API ФНС «Мой ОКВЭД».

    Шаги:
      1. Получаем справочник okveds из ФНС.
      2. Очищаем текущую таблицу okved.
      3. Вставляем новые записи.
      4. Обновляем settings.okved_last_sync.
    """
    okveds_raw = fetch_okved_reference()
    rows = []
    for it in okveds_raw:
        norm = normalize_okved_item(it)
        if norm:
            rows.append(norm)

    conn = get_db()
    cur = conn.cursor()

    try:
        # Полностью очищаем старый справочник
        cur.execute("DELETE FROM okved")

        # Вставляем новые записи
        cur.executemany("""
            INSERT INTO okved (code, name, parent_code, is_active)
            VALUES (:code, :name, :parent_code, :is_active)
        """, rows)

        # Обновляем timestamp синхронизации
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        set_setting(conn, 'okved_last_sync', now)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return len(rows)


# ─── ROUTE: СИНХРОНИЗАЦИЯ С ФНС «МОЙ ОКВЭД» ──────────────────────────────────

@okved_bp.route('/sync-my-okved', methods=['POST'])
@login_required
@admin_required
def okved_sync_my_okved():
    """
    Обработчик кнопки в админке:
    подтягивает полный справочник ОКВЭД из ФНС «Мой ОКВЭД»
    и заменяет им локальную таблицу okved.
    """
    try:
        count = sync_okveds_from_fns()
        flash(f'Справочник ОКВЭД обновлён из ФНС. Загружено записей: {count}', 'success')
    except Exception as e:
        flash(f'Ошибка при синхронизации ОКВЭД из ФНС: {e}', 'error')

    return redirect(url_for('admin.classifiers'))


# ─── ROUTE: ЗАГРУЗКА СПРАВОЧНИКА ИЗ CSV ──────────────────────────────────────

@okved_bp.route('/upload', methods=['POST'])
@login_required
@admin_required
def okved_upload():
    """
    Ручная загрузка файла с ОКВЭД (CSV).

    Ожидается CSV в UTF-8 с разделителем «;» и колонками:
      code;name;parent_code (заголовок первой строки можно игнорировать).

    При загрузке:
      - текущий классификатор полностью очищается;
      - все записи из файла вставляются в таблицу okved;
      - обновляется settings.okved_last_sync.
    """
    file = request.files.get('file')
    if not file or not file.filename:
        flash('Файл не выбран', 'error')
        return redirect(url_for('admin.classifiers'))

    filename = secure_filename(file.filename)
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in ('csv',):
        flash('Ожидается CSV-файл с ОКВЭД (формат CSV)', 'error')
        return redirect(url_for('admin.classifiers'))

    conn = None
    try:
        # Читаем как текст, предполагаем UTF-8
        stream = io.StringIO(file.stream.read().decode('utf-8', errors='ignore'))
        reader = csv.reader(stream, delimiter=';', quotechar='"')

        conn = get_db()
        cur = conn.cursor()

        # Полностью очищаем старый классификатор
        cur.execute("DELETE FROM okved")

        inserted = 0
        # Пытаемся пропустить строку заголовка, если там не цифры в первом столбце
        for row in reader:
            if not row:
                continue
            code = (row[0] or '').strip()
            name = (row[1] or '').strip() if len(row) > 1 else ''
            parent_code = (row[2] or '').strip() if len(row) > 2 else None

            # Пропускаем строку заголовка (если первый код не похож на код ОКВЭД)
            if inserted == 0 and (not code or not any(ch.isdigit() for ch in code)):
                continue

            if not code or not name:
                continue

            cur.execute(
                "INSERT INTO okved (code, name, parent_code, is_active) VALUES (?,?,?,1)",
                (code, name, parent_code)
            )
            inserted += 1

        # Обновляем timestamp синхронизации
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        set_setting(conn, 'okved_last_sync', now)

        conn.commit()
        flash(f'Классификатор ОКВЭД обновлён. Загружено записей: {inserted}', 'success')
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        flash(f'Ошибка при загрузке ОКВЭД: {e}', 'error')
    finally:
        if conn is not None:
            conn.close()

    return redirect(url_for('admin.classifiers'))