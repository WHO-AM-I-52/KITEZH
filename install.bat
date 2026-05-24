@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"
title SONAR — Установка

echo.
echo ============================================================
echo   SONAR — Установка / первоначальная настройка
echo ============================================================
echo.

set "APP_DIR=%~dp0"
set "PYTHON="
set "SITEPKG="

:: ============================================================
:: ШАГ 1: Ищем Python / WPy рядом
:: ============================================================
echo [1/5] Поиск Python...

for /d %%A in ("%APP_DIR%WPy\python-*.amd64") do (
  if exist "%%A\python.exe" (
    set "PYTHON=%%A\python.exe"
    set "SITEPKG=%%A\Lib\site-packages"
  )
)

if defined PYTHON (
  echo  OK: !PYTHON!
  goto :install_deps
)

:: WPy не найден — спрашиваем путь
echo  WPy не найден в папке SONAR\WPy\
echo.
echo  Удостоверься, что папка WPy скопирована рядом с этим файлом.
echo  Скачать WPy можно здесь: https://winpython.github.io/
echo.
echo  Если WPy уже есть в другом месте — укажи путь вручную:
echo  Пример: C:\WPy64-31131\python-3.11.3.amd64\python.exe
echo.
set "MANUAL_PY="
set /p MANUAL_PY=  Путь к python.exe (или Enter чтобы пропустить): 

if not "!MANUAL_PY!"=="" (
  if exist "!MANUAL_PY!" (
    set "PYTHON=!MANUAL_PY!"
    for %%X in ("!MANUAL_PY!") do set "SITEPKG=%%~dpXLib\site-packages"
    echo  OK: !PYTHON!
    goto :install_deps
  ) else (
    echo  [ОШИБКА] Файл не найден: !MANUAL_PY!
    goto :no_python
  )
)

:no_python
echo.
echo  [ОШИБКА] Python не найден. Установка невозможна.
echo  Скопируйте WPy\ в папку рядом с install.bat и повторите.
echo.
pause
exit /b 1

:: ============================================================
:: ШАГ 2: Установка зависимостей
:: ============================================================
:install_deps
echo.
echo [2/5] Установка зависимостей из requirements.txt...

if not exist "%APP_DIR%requirements.txt" (
  echo  [ПРЕДУПРЕЖДЕНИЕ] requirements.txt не найден — пропуск.
  goto :create_dirs
)

"%PYTHON%" -m pip install --quiet -r "%APP_DIR%requirements.txt"
if errorlevel 1 (
  echo  [ОШИБКА] Не удалось установить зависимости.
  echo  Проверьте подключение к Интернету.
  pause
  exit /b 1
)
echo  OK: зависимости установлены.

:: ============================================================
:: ШАГ 3: Создание папок
:: ============================================================
:create_dirs
echo.
echo [3/5] Создание папок...

for %%D in (db uploads reports db\backups) do (
  if not exist "%APP_DIR%%%D" (
    mkdir "%APP_DIR%%%D"
    echo  Создана: %%D
  ) else (
    echo  Уже есть: %%D
  )
)

:: ============================================================
:: ШАГ 4: Создание БД
:: ============================================================
echo.
echo [4/5] Подготовка базы данных...

if exist "%APP_DIR%db\database.db" (
  echo  База данных уже существует — не трогаем.
  goto :create_env
)

:: Есть шаблон — копируем
if exist "%APP_DIR%db\db_template.db" (
  copy /Y "%APP_DIR%db\db_template.db" "%APP_DIR%db\database.db" >nul
  echo  OK: База создана из db_template.db
  goto :create_env
)

:: Шаблона нет — инициализируем через код
if exist "%APP_DIR%db.py" (
  "%PYTHON%" "%APP_DIR%db.py"
  if errorlevel 1 (
    echo  [ПРЕДУПРЕЖДЕНИЕ] db.py вернул ошибку — БД будет создана при первом запуске.
  ) else (
    echo  OK: База инициализирована через db.py
  )
) else (
  echo  [ПРЕДУПРЕЖДЕНИЕ] db.py не найден. БД будет создана при первом запуске SONAR.
)

:: ============================================================
:: ШАГ 5: Создание .env если его нет
:: ============================================================
:create_env
echo.
echo [5/5] Проверка .env...

if exist "%APP_DIR%.env" (
  echo  .env уже есть — не трогаем.
) else (
  echo  Создаю .env с уникальным SECRET_KEY...
  "%PYTHON%" -c "import secrets; open('.env','w').write('SECRET_KEY=' + secrets.token_hex(32) + '\n')"
  echo  OK: .env создан.
  echo.
  echo  [ВАЖНО] Для автообновления добавьте в .env:
  echo  GITHUB_TOKEN=ваш_токен
)

:: ============================================================
:: ИТОГ
:: ============================================================
echo.
echo ============================================================
echo   Установка завершена!
echo.
echo   Теперь запусти start SONAR.bat
echo ============================================================
echo.
pause
exit /b 0
