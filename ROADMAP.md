# SONAR — Дорожная карта / Roadmap

> Актуальное состояние планов разработки.  
> Статусы: `🔄 в работе` · `📋 запланировано` · `💡 идея` · `✅ готово`

---

## ✅ Готово

### v2.3.3 — Hover-popover + hotfix Bootstrap (25.05.2026)
- Превью обращения при наведении на строку таблицы (номер, заявитель, статус, контакт, телефон)
- API-маршрут `/api/request/<id>/preview` — Blueprint `preview_bp`
- Исправлен TypeError в Bootstrap `tooltip.js:534` (`_isWithActiveTrigger`)

### v2.3.2 — SettingsMenu (25.05.2026)
- Страница `/settings` с настройками профиля пользователя
- Смена пароля с проверкой текущего
- Выбор темы оформления: Светлая / Тёмная / Системная
- Email-уведомления вкл/выкл + поле адреса
- Blueprint `settings_routes.py`, миграция БД (колонки `email`, `theme`, `email_notifications`)

### v2.3.1 — Unseen changelog badge
- Красный бейдж NEW на пункте «Версии» в сайдбаре
- Хранение факта просмотра в localStorage

### v2.2.x — UX: поиск, онлайн, темы, hotfix
- Глобальный поиск Ctrl+Shift+F
- Счётчик пользователей онлайн
- Тема GAMMA
- Inline diff в истории изменений

---

## 📋 Запланировано

### v2.4 — OCR и автозаполнение (июнь 2026)
- Автозаполнение полей формы по скану анкеты (PDF/DOCX)
- Поддержка форматов: PDF, DOCX, JPG, PNG
- Просмотр результатов распознавания перед сохранением

### v2.5 — Уведомления на почту (июль 2026)
- Уведомления на почту при назначении обращения
- Напоминания о просроченных обращениях
- Настройка уведомлений в профиле пользователя

### Автообновление в интерфейсе
- Встроенная проверка новых версий при запуске
- Автоматическое обновление кода из GitHub одной кнопкой
- Отображение текущей и доступной версии в интерфейсе

---

## 💡 Идеи

### v3.0 — Автоматический подбор земельных участков под анкеты

**Концепция:**  
При создании или обновлении обращения система автоматически анализирует параметры заявителя
(район, площадь, ОКВЭД, тип производства, инфраструктурные требования) и предлагает подходящие
участки из базы земельных площадок НО прямо в карточке обращения.

**Логика работы:**
1. Заявитель заполняет анкету (preferred_districts, площадь, тип деятельности, ОКВЭД)
2. Система запускает алгоритм подбора по таблице `land_plots` (будущая БД участков)
3. В карточке обращения появляется блок «Рекомендуемые участки» с топ-5 совпадениями
4. Менеджер может одним кликом привязать участок к обращению
5. История привязок сохраняется в `activity_log`

**Необходимые изменения в БД:**
```sql
-- Таблица земельных участков
CREATE TABLE land_plots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cadastral_num   TEXT,                        -- кадастровый номер
    district        TEXT,                        -- район НО
    area_ha         REAL,                        -- площадь, га
    address         TEXT,
    owner_type      TEXT,                        -- муниципальная / региональная / частная
    allowed_okved   TEXT,                        -- разрешённые виды деятельности (JSON)
    infrastructure  TEXT,                        -- газ, вода, электричество, ж/д (JSON)
    status          TEXT DEFAULT 'free',         -- free / reserved / occupied
    lat             REAL,                        -- координаты для карты
    lon             REAL,
    description     TEXT,
    created_at      TEXT,
    updated_at      TEXT
);

-- Привязка участков к обращениям
CREATE TABLE request_plots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  INTEGER REFERENCES requests(id) ON DELETE CASCADE,
    plot_id     INTEGER REFERENCES land_plots(id),
    assigned_by INTEGER REFERENCES users(id),
    assigned_at TEXT,
    note        TEXT
);
```

