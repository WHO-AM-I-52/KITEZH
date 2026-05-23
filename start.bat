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

echo Cleaning old .pth packages...
for %%v in (3.5 3.6 3.7 3.8 3.9) do (
  for %%f in ("%SITEPKG%\*-py%%v-nspkg.pth") do (
    if exist "%%f" ( del /f /q "%%f" )
  )
)
del /f /q "%SITEPKG%\distutils-precedence.pth" 2>nul
echo Done.
echo.

if not exist db\backups mkdir db\backups
echo Creating database backup...
xcopy /Y /I db\database.db "db\backups\database_%date:~6,4%%date:~3,2%%date:~0,2%.db*" >nul
echo Backup: db\backups\database_%date:~6,4%%date:~3,2%%date:~0,2%.db
echo.

"%PYTHON%" -c "import os,glob;files=sorted(glob.glob('db/backups/database_*.db'));[os.remove(f) for f in files[:-5]];print('Backups kept: '+str(min(len(files),5)))"
echo.

:: ── Sync changelog + roadmap ────────────────────────────────
set /p SYNC=Sync changelog s GitHub? [Enter = da / 0 = net]: 
if not "%SYNC%"=="0" (
  echo.
  "%PYTHON%" "%APP_DIR%sync_changelog.py"
  echo.
)

:: ── Определяем IP ───────────────────────────────────────────
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4"') do (
  set ip=%%a
  goto :found
)
:found
set ip=%ip: =%

:: ── Запускаем сервер — адреса покажет сам Flask ─────────────
echo Starting server...
echo.
echo ============================================
echo  Local:   http://127.0.0.1:5000
echo  Network: http://%ip%:5000
echo ============================================
echo.

"%PYTHON%" "%APP_DIR%app.py"
pause
