# KITEZH — Платформа учёта и подбора инвестиционных площадок

Внутренняя система управления обращениями и подбора инвестиционных площадок.  
Разработана для автоматизации работы с заявками, документами и аналитикой.

---

## Возможности

- Приём и обработка обращений от контрагентов
- Загрузка и OCR-распознавание документов (анкеты, договоры, PDF/DOCX/изображения)
- Экспорт отчётов в Excel
- Справочник организаций и телефонная книга
- Журнал активности пользователей
- Панель администратора: управление пользователями, ОКВЭД, классификаторами
- Глобальный поиск по всем обращениям
- Дашборд и аналитика по заявкам
- Changelog и система уведомлений
- Авторизация с разграничением прав доступа
- Автообновление кода через `update.bat` (без потери данных)

---

## Технологии

- **Python 3.12**
- **Flask** — веб-фреймворк
- **SQLite** — база данных (хранится локально, не входит в репозиторий)
- **openpyxl** — экспорт в Excel
- **python-docx** — работа с DOCX-файлами
- **easyocr + Pillow** — OCR-распознавание документов (опционально)
- **pdfplumber** — извлечение текста из PDF
- **Werkzeug** — утилиты безопасности
- **python-dotenv** — переменные окружения из `.env`

---

## Структура проекта

Проект организован по пакетам: инфраструктура (`core/`), утилиты (`utils/`),
бизнес-сервисы (`services/`), HTTP-маршруты (`routes/`) и подсистема
обновления (`updater/`). Все runtime-пути берутся из единого `paths.py`.

```
KITEZH/
├── app.py                  # Точка входа, инициализация Flask, регистрация Blueprints
├── run_server.py           # Запуск сервера (Waitress / dev)
├── tray.py                 # Иконка в системном трее (Windows)
├── paths.py                # Единый источник правды для всех runtime-путей
├── db.py                   # Подключение к SQLite, нормативы, маппинги
├── migrations.py           # Инициализация и миграции схемы БД
├── spravochnik.py          # Справочник организаций
├── changelog.py            # История изменений (CHANGELOG)
├── publish_release.py      # Публикация релизов на GitHub
│
├── core/                   # Инфраструктурный слой
│   ├── auth_utils.py           # Авторизация и права доступа
│   ├── activity_log.py         # Журнал действий пользователей
│   ├── kitezh_logger.py        # Логирование ошибок
│   ├── limiter.py              # Ограничение частоты запросов
│   ├── context_processors.py   # Контекст для шаблонов
│   └── request_history.py      # История изменений обращений
│
├── utils/                  # Переиспользуемые утилиты
│   ├── validators.py           # Валидация данных (ИНН, файлы)
│   ├── field_validator.py      # Валидация полей форм
│   ├── form_utils.py           # Утилиты форм
│   └── github_utils.py         # Помощники для работы с GitHub API
│
├── services/               # Бизнес-сервисы
│   ├── dashboard.py            # Дашборд и аналитика
│   ├── ocr_utils.py            # OCR-распознавание документов
│   ├── export_excel.py         # Экспорт отчётов в Excel
│   ├── export_helpers.py       # Помощники экспорта
│   ├── export_import.py        # Импорт/экспорт данных
│   ├── backup_scheduler.py     # Планировщик резервных копий
│   ├── restore_investmap.py    # Восстановление инвесткарты
│   └── roadmap.py              # Дорожная карта (ROADMAP)
│
├── routes/                 # HTTP-маршруты (Flask Blueprints)
│   ├── login_routes.py         # Авторизация (auth_bp)
│   ├── search_routes.py        # Глобальный поиск (search_bp)
│   ├── admin_routes.py         # Админ-панель (admin_bp)
│   ├── admin_deps.py           #   ├─ роуты зависимостей
│   ├── admin_classifiers.py    #   ├─ роуты классификаторов
│   ├── admin_filters.py        #   └─ роуты сохранённых фильтров
│   ├── admin_sql_routes.py     # SQL-консоль администратора
│   ├── export_routes.py        # Экспорт в Excel (report_bp)
│   ├── info_routes.py          # Информационные страницы (misc_bp)
│   ├── update_routes.py        # Управление обновлениями (update_bp)
│   ├── settings_routes.py      # Настройки (settings_bp)
│   ├── preview_routes.py       # Предпросмотр документов (preview_bp)
│   ├── investmap_routes.py     # Инвестиционная карта (investmap_bp)
│   ├── phonebook_routes.py     # Телефонная книга (phonebook_bp)
│   ├── phonebook_import.py     # Импорт контактов (pb_import_bp)
│   ├── ai_routes.py            # ИИ-функции и OCR (ai_bp)
│   ├── quality_checks.py       # Проверки качества данных (quality_bp)
│   ├── okved_admin.py          # Управление ОКВЭД (okved_bp)
│   ├── okved_api.py            # API ОКВЭД (okved_api_bp)
│   └── egrul_api.py            # API ЕГРЮЛ (egrul_api_bp)
│
├── updater/                # Подсистема автообновления
│   ├── _updater.py             # Ядро автообновления (скачивание/применение патчей)
│   ├── branch_switcher.py      # Переключение веток
│   ├── sync_changelog.py       # Синхронизация changelog/roadmap с GitHub
│   └── make_db_template.py     # Генерация шаблона БД
│
├── requests_app/           # Пакет обращений (list/form/view/action/admin/misc)
├── api/                    # REST API (requests_api → api_bp)
├── portal_analysis/        # Анализ портала (portal_analysis_bp)
│
├── templates/              # HTML-шаблоны (Jinja2)
├── static/                 # Статика (CSS, JS, изображения)
├── db/                     # SQLite (database.db — не в репозитории)
├── uploads/                # Загруженные файлы (не в репозитории)
├── reports/                # Сгенерированные отчёты (не в репозитории)
├── logs/                   # Логи приложения и обновлений
│
├── requirements.txt        # Базовые зависимости Python
├── requirements-ocr.txt    # Зависимости для OCR (опционально)
├── start KITEZH.bat        # Запуск сервера (Windows, портативная версия)
├── update.bat              # Обновление кода из репозитория
├── install.bat             # Первичная установка
├── install_ocr.bat         # Установка OCR-зависимостей (Windows)
└── copy_data.bat           # Перенос БД и файлов из резервной копии
```