**Алгоритм подбора (`plot_matcher.py`):**
```python
def find_plots(conn, request: dict, limit: int = 5) -> list:
    """
    Подбирает участки под параметры обращения.
    request — словарь полей из таблицы requests.
    """
    district = request.get('preferred_districts', '')
    okved    = request.get('applicant_okved_main', '')

    rows = conn.execute("""
        SELECT *, (
            CASE WHEN district LIKE ? THEN 10 ELSE 0 END +
            CASE WHEN allowed_okved LIKE ?  THEN 5  ELSE 0 END +
            CASE WHEN status = 'free'       THEN 3  ELSE 0 END
        ) AS score
        FROM land_plots
        WHERE status != 'occupied'
        ORDER BY score DESC, area_ha ASC
        LIMIT ?
    """, (f'%{district}%', f'%{okved}%', limit)).fetchall()

    return [dict(r) for r in rows]
```

**Интеграция в `request_routes.py`:**
```python
from plot_matcher import find_plots

# В view_request() добавить:
suggested_plots = find_plots(conn, dict(req), limit=5)
return render_template('view.html', ..., suggested_plots=suggested_plots)
```

---

### v3.1 — ИИ-поиск по участкам (Perplexity Sonar API)

**Концепция:**  
Интеграция с Perplexity Sonar API позволяет менеджеру задавать вопросы на естественном языке —
например: *«Есть ли свободные участки под пищевое производство в Борском районе от 2 га?»* —
и получать умный ответ. На первом этапе поиск ведётся по открытым источникам, на втором —
по внутренней базе `land_plots`.

**Этап 1 — поиск по вебу через Sonar API:**

Создать файл `ai_search.py` в корне проекта:

```python
# ai_search.py
# ИИ-поиск по земельным участкам через Perplexity Sonar API
import requests as rq
import os

PPLX_API_KEY = os.getenv("PPLX_API_KEY", "")   # задать в .env или конфиге
PPLX_MODEL   = "sonar-pro"                       # или "sonar" для экономии


def ai_search_land(query: str, context: str = "") -> dict:
    """
    Отправляет запрос к Perplexity Sonar API.
    query   — вопрос пользователя на естественном языке
    context — дополнительный контекст (например, список участков из БД)
    Возвращает: {"answer": str, "citations": list}
    """
    system_prompt = (
        "Ты ассистент по земельным участкам и инвестиционным площадкам "
        "Нижегородской области. Отвечай кратко, структурированно, на русском языке. "
        "Если есть данные из внутренней базы — используй их в первую очередь."
    )
    if context:
        system_prompt += f"\n\nДанные из внутренней базы участков:\n{context}"

    payload = {
        "model": PPLX_MODEL,
        "messages": [
            {"role": "system",  "content": system_prompt},
            {"role": "user",    "content": query}
        ],
        "max_tokens": 1024,
        "temperature": 0.2,
        "return_citations": True,
    }

    resp = rq.post(
        "https://api.perplexity.ai/chat/completions",
        headers={
            "Authorization": f"Bearer {PPLX_API_KEY}",
            "Content-Type":  "application/json",
        },
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    return {
        "answer":    data["choices"][0]["message"]["content"],
        "citations": data.get("citations", []),
    }
```

**Этап 2 — поиск с контекстом из внутренней БД:**

```python
def ai_search_with_db(conn, query: str) -> dict:
    """
    Подтягивает свободные участки из БД и передаёт их как контекст в Sonar API.
    """
    plots = conn.execute(
        "SELECT cadastral_num, district, area_ha, address, allowed_okved, infrastructure "
        "FROM land_plots WHERE status='free' ORDER BY area_ha"
    ).fetchall()

    context_lines = []
    for p in plots:
        context_lines.append(
            f"- [{p['cadastral_num']}] {p['district']}, {p['area_ha']} га, "
            f"адрес: {p['address']}, ОКВЭД: {p['allowed_okved']}, "
            f"инфраструктура: {p['infrastructure']}"
        )
    context = "\n".join(context_lines) if context_lines else "База участков пока пуста."

    return ai_search_land(query, context=context)
```

**Flask-маршрут для поиска (`search_routes.py`):**

```python
from ai_search import ai_search_with_db

@search_bp.route('/api/ai-land-search', methods=['POST'])
@login_required
def ai_land_search():
    query = request.json.get('query', '').strip()
    if not query:
        return jsonify({"error": "Пустой запрос"}), 400
    conn = get_db()
    try:
        result = ai_search_with_db(conn, query)
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500
    conn.close()
    log_action(conn_log := get_db(), session['user_id'],
               'ai_search', None, f'ИИ-запрос: {query[:100]}')
    conn_log.commit(); conn_log.close()
    return jsonify(result)
```

