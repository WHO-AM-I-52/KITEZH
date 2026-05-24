@echo off
chcp 65001 >nul
cd /d "%~dp0"
title SONAR

echo ============================================
echo  SONAR - Nizhegorodskaya oblast
echo ============================================
echo.

reg add "HKCU\Software\Microsoft\Command Processor" /v DisableUNCCheck /t REG_DWORD /d 1 /f >nul 2>&1

set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"
set "PYTHON="
set "SITEPKG="

:: [1] Ishchem Python
if exist "%APP_DIR%\WPy\python313\python.exe" (
  set "PYTHON=%APP_DIR%\WPy\python313\python.exe"
  set "SITEPKG=%APP_DIR%\WPy\python313\Lib\site-packages"
)

if defined PYTHON goto :python_found

:: [2] Python ne nayden
echo.
echo  [VNIMANIE] Python / WPy ne nayden v papke SONAR!
echo.
echo  Vyberi variant:
echo    [1] Zapustit install.bat - avtoystanovka
echo    [2] Ukazat put k python.exe vruchnuyu
echo    [0] Vyyti
echo.
set "PY_CHOICE="
set /p PY_CHOICE=  Vybor (1/2/0): 

if "%PY_CHOICE%"=="1" goto :run_install
if "%PY_CHOICE%"=="2" goto :manual_path
goto :quit

:run_install
if exist "%APP_DIR%\install.bat" (
  echo.
  call "%APP_DIR%\install.bat"
  if exist "%APP_DIR%\WPy\python313\python.exe" (
    set "PYTHON=%APP_DIR%\WPy\python313\python.exe"
    set "SITEPKG=%APP_DIR%\WPy\python313\Lib\site-packages"
    goto :python_found
  )
) else (
  echo  [OSHIBKA] install.bat ne nayden.
)
goto :no_python

:manual_path
echo.
echo  Ukazhi polnyy put k python.exe
echo  Primer: C:\Python313\python.exe
echo.
set "MANUAL_PY="
set /p MANUAL_PY=  Put: 
if exist "%MANUAL_PY%" (
  set "PYTHON=%MANUAL_PY%"
  set "SITEPKG="
  goto :python_found
)
echo  [OSHIBKA] Fayl ne nayden: %MANUAL_PY%

:no_python
echo.
echo  [OSHIBKA] Python ne nayden. Zapusti install.bat i povtori.
echo.
pause
exit /b 1

:python_found
echo  OK: %PYTHON%
echo.

:: Ochistka starykh .pth
if not defined SITEPKG goto :skip_pth
del /f /q "%SITEPKG%\distutils-precedence.pth" 2>nul
:skip_pth

:: Bekap BD
if not exist "%APP_DIR%\db\backups" mkdir "%APP_DIR%\db\backups"
if exist "%APP_DIR%\db\database.db" (
  set "BKDATE=%date:~6,4%%date:~3,2%%date:~0,2%"
  xcopy /Y /I "%APP_DIR%\db\database.db" "%APP_DIR%\db\backups\database_%BKDATE%.db*" >nul
  echo  Bekap: db\backups\database_%BKDATE%.db
) else (
  echo  [WARN] db\database.db ne nayden
)
"%PYTHON%" -c "import os,glob;files=sorted(glob.glob(r'%APP_DIR%\db\backups\database_*.db'));[os.remove(f) for f in files[:-5]]"
echo.

:: Health check
cd /d "%APP_DIR%"
"%PYTHON%" -m py_compile app.py
if errorlevel 1 (
  echo.
  echo  [OSHIBKA] Sintaksicheskaya oshibka v app.py!
  pause
  exit /b 1
)
echo  Health check OK.
echo.

:: Obnovlenie koda
if exist "%APP_DIR%\update.bat" (
  set /p UPD=  Obnovit kod iz GitHub? [Enter=da / 0=net]: 
  if not "%UPD%"=="0" (
    echo.
    call "%APP_DIR%\update.bat"
    echo.
  )
)

:: Sync changelog
set /p SYNC=  Sync changelog? [Enter=da / 0=net]: 
if not "%SYNC%"=="0" (
  echo.
  "%PYTHON%" "%APP_DIR%\sync_changelog.py"
  echo.
)

:: Rezhim
:ask_mode
echo  Vyberi rezhim:
echo    [1] Production
echo    [2] Debug
echo.
set "MODE_CHOICE="
set /p MODE_CHOICE=  Rezhim (1/2): 
if "%MODE_CHOICE%"=="1" (
  set "FLASK_ENV=production"
  set "APP_DEBUG=0"
) else if "%MODE_CHOICE%"=="2" (
  set "FLASK_ENV=development"
  set "APP_DEBUG=1"
) else (
  echo  Neverniy vybor.
  goto :ask_mode
)
echo.

:: Brauzer
:ask_open
set "OPEN_CHOICE="
set /p OPEN_CHOICE=  Otkryt brauzer? [1=da / 0=net]: 
if "%OPEN_CHOICE%"=="1" (
  start "" http://127.0.0.1:5000
) else if "%OPEN_CHOICE%"=="0" (
  rem skip
) else (
  echo  Vvedi 1 ili 0.
  goto :ask_open
)

:: IP
set "ip=127.0.0.1"
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4"') do (
  set "ip=%%a"
  goto :found_ip
)
:found_ip
set "ip=%ip: =%"

echo.
echo ============================================
echo  Local:   http://127.0.0.1:5000
echo  Network: http://%ip%:5000
echo ============================================
echo.

:start_server
echo  Server zapushen... Dlya ostanovki nazhmi Ctrl+C
echo.
set FLASK_ENV=%FLASK_ENV%
set APP_DEBUG=%APP_DEBUG%
cd /d "%APP_DIR%"
"%PYTHON%" "%APP_DIR%\app.py"

echo.
echo ============================================
echo   Server ostanovlen.
echo ============================================
echo.
echo   [1] Povtornyy zapusk
echo   [2] Vyyti
echo.
set "CHOICE="
set /p CHOICE=  Vybor (1/2): 
if "%CHOICE%"=="1" goto :start_server

:quit
exit /b 0
