@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"
title KITEZH

echo ============================================
echo  KITEZH - Нижегородская область
echo ============================================
echo.

reg add "HKCU\Software\Microsoft\Command Processor" /v DisableUNCCheck /t REG_DWORD /d 1 /f >nul 2>&1

set "APP_DIR=%~dp0"
set "PYTHON="
set "SITEPKG="

:: [1] Ищем Python
if exist "%APP_DIR%WPy\python313\python.exe" (
  set "PYTHON=%APP_DIR%WPy\python313\python.exe"
  set "SITEPKG=%APP_DIR%WPy\python313\Lib\site-packages"
)

if defined PYTHON goto :python_found

:: [2] Python не найден
echo.
echo  [ВНИМАНИЕ] Python / WPy не найден в папке KITEZH!
echo.
echo  Выбери вариант:
echo    [1] Запустить install.bat - автоустановка
echo    [2] Указать путь к python.exe вручную
echo    [0] Выйти
echo.
set "PY_CHOICE="
set /p PY_CHOICE=  Выбор (1/2/0): 

if "%PY_CHOICE%"=="1" goto :run_install
if "%PY_CHOICE%"=="2" goto :manual_path
goto :quit

:run_install
if exist "%APP_DIR%install.bat" (
  echo.
  call "%APP_DIR%install.bat"
  if exist "%APP_DIR%WPy\python313\python.exe" (
    set "PYTHON=%APP_DIR%WPy\python313\python.exe"
    set "SITEPKG=%APP_DIR%WPy\python313\Lib\site-packages"
    goto :python_found
  )
) else (
  echo  [ОШИБКА] install.bat не найден.
)
goto :no_python

:manual_path
echo.
echo  Укажи полный путь к python.exe
echo  Пример: C:\Python313\python.exe
echo.
set "MANUAL_PY="
set /p MANUAL_PY=  Путь: 
if exist "!MANUAL_PY!" (
  set "PYTHON=!MANUAL_PY!"
  set "SITEPKG="
  goto :python_found
)
echo  [ОШИБКА] Файл не найден: !MANUAL_PY!

:no_python
echo.
echo  [ОШИБКА] Python не найден. Запусти install.bat и повтори.
echo.
pause
exit /b 1

:python_found
echo  OK: %PYTHON%
echo.

:: Автофикс python313._pth
set "PTH_FILE=%APP_DIR%WPy\python313\python313._pth"
(
  echo python313.zip
  echo Lib
  echo Lib\site-packages
  echo .
  echo %APP_DIR%
) > "%PTH_FILE%"

:: Явно задаём PYTHONHOME для портативной WPy-сборки
set "PYTHONHOME=%APP_DIR%WPy\python313"

:: Очистка устаревших .pth
if not defined SITEPKG goto :skip_pth
del /f /q "%SITEPKG%\distutils-precedence.pth" 2>nul
:skip_pth

:: Бекап БД
cd /d "%APP_DIR%"
if not exist "%APP_DIR%db\backups" mkdir "%APP_DIR%db\backups"
if exist "%APP_DIR%db\database.db" (
  "%PYTHON%" -c "from datetime import date; open('db\\backups\\.bkdate','w').write(date.today().strftime('%%Y%%m%%d'))"
  set /p BKDATE=<"db\backups\.bkdate"
  del /f /q "db\backups\.bkdate" 2>nul
  xcopy /Y /I "%APP_DIR%db\database.db" "%APP_DIR%db\backups\database_!BKDATE!.db*" >nul
  echo  Бекап: db\backups\database_!BKDATE!.db
) else (
  echo  [ПРЕДУПРЕЖДЕНИЕ] db\database.db не найден
)


"%PYTHON%" -c "import os,glob;files=sorted(glob.glob('db/backups/database_*.db'));[os.remove(f) for f in files[:-5]]"
echo.

:: Очистка uploads\tmp\
if exist "%APP_DIR%uploads\tmp" (
  del /f /q "%APP_DIR%uploads\tmp\*" 2>nul
  echo  uploads\tmp\ очищена.
) else (
  mkdir "%APP_DIR%uploads\tmp"
  echo  uploads\tmp\ создана.
)
echo.

:: Проверка целостности
"%PYTHON%" -m py_compile app.py
if errorlevel 1 (
  echo.
  echo  [ОШИБКА] Синтаксическая ошибка в app.py!
  pause
  exit /b 1
)
echo  Проверка целостности OK.
echo.

:: ────────────────────────────────────────────────────────────────────────────
:: Авторестарт: если _restart.flag существует до запуска — сразу к серверу
:: ────────────────────────────────────────────────────────────────────────────
if exist "%APP_DIR%_restart.flag" (
  del /f /q "%APP_DIR%_restart.flag" 2>nul
  del /f /q "%APP_DIR%_update_available.json" 2>nul
  del /f /q "%APP_DIR%_updating.lock" 2>nul
  echo.
  echo ============================================
  echo  [AUTO-RESTART] Обновление применено.
  echo  Сервер перезапускается автоматически...
  echo ============================================
  echo.
  goto :start_server
)