---

## Установка и запуск

### Вариант 1 — Портативная версия (Windows)

1. Скачать архив портативной сборки.
2. Распаковать в любую папку (можно на флешку).
3. Запустить `install.bat` — установит зависимости.
4. Создать файл `.env` (см. раздел ниже).
5. Запустить `start KITEZH.bat`.
6. Открыть браузер: [http://localhost:5000](http://localhost:5000)

> База данных, загруженные файлы и отчёты хранятся локально и **не входят в репозиторий**.

### Вариант 2 — Установка из исходников

```bash
git clone https://github.com/WHO-AM-I-52/KITEZH.git
cd KITEZH
pip install -r requirements.txt
python app.py
```

---

## Переменные окружения (.env)

Создай файл `.env` в корне проекта:

```env
SECRET_KEY=замени_на_случайную_строку
GITHUB_TOKEN=ghp_...        # токен для автообновления (Contents: Read)
```

> `.env` исключён из репозитория через `.gitignore` — не добавляй его в git.

---

## Установка OCR (опционально)

OCR-функции используют **EasyOCR** и требуют дополнительных зависимостей (~500 МБ моделей PyTorch):

```bash
pip install -r requirements-ocr.txt
```

Или запустить `install_ocr.bat` на Windows.

---

## Обновление (портативная версия)

Запусти `update.bat` — скрипт загрузит последние изменения кода из репозитория,  
**не затрагивая** базу данных (`database.db`), загруженные файлы (`uploads/`) и отчёты (`reports/`).

Требуется `GITHUB_TOKEN` в `.env`.

---

## Восстановление данных

Если нужно перенести данные из резервной копии (`KITEZH.backup`) в новую установку:

```
Запустить copy_data.bat
```

Скрипт скопирует `.env`, `database.db`, `uploads/` и `reports/` из папки резервной копии.

---

## Важно

- База данных (`database.db`) и папки `uploads/`, `reports/` создаются автоматически при первом запуске.
- Файлы с персональными данными хранятся **только локально** и исключены из репозитория через `.gitignore`.
- OCR-функции опциональны — приложение работает без них.

---

## Автор

**WHO-AM-I-52** | Нижегородская область  
Telegram: [@whoami52](https://t.me/whoami52)
