@echo off
chcp 65001 >nul
cd /d "%~dp0"
title SONAR - Land App

echo ============================================
echo  SONAR - Nizhny Novgorod
echo ============================================
echo.

reg add "HKCU\Software\Microsoft\Command Processor" /v DisableUNCCheck /t REG_DWORD /d 1 /f >nul 2>&1

set "PYTHON=%~dp0WPy64-31100\WPy64-31241\python-3.12.4.amd64\python.exe"
set "SITEPKG=%~dp0WPy64-31100\WPy64-31241\python-3.12.4.amd64\Lib\site-packages"
set "APP_DIR=%~dp0"

if not exist "%PYTHON%" (
  echo.
  echo [ERROR] Python not found: %PYTHON%
  echo.
  pause
  exit /b 1
)

echo OK: %PYTHON%
echo.

:: ── Очистка старых .pth пакетов ───────────────────────────────
echo Cleaning old .pth packages...
for %%v in (3.5 3.6 3.7 3.8 3.9) do (
  for %%f in ("%SITEPKG%\*-py%%v-nspkg.pth") do (
    if exist "%%f" del /f /q "%%f"
  )
)
del /f /q "%SITEPKG%\distutils-precedence.pth" 2>nul
echo Done.
echo.

:: ── Бэкап базы данных ─────────────────────────────────────────
if not exist db\backups mkdir db\backups
echo Creating database backup...
xcopy /Y /I db\database.db "db\backups\database_%date:~6,4%%date:~3,2%%date:~0,2%.db*" >nul
echo Backup: db\backups\database_%date:~6,4%%date:~3,2%%date:~0,2%.db
echo.

"%PYTHON%" -c "import os,glob;files=sorted(glob.glob('db/backups/database_*.db'));[os.remove(f) for f in files[:-5]];print('Backups kept: '+str(min(len(files),5)))"
echo.

:: ── Health check ──────────────────────────────────────────────
echo Running health check...
"%PYTHON%" -m py_compile app.py
if errorlevel 1 (
  echo.
  echo [ERROR] Syntax error in app.py — fix and restart.
  echo.
  pause
  exit /b 1
)
echo Health check OK.
echo.

:: ── Обновление кода из GitHub ─────────────────────────────────
if exist "%APP_DIR%update.bat" (
  set /p UPD=Obnovit kod iz GitHub? [Enter = da / 0 = net]: 
  if not "%UPD%"=="0" (
    echo.
    call "%APP_DIR%update.bat"
    echo.
  )
)

:: ── Sync changelog + roadmap ──────────────────────────────────
set /p SYNC=Sync changelog s GitHub? [Enter = da / 0 = net]: 
if not "%SYNC%"=="0" (
  echo.
  "%PYTHON%" "%APP_DIR%sync_changelog.py"
  echo.
)

:: ── Выбор режима ──────────────────────────────────────────────
:ask_mode
echo Select mode:
echo   [1] Production  (normal work)
echo   [2] Debug       (detailed errors, auto-reload)
echo.
set "MODE_CHOICE="
set /p MODE_CHOICE=Mode (1/2): 

if "%MODE_CHOICE%"=="1" (
  set "FLASK_ENV=production"
  set "APP_DEBUG=0"
) else if "%MODE_CHOICE%"=="2" (
  set "FLASK_ENV=development"
  set "APP_DEBUG=1"
) else (
  echo Invalid choice, enter 1 or 2.
  echo.
  goto ask_mode
)
echo.

:: ── Открыть браузер ───────────────────────────────────────────
:ask_open
set "OPEN_CHOICE="
set /p OPEN_CHOICE=Otkryt brauser? [1 = da / 0 = net]: 
if "%OPEN_CHOICE%"=="1" (
  start "" http://127.0.0.1:5000
) else if "%OPEN_CHOICE%"=="0" (
  rem skip
) else (
  echo Enter 1 or 0.
  echo.
  goto ask_open
)

:: ── Определяем IP ────────────────────────────────────────────
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4"') do (
  set ip=%%a
  goto :found
)
:found
set ip=%ip: =%

echo.
echo ============================================
echo  Local:   http://127.0.0.1:5000
echo  Network: http://%ip%:5000
echo ============================================
echo.

:: ── Запуск сервера ───────────────────────────────────────────
:start_server
echo Server is running... Close window to stop.
echo.

set FLASK_ENV=%FLASK_ENV%
set APP_DEBUG=%APP_DEBUG%
start "SONAR Server" /wait "%PYTHON%" "%APP_DIR%app.py"

echo.
echo ============================================
echo   Server stopped.
echo ============================================
echo.
echo   [1] Restart server
echo   [2] Exit
echo.
set "CHOICE="
set /p CHOICE=Choice (1/2): 
if "%CHOICE%"=="1" goto start_server
goto quit

:quit
exit /b 0