:: Проверка обновлений на GitHub
if exist "%APP_DIR%update.bat" (
  if exist "%APP_DIR%updater\_updater.py" (
    echo  Проверка обновлений на GitHub...
    "%PYTHON%" "%APP_DIR%updater\_updater.py" --check
    set "CHECK_RESULT=!ERRORLEVEL!"
    echo.

    if "!CHECK_RESULT!"=="0" (
      echo  [OK] Установлена актуальная версия. Обновление пропущено.
      echo.
    ) else if "!CHECK_RESULT!"=="2" (
      echo  [!] Не удалось проверить обновления. Запустить всё равно?
      set "UPD=x"
      set /p UPD=  Скачать архив обновления с GitHub? [Ввод=да / 0=нет]: 
      if "!UPD!"=="x" (
        echo.
        call "%APP_DIR%update.bat"
        cd /d "%APP_DIR%"
        echo.
      ) else if not "!UPD!"=="0" (
        echo.
        call "%APP_DIR%update.bat"
        cd /d "%APP_DIR%"
        echo.
      )
    ) else (
      set "UPD=x"
      set /p UPD=  Скачать архив обновления с GitHub? [Ввод=да / 0=нет]: 
      if "!UPD!"=="x" (
        echo.
        call "%APP_DIR%update.bat"
        cd /d "%APP_DIR%"
        echo.
      ) else if not "!UPD!"=="0" (
        echo.
        call "%APP_DIR%update.bat"
        cd /d "%APP_DIR%"
        echo.
      )
    )
  ) else (
    set "UPD=x"
    set /p UPD=  Скачать архив обновления с GitHub? [Ввод=да / 0=нет]: 
    if "!UPD!"=="x" (
      echo.
      call "%APP_DIR%update.bat"
      cd /d "%APP_DIR%"
      echo.
    ) else if not "!UPD!"=="0" (
      echo.
      call "%APP_DIR%update.bat"
      cd /d "%APP_DIR%"
      echo.
    )
  )
)

:: Режим запуска
:ask_mode
echo  Выбери режим:
echo    [1] Production
echo    [2] Debug
echo    [3] Tray (Production + иконка в трее)
echo.
set "MODE_CHOICE="
set /p MODE_CHOICE=  Режим (1/2/3): 
if "%MODE_CHOICE%"=="1" (
  set "FLASK_ENV=production"
  set "APP_DEBUG=0"
  set "KITEZH_TRAY=0"
) else if "%MODE_CHOICE%"=="2" (
  set "FLASK_ENV=development"
  set "APP_DEBUG=1"
  set "KITEZH_TRAY=0"
) else if "%MODE_CHOICE%"=="3" (
  set "FLASK_ENV=production"
  set "APP_DEBUG=0"
  set "KITEZH_TRAY=1"
) else (
  echo  Неверный выбор.
  goto :ask_mode
)
echo.

:: Открыть браузер
:ask_open
set "OPEN_CHOICE="
set /p OPEN_CHOICE=  Открыть браузер? [1=да / 0=нет]: 
if "%OPEN_CHOICE%"=="1" (
  start "" http://127.0.0.1:5000
) else if "%OPEN_CHOICE%"=="0" (
  rem skip
) else (
  echo  Введи 1 или 0.
  goto :ask_open
)

:: Определяем IP
set "ip=127.0.0.1"
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4"') do (
  set "ip=%%a"
  goto :found_ip
)
:found_ip
set "ip=%ip: =%"

echo.
echo ============================================
echo  Локальный:  http://127.0.0.1:5000
echo  Сетевой:    http://%ip%:5000
echo ============================================
echo.

:start_server
if "%KITEZH_TRAY%"=="1" (
  echo  Tray-режим: консоль свернётся через 2 сек.
) else (
  echo  Сервер запущен... Для остановки нажми Ctrl+C
)
echo.
set FLASK_ENV=%FLASK_ENV%
set APP_DEBUG=%APP_DEBUG%
set KITEZH_TRAY=%KITEZH_TRAY%
set PYTHONUTF8=1
set PYTHONPATH=%APP_DIR%
cd /d "%APP_DIR%"

"%PYTHON%" run_server.py
set "EXIT_CODE=!ERRORLEVEL!"

echo.
echo ============================================
echo   Сервер остановлен. Код: !EXIT_CODE!
echo ============================================
echo.

:: Код 42 — запрос на авторестарт из _worker
if "!EXIT_CODE!"=="42" (
  del /f /q "%APP_DIR%_update_available.json" 2>nul
  del /f /q "%APP_DIR%_updating.lock" 2>nul
  echo.
  echo ============================================
  echo  [AUTO-RESTART] Обновление применено.
  echo  Сервер перезапускается автоматически...
  echo ============================================
  echo.
  goto :start_server
)

:: Пауза перед меню: даём Flask-процессу время полностью закрыть сокет.
:: Без паузы polling-запросы клиентов (каждые 3 сек) вклиниваются в stdin
:: и ломают set /p — нажатия 1/2 не распознаются.
timeout /t 2 /nobreak >nul

echo   [1] Повторный запуск
echo   [2] Выйти
echo.
set "CHOICE="
set /p CHOICE=  Выбор (1/2): 
if "%CHOICE%"=="1" goto :start_server

:quit
exit /b 0
