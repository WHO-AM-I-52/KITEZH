@echo off
chcp 65001 >nul
cd /d "%~dp0"
title SONAR - Ustanovka

echo.
echo ============================================================
echo   SONAR - Ustanovka / pervonachalnaya nastroyka
echo ============================================================
echo.

set "APP_DIR=%~dp0"
set "PYTHON="
set "SITEPKG="
set "WPY_DIR=%APP_DIR%WPy"

:: ============================================================
:: SHAG 1: Ishchem Python / WPy ryadom
:: ============================================================
echo [1/6] Poisk Python...

for /d %%A in ("%WPY_DIR%\python-*.amd64") do (
  if exist "%%A\python.exe" (
    set "PYTHON=%%A\python.exe"
    set "SITEPKG=%%A\Lib\site-packages"
  )
)

if defined PYTHON (
  echo  OK: %PYTHON%
  goto :install_deps
)

:: WPy ne nayden
echo  WPy ne nayden v papke SONAR\WPy\
echo.
echo  Vyberi variant:
echo    [1] Skachat i ustanovit WPy avtomaticheski (trebuetsya Internet)
echo    [2] Ukazat put k python.exe vruchnuyu
echo    [0] Vyyti
echo.
set "DL_CHOICE="
set /p DL_CHOICE=  Vybor (1/2/0): 

if "%DL_CHOICE%"=="1" goto :download_wpy
if "%DL_CHOICE%"=="2" goto :manual_path
goto :quit

:: ============================================================
:: AVTOSKACHIVANIYE WPy cherez PS1
:: ============================================================
:download_wpy
echo.
echo  [2/6] Skachivanie posledney versii WPy...

if not exist "%WPY_DIR%" mkdir "%WPY_DIR%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%APP_DIR%download_wpy.ps1" -TargetDir "%WPY_DIR%"

if errorlevel 1 (
  echo.
  echo  [OSHIBKA] Avtoskachivaniye ne udalos.
  echo  Proverte podklyucheniye k Internetu.
  echo  Ili skachain WPy vruchnuyu: https://winpython.github.io/
  goto :manual_path
)

echo.
echo  Raspakuyu WPy (2-5 minut)...

"%WPY_DIR%\wpy_setup.exe" -o"%WPY_DIR%" -y

del /f /q "%WPY_DIR%\wpy_setup.exe" 2>nul

:: Ishchem python posle raspakrovki
for /d %%B in ("%WPY_DIR%\WPy64*") do (
  for /d %%A in ("%%B\python-*.amd64") do (
    if exist "%%A\python.exe" (
      set "PYTHON=%%A\python.exe"
      set "SITEPKG=%%A\Lib\site-packages"
    )
  )
)
for /d %%A in ("%WPY_DIR%\python-*.amd64") do (
  if exist "%%A\python.exe" (
    set "PYTHON=%%A\python.exe"
    set "SITEPKG=%%A\Lib\site-packages"
  )
)

if defined PYTHON (
  echo  OK: WPy ustanovlen!
  echo  Python: %PYTHON%
  goto :install_deps
)

echo.
echo  [OSHIBKA] WPy skachan, no python.exe ne nayden.
echo  Prover papku: %WPY_DIR%
goto :manual_path

:: ============================================================
:: RUCHNOY PUT
:: ============================================================
:manual_path
echo.
echo  Ukazhi polnyy put k python.exe:
echo  Primer: C:\WPy64-31131\python-3.11.3.amd64\python.exe
echo.
set "MANUAL_PY="
set /p MANUAL_PY=  Put k python.exe (ili Enter chtoby vyyti): 

if "%MANUAL_PY%"=="" goto :no_python
if exist "%MANUAL_PY%" (
  set "PYTHON=%MANUAL_PY%"
  for %%X in ("%MANUAL_PY%") do set "SITEPKG=%%~dpXLib\site-packages"
  echo  OK: %PYTHON%
  goto :install_deps
) else (
  echo  [OSHIBKA] Fayl ne nayden: %MANUAL_PY%
  goto :no_python
)

:no_python
echo.
echo  [OSHIBKA] Python ne nayden. Ustanovka nevozmozhna.
echo.
pause
exit /b 1

:: ============================================================
:: SHAG 3: Zavisimosti
:: ============================================================
:install_deps
echo.
echo [3/6] Ustanovka zavisimostey iz requirements.txt...

if not exist "%APP_DIR%requirements.txt" (
  echo  [PREDUPREZHDENIE] requirements.txt ne nayden - propusk.
  goto :create_dirs
)

"%PYTHON%" -m pip install --quiet -r "%APP_DIR%requirements.txt"
if errorlevel 1 (
  echo  [OSHIBKA] Ne udalos ustanovit zavisimosti.
  pause
  exit /b 1
)
echo  OK: zavisimosti ustanovleny.

:: ============================================================
:: SHAG 4: Papki
:: ============================================================
:create_dirs
echo.
echo [4/6] Sozdaniye papok...

for %%D in (db uploads reports db\backups) do (
  if not exist "%APP_DIR%%%D" (
    mkdir "%APP_DIR%%%D"
    echo  Sozdana: %%D
  ) else (
    echo  Uzhe est: %%D
  )
)

:: ============================================================
:: SHAG 5: Baza dannykh
:: ============================================================
echo.
echo [5/6] Podgotovka bazy dannykh...

if exist "%APP_DIR%db\database.db" (
  echo  Baza uzhe sushchestvuyet - ne trogaem.
  goto :create_env
)

if exist "%APP_DIR%db\db_template.db" (
  copy /Y "%APP_DIR%db\db_template.db" "%APP_DIR%db\database.db" >nul
  echo  OK: Baza sozdana iz db_template.db
  goto :create_env
)

if exist "%APP_DIR%db.py" (
  "%PYTHON%" "%APP_DIR%db.py"
  if errorlevel 1 (
    echo  [PREDUPREZHDENIE] db.py vernul oshibku.
  ) else (
    echo  OK: Baza initializirovana.
  )
) else (
  echo  [PREDUPREZHDENIE] db.py ne nayden. BD budet sozdana pri zapuske.
)

:: ============================================================
:: SHAG 6: .env
:: ============================================================
:create_env
echo.
echo [6/6] Proverka .env...

if exist "%APP_DIR%.env" (
  echo  .env uzhe est - ne trogaem.
) else (
  echo  Sozdayu .env...
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

:quit
exit /b 0