**Переменные окружения (добавить в `.env` или `config.py`):**
```
PPLX_API_KEY=pplx-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

---

### v3.2 — Telegram-уведомления (Вариант А)

**Концепция:**  
Односторонний канал: SONAR → Telegram. Бот отправляет уведомления в рабочий канал/группу
при ключевых событиях с заявками, публикует сводки из dashboard и анонсирует новые версии.
Все отправки фиксируются в `activity_log` с типом `tg_notify`.

**Шаг 1 — создать `telegram_notify.py`:**

```python
# telegram_notify.py
# Модуль отправки уведомлений в Telegram
import requests as rq
import os
import logging

BOT_TOKEN = os.getenv("TG_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TG_CHAT_ID", "")    # id канала: -100xxxxxxxxxx
logger    = logging.getLogger(__name__)


def tg(text: str, parse_mode: str = "HTML") -> bool:
    """
    Отправляет сообщение в Telegram-канал.
    Возвращает True при успехе, False при ошибке (не роняет приложение).
    """
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("Telegram: BOT_TOKEN или CHAT_ID не заданы — пропуск отправки")
        return False
    try:
        resp = rq.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id":    CHAT_ID,
                "text":       text,
                "parse_mode": parse_mode,
            },
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return False


# ── Готовые шаблоны уведомлений ────────────────────────────────────────

def notify_new_request(applicant: str, req_id: int):
    tg(f"🆕 <b>Новое обращение</b>\n"
       f"Заявитель: {applicant}\n"
       f"🔗 <a href='http://localhost:5000/view/{req_id}'>Открыть</a>")

def notify_accepted(num: str, assigned_name: str, req_id: int):
    tg(f"✅ <b>Принято в работу</b>\n"
       f"Номер: <code>{num}</code>\n"
       f"Ответственный: {assigned_name}\n"
       f"🔗 <a href='http://localhost:5000/view/{req_id}'>Открыть</a>")

def notify_rejected(num: str, comment: str, req_id: int):
    tg(f"↩️ <b>Возврат на доработку</b>\n"
       f"Номер: <code>{num}</code>\n"
       f"Причина: {comment}\n"
       f"🔗 <a href='http://localhost:5000/view/{req_id}'>Открыть</a>")

def notify_answered(num: str, method: str, req_id: int):
    tg(f"📬 <b>Ответ зафиксирован</b>\n"
       f"Номер: <code>{num}</code>\n"
       f"Способ: {method}\n"
       f"🔗 <a href='http://localhost:5000/view/{req_id}'>Открыть</a>")

def notify_release(version: str, changelog: str):
    text = changelog[:3000] + ("…" if len(changelog) > 3000 else "")
    tg(f"🚀 <b>SONAR {version} — новая версия</b>\n\n{text}")

def notify_overdue(count: int):
    tg(f"⚠️ <b>Просроченные обращения</b>\n"
       f"Необработанных более 7 дней: <b>{count}</b>\n"
       f"🔗 <a href='http://localhost:5000/?quick=overdue'>Список</a>")
```

**Шаг 2 — добавить тип в `activity_log.py`:**

```python
# Добавить в ACTION_LABELS:
'tg_notify': 'Уведомление в Telegram',
```

**Шаг 3 — вызовы в `request_routes.py`:**

```python
from telegram_notify import (
    notify_new_request, notify_accepted,
    notify_rejected, notify_answered
)
from activity_log import log_action

# После INSERT нового обращения:
notify_new_request(applicant, new_id)
log_action(conn, session['user_id'], 'tg_notify', new_id,
           f'Уведомление TG: новое обращение от {applicant}')

# После accept:
notify_accepted(num, assigned_name, rid)
log_action(conn, session['user_id'], 'tg_notify', rid,
           f'Уведомление TG: принято {num}')

# После reject:
notify_rejected(num, comment, rid)
log_action(conn, session['user_id'], 'tg_notify', rid,
           f'Уведомление TG: возврат {num}')

# После answer:
notify_answered(num, method, rid)
log_action(conn, session['user_id'], 'tg_notify', rid,
           f'Уведомление TG: ответ по {num}')
```

**Шаг 4 — сводка из dashboard по расписанию:**

```python
# scheduler.py — запускается рядом с Flask
import schedule
import time
from dashboard import build_dash
from db import get_db
from telegram_notify import tg

