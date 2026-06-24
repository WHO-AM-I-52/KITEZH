# KITEZH — Платформа учёта и подбора инвестиционных площадок

Внутренняя система управления обращениями и подбора инвестиционных площадок.  
Разработана для автоматизации работы с заявками, документами и аналитикой.

---

## ⚡ Быстрый бегунок

```
KITEZH/
├── app.py                  # Точка входа, регистрация Blueprints, миграции БД
├── run_server.py           # Запуск сервера (Waitress / dev-режим)
├── tray.py                 # Иконка в системном трее (Windows)
├── paths.py                # Единый источник всех runtime-путей
├── db.py                   # Подключение к SQLite, нормативы, маппинги
├── migrations.py           # Инициализация и миграции схемы БД
├── spravochnik.py          # Справочник организаций
├── changelog.py            # История изменений (CHANGELOG)
├── publish_release.py      # Публикация релизов на GitHub
│
├── core/                   # Инфраструктурный слой
│   ├── auth_utils.py           # Авторизация и права доступа
│   ├── activity_log.py         # Журнал действий пользователей (log_action)
│   ├── kitezh_logger.py        # Логирование ошибок
│   ├── limiter.py              # Rate-limiting запросов
│   ├── context_processors.py   # Контекст для Jinja2-шаблонов
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
│   ├── export_import.py        # Импорт/экспорт данных (Excel)
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
│   ├── _updater.py             # Ядро: скачивание и применение патчей
│   ├── branch_switcher.py      # Переключение веток
│   ├── sync_changelog.py       # Синхронизация changelog с GitHub
│   └── make_db_template.py     # Генерация шаблона БД
│
├── requests_app/           # Пакет обращений (list/form/view/action/admin/misc)
├── api/                    # REST API (api_bp)
├── portal_analysis/        # Анализ портала (portal_analysis_bp)
│
├── templates/              # HTML-шаблоны (Jinja2)
├── static/                 # Статика (CSS, JS, изображения)
├── db/                     # SQLite — не в репозитории
├── uploads/                # Загруженные файлы — не в репозитории
├── reports/                # Сгенерированные отчёты — не в репозитории
├── logs/                   # Логи приложения и обновлений
│
├── requirements.txt        # Базовые зависимости Python
├── requirements-ocr.txt    # Зависимости OCR (опционально, ~500 МБ)
├── start KITEZH.bat        # ▶ Запуск сервера (Windows, WPy)
├── install.bat             # Первичная установка зависимостей
├── install_ocr.bat         # Установка OCR-зависимостей (Windows)
├── update.bat              # Обновление кода из репозитория
└── copy_data.bat           # Перенос БД и файлов из резервной копии
```

> **Подробная пользовательская инструкция** — см. [GUIDE.md](GUIDE.md)

---

## Возможности

- Приём и обработка обращений от контрагентов
- Загрузка и OCR-распознавание документов (PDF/DOCX/изображения)
- Экспорт отчётов в Excel
- Справочник организаций и телефонная книга
- Журнал активности пользователей
- Панель администратора: пользователи, ОКВЭД, классификаторы
- Глобальный поиск по всем обращениям
- Дашборд и аналитика по заявкам
- Changelog и история изменений
- Авторизация с разграничением прав
- Инвестиционная карта площадок
- Автообновление через веб-интерфейс (`/admin/update`)

---

## Технологии

- **Python 3.12** / **Flask** — веб-фреймворк
- **SQLite** — база данных (хранится локально, не в репозитории)
- **openpyxl** — экспорт в Excel
- **python-docx** — работа с DOCX
- **easyocr + Pillow** — OCR (опционально)
- **pdfplumber** — извлечение текста из PDF
- **Werkzeug** — утилиты безопасности
- **python-dotenv** — переменные окружения из `.env`

---

## Запуск

### Портативная версия (Windows, рекомендуется)

1. Распаковать архив портативной сборки в любую папку.
2. Запустить `install.bat` — установит зависимости в локальный WPy.
3. Создать файл `.env` (см. ниже).
4. Запустить **`start KITEZH.bat`** — откроет сервер и иконку в трее.
5. Открыть браузер: [http://localhost:5000](http://localhost:5000)

> База данных, файлы и отчёты хранятся локально и **не входят в репозиторий**.

### Из исходников

```bash
git clone https://github.com/WHO-AM-I-52/KITEZH.git
cd KITEZH
pip install -r requirements.txt
python app.py
```

---

## Переменные окружения (.env)

```env
SECRET_KEY=замени_на_случайную_строку
GITHUB_TOKEN=ghp_...        # токен для автообновления (Contents: Read)
```

> `.env` исключён из репозитория через `.gitignore`.

---

## Установка OCR (опционально)

```bash
pip install -r requirements-ocr.txt
# или на Windows:
install_ocr.bat
```

---

## Обновление

Обновление выполняется через веб-интерфейс: **Админ → Обновление системы** (`/admin/update`).  
Система переходит в режим обслуживания, скачивает патчи и перезапускается.  
База данных (`database.db`), загруженные файлы (`uploads/`) и отчёты (`reports/`) **не затрагиваются**.

Также доступно ручное обновление через `update.bat` (требует `GITHUB_TOKEN` в `.env`).

---

## Резервное копирование

Автоматические бэкапы выполняются планировщиком (`services/backup_scheduler.py`).  
Для ручного переноса данных из резервной копии (`KITEZH.backup`) в новую установку:

```
Запустить copy_data.bat
```

Скрипт скопирует `.env`, `database.db`, `uploads/` и `reports/`.

---

## Важно

- `database.db` и папки `uploads/`, `reports/` создаются автоматически при первом запуске.
- Файлы с персональными данными хранятся **только локально**.
- OCR-функции опциональны — приложение работает без них.

---

## Автор

**WHO-AM-I-52** | Нижегородская область  
Telegram: [@whoami52](https://t.me/whoami52)
