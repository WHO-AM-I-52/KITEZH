@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"
title SONAR - Ustanovka

echo.
echo ============================================================
echo   SONAR - Ustanovka / pervonachalnaya nastroyka
echo ============================================================
echo.

set "APP_DIR=%~dp0"
set "PYTHON=%APP_DIR%WPy\python313\python.exe"
set "SITEPKG=%APP_DIR%WPy\python313\Lib\site-packages"
set "PYDIR=%APP_DIR%WPy\python313"

:: ============================================================
:: SHAG 1: Ishchem / ustanavlivaem Python
:: ============================================================
echo [1/5] Poisk Python...

if exist "%PYTHON%" (
  echo  OK: WPy\python313 uzhe est.
  goto :check_lib
)

echo  Python ne nayden. Skachivayem Python 3.13 embeddable...
if not exist "%APP_DIR%WPy" mkdir "%APP_DIR%WPy"

curl -L --progress-bar -o "%APP_DIR%WPy\python313.zip" "https://www.python.org/ftp/python/3.13.3/python-3.13.3-embed-amd64.zip"
if errorlevel 1 (
  echo  [OSHIBKA] Ne udalos skachat Python.
  pause
  exit /b 1
)

echo  Raspakovka...
powershell -Command "Expand-Archive -Path '%APP_DIR%WPy\python313.zip' -DestinationPath '%APP_DIR%WPy\python313' -Force"
if errorlevel 1 (
  echo  [OSHIBKA] Ne udalos raspakovat Python.
  pause
  exit /b 1
)
del "%APP_DIR%WPy\python313.zip" >nul 2>&1

if exist "%APP_DIR%tools\python313\get-pip.py" (
  copy /Y "%APP_DIR%tools\python313\get-pip.py" "%PYDIR%\get-pip.py" >nul
)

echo  OK: Python 3.13 ustanovlen.

:: ============================================================
:: SHAG 1.5: Raspakovat vnutrenniy python313.zip
:: ============================================================
:check_lib
if exist "%PYDIR%\Lib\os.py" (
  echo  OK: Lib uzhe est.
  goto :fix_pth
)

if exist "%PYDIR%\python313.zip" (
  echo  Raspakovka standartnoy biblioteki...
  powershell -Command "Expand-Archive -Path '%PYDIR%\python313.zip' -DestinationPath '%PYDIR%\Lib' -Force"
  echo  OK: Lib raspakovna.
) else (
  echo  [WARN] python313.zip ne nayden - Lib mozhet byt nedostupna.
)

:: ============================================================
:: SHAG 1.6: Sozdaem pravilnyy _pth
:: ============================================================
:fix_pth
echo  Nastroyka python313._pth...
(
  echo %PYDIR%\Lib
  echo %PYDIR%\Lib\site-packages
  echo %APP_DIR%
  echo .
) > "%PYDIR%\python313._pth"
echo  OK: _pth nastroyen.

:: ============================================================
:: SHAG 2: Proverka pip
:: ============================================================
echo.
echo [2/5] Proverka pip...

set PYTHONUTF8=1
"%PYTHON%" -m pip --version >nul 2>&1
if errorlevel 1 (
  echo  pip ne nayden, ustanovka...
  if exist "%PYDIR%\get-pip.py" (
    "%PYTHON%" "%PYDIR%\get-pip.py" --quiet
  ) else if exist "%APP_DIR%tools\python313\get-pip.py" (
    "%PYTHON%" "%APP_DIR%tools\python313\get-pip.py" --quiet
  ) else (
    curl -L -o "%PYDIR%\get-pip.py" "https://bootstrap.pypa.io/get-pip.py"
    "%PYTHON%" "%PYDIR%\get-pip.py" --quiet
  )
  echo  OK: pip ustanovlen.
) else (
  echo  OK: pip est.
)

:: ============================================================
:: SHAG 3: Zavisimosti
:: ============================================================
echo.
echo [3/5] Ustanovka zavisimostey iz requirements.txt...

if not exist "%APP_DIR%requirements.txt" (
  echo  [WARN] requirements.txt ne nayden - propusk.
  goto :create_dirs
)

"%PYTHON%" -m pip install -r "%APP_DIR%requirements.txt" --target "%SITEPKG%" --quiet
if errorlevel 1 (
  echo  [OSHIBKA] Ne udalos ustanovit zavisimosti.
  pause
  exit /b 1
)
echo  OK: zavisimosti ustanovleny.

:: ============================================================
:: SHAG 4: Papki i BD
:: ============================================================
:create_dirs
echo.
echo [4/5] Sozdaniye papok...

if not exist "%APP_DIR%db" mkdir "%APP_DIR%db"
if not exist "%APP_DIR%uploads" mkdir "%APP_DIR%uploads"
if not exist "%APP_DIR%reports" mkdir "%APP_DIR%reports"
if not exist "%APP_DIR%db\backups" mkdir "%APP_DIR%db\backups"
echo  OK: papki sozdany.

echo.
echo  Podgotovka bazy dannykh...

if exist "%APP_DIR%db\database.db" (
  echo  BD uzhe est - ne trogaem.
  goto :create_env
)
if exist "%APP_DIR%db\db_template.db" (
  copy /Y "%APP_DIR%db\db_template.db" "%APP_DIR%db\database.db" >nul
  echo  OK: BD sozdana iz shablona.
  goto :create_env
)
if exist "%APP_DIR%db.py" (
  "%PYTHON%" "%APP_DIR%db.py"
  if errorlevel 1 (
    echo  [WARN] db.py vernul oshibku.
  ) else (
    echo  OK: BD initializirovana.
  )
) else (
  echo  [WARN] BD budet sozdana pri pervom zapuske.
)

:: ============================================================
:: SHAG 5: .env
:: ============================================================
:create_env
echo.
echo [5/5] Proverka .env...

if exist "%APP_DIR%.env" (
  echo  .env uzhe est - ne trogaem.
) else (
  "%PYTHON%" -c "import secrets; open('.env','w').write('SECRET_KEY=' + secrets.token_hex(32) + '\n')"
  echo  OK: .env sozdan.
)

echo.
echo ============================================================
echo   Ustanovka zavershena!
echo.
echo   Teper zapusti: start SONAR.bat
echo ============================================================
echo.
pause
exit /b 0