def send_daily_dash():
    conn = get_db()
    d    = build_dash(conn, 'month')
    conn.close()
    tg(
        f"📊 <b>Сводка SONAR — {d.get('period_label','месяц')}</b>\n"
        f"Всего заявок:    {d.get('total', '—')}\n"
        f"Новые:           {d.get('new', '—')}\n"
        f"В работе:        {d.get('accepted', '—')}\n"
        f"Отвечено:        {d.get('answered', '—')}\n"
        f"Просрочено:      {d.get('overdue', '—')}"
    )

schedule.every().day.at("09:00").do(send_daily_dash)

if __name__ == "__main__":
    while True:
        schedule.run_pending()
        time.sleep(60)
```

**Шаг 5 — уведомление о релизе в `publish_release.py`:**

```python
# Добавить в конец publish_release.py
from telegram_notify import notify_release
notify_release(version, changelog_text)
```

**Переменные окружения:**
```
TG_BOT_TOKEN=xxxxxxxxxx:xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TG_CHAT_ID=-100xxxxxxxxxxxx
```

---

### v3.3 — Telegram как канал приёма заявок (Вариант Б)

**Концепция:**  
Двусторонний канал: пользователь пишет Telegram-боту → бот задаёт вопросы анкеты →
данные сохраняются в таблицу `requests` с `source_type='Telegram'` →
заявка появляется в SONAR как обычная, фильтруется и обрабатывается наравне с остальными.
Все действия фиксируются в `activity_log`.

**Шаг 1 — добавить `source_type = 'Telegram'` в классификаторы БД:**

```sql
INSERT INTO classifiers (category, value, sort_order)
VALUES ('source_type', 'Telegram', 99);
```

**Шаг 2 — создать `tg_bot.py` (intake-бот на aiogram 3.x):**

```python
# tg_bot.py
# Telegram intake-бот для приёма заявок в SONAR
import asyncio
import os
from aiogram import Bot, Dispatcher, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import CommandStart
from datetime import datetime
from db import get_db
from activity_log import log_action

BOT_TOKEN   = os.getenv("TG_BOT_TOKEN", "")
TG_SYSTEM_USER_ID = 1   # id системного пользователя SONAR для bot-created заявок

bot = Bot(token=BOT_TOKEN)
dp  = Dispatcher(storage=MemoryStorage())


class Form(StatesGroup):
    full_name    = State()   # полное наименование заявителя
    short_name   = State()   # краткое наименование
    inn          = State()   # ИНН
    contact      = State()   # контактное лицо
    phone        = State()   # телефон
    district     = State()   # предпочтительный район
    description  = State()   # описание проекта / доп. информация
    confirm      = State()   # подтверждение


@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "👋 Добро пожаловать в SONAR!\n\n"
        "Я помогу подать заявку на подбор земельного участка "
        "в Нижегородской области.\n\n"
        "Введите полное наименование организации или ФИО:"
    )
    await state.set_state(Form.full_name)


@dp.message(Form.full_name)
async def step_full_name(message: types.Message, state: FSMContext):
    await state.update_data(full_name=message.text.strip())
    await message.answer("Краткое наименование (или торговая марка):")
    await state.set_state(Form.short_name)


@dp.message(Form.short_name)
async def step_short_name(message: types.Message, state: FSMContext):
    await state.update_data(short_name=message.text.strip())
    await message.answer("ИНН организации или ИП:")
    await state.set_state(Form.inn)


@dp.message(Form.inn)
async def step_inn(message: types.Message, state: FSMContext):
    await state.update_data(inn=message.text.strip())
    await message.answer("Контактное лицо (ФИО):")
    await state.set_state(Form.contact)


@dp.message(Form.contact)
async def step_contact(message: types.Message, state: FSMContext):
    await state.update_data(contact=message.text.strip())
    await message.answer("Контактный телефон:")
    await state.set_state(Form.phone)


@dp.message(Form.phone)
async def step_phone(message: types.Message, state: FSMContext):
    await state.update_data(phone=message.text.strip())
    await message.answer(
        "Предпочтительный район НО (например: Борский, Кстовский, "
        "Нижегородский или «любой»):"
    )
    await state.set_state(Form.district)


@dp.message(Form.district)
async def step_district(message: types.Message, state: FSMContext):
    await state.update_data(district=message.text.strip())
    await message.answer(
        "Кратко опишите проект и требования к участку "
        "(площадь, инфраструктура, вид деятельности):"
    )
    await state.set_state(Form.description)


@dp.message(Form.description)
async def step_description(message: types.Message, state: FSMContext):
    data = await state.update_data(description=message.text.strip())
    await message.answer(
        f"📋 <b>Проверьте данные:</b>\n\n"
        f"Организация: {data['full_name']}\n"
        f"Краткое: {data['short_name']}\n"
        f"ИНН: {data['inn']}\n"
        f"Контакт: {data['contact']}\n"
        f"Телефон: {data['phone']}\n"
        f"Район: {data['district']}\n"
        f"Описание: {data['description']}\n\n"
        f"Отправить заявку? (да / нет)",
        parse_mode="HTML"
    )
    await state.set_state(Form.confirm)


@dp.message(Form.confirm, F.text.lower().in_({"да", "yes", "✅"}))
async def step_confirm_yes(message: types.Message, state: FSMContext):
    data = await state.get_data()
    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    conn = get_db()
    cursor = conn.execute("""
        INSERT INTO requests (
            applicant_full_name, applicant_short_name, applicant_inn,
            contact_person, contact_phone,
            preferred_districts, additional_info,
            source_type, status,
            created_by, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        data['full_name'], data['short_name'], data['inn'],
        data['contact'], data['phone'],
        data['district'], data['description'],
        'Telegram', 'draft',
        TG_SYSTEM_USER_ID, now, now
    ))
    new_id = cursor.lastrowid
    log_action(conn, TG_SYSTEM_USER_ID, 'create', new_id,
               f'Создано через Telegram-бот: {data["short_name"]}')
    conn.commit()
    conn.close()

    await message.answer(
        f"✅ Заявка принята! Номер в системе: <b>ID:{new_id}</b>\n\n"
        f"Менеджер свяжется с вами в ближайшее время.",
        parse_mode="HTML"
    )
    await state.clear()


@dp.message(Form.confirm, F.text.lower().in_({"нет", "no", "❌"}))
async def step_confirm_no(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Заявка отменена. Напишите /start чтобы начать заново.")


async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
```

**Шаг 3 — добавить тип действия в `activity_log.py`:**

```python
# Добавить в ACTION_LABELS:
'tg_intake': 'Заявка из Telegram-бота',
```

**Шаг 4 — фильтрация по source_type в интерфейсе SONAR:**

В `request_routes.py` source_type уже поддерживается в фильтрах. После добавления
классификатора `'Telegram'` он автоматически появится в выпадающем списке фильтра «Источник».

**Зависимости для установки:**
```
pip install aiogram==3.* schedule python-dotenv
```

**Запуск:**
```bat
:: Добавить в start SONAR.bat отдельным окном:
start "SONAR TG Bot" WPy\python.exe tg_bot.py
start "SONAR Scheduler" WPy\python.exe scheduler.py
```

---

## ✅ Реализовано

| Версия | Что сделано | Дата |
|--------|-------------|------|
| v2.3.3 | Hover-popover, fix TypeError Bootstrap tooltip.js | 25.05.2026 |
| v2.3.2 | SettingsMenu: /settings, смена пароля, тема, email | 25.05.2026 |
| v2.3.1 | Unseen changelog badge | 25.05.2026 |
| v2.2.1 | Hotfix батник и base.html | 25.05.2026 |
| v2.2.0 | Поиск, онлайн, GAMMA, inline diff | 24.05.2026 |
| v2.1.0 | Телефонный справочник | 07.05.2026 |
| v2.0.0 | Журнал действий, баннер потери соединения | 07.05.2026 |
| v1.9.1 | История изменений с откатом, загрузка файлов | 05.05.2026 |
| v1.8.2 | Интеграция ОКВЭД, API ФНС | 01.05.2026 |
| v1.8.0 | Рефакторинг на Blueprint-ы | 30.04.2026 |
| v1.6.0 | Боковое меню, тёмный режим | 27.04.2026 |
| v1.0.0 | Первый рабочий выпуск | 23.04.2026 |

---

*Поддержка: [@whoami52](https://t.me/whoami52)*  
*GitHub: [WHO-AM-I-52/SONAR](https://github.com/WHO-AM-I-52/SONAR)*
