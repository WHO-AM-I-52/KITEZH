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
:: AVTOSKACHIVANIYE WPy
:: ============================================================
:download_wpy
echo.
echo  [2/6] Skachivanie posledney versii WPy...
echo  Poluchayu ssylku s GitHub API...
echo.

if not exist "%APP_DIR%WPy" mkdir "%APP_DIR%WPy"

:: Skachivanie cherez PowerShell - poluchaem posledniy reliz i skachivam
powershell -NoProfile -ExecutionPolicy Bypass -Command "
  try {
    $releases = Invoke-RestMethod 'https://api.github.com/repos/winpython/winpython/releases' -UseBasicParsing;
    $asset = $null;
    foreach ($rel in $releases) {
      foreach ($a in $rel.assets) {
        if ($a.name -match 'Winpython64.*dot\.exe$') {
          $asset = $a; break
        }
      }
      if ($asset) { break }
    };
    if ($asset) {
      Write-Host ('  Nayden: ' + $asset.name);
      Write-Host ('  Razmer: ' + [math]::Round($asset.size/1MB,1) + ' MB');
      Write-Host '  Skachivanie...';
      $out = '%APP_DIR%WPy\wpy_setup.exe';
      $wc = New-Object System.Net.WebClient;
      $wc.DownloadFile($asset.browser_download_url, $out);
      Write-Host '  OK: skachano!';
      Write-Host $asset.name | Out-File '%APP_DIR%WPy\.wpy_name.txt' -Encoding utf8
    } else {
      Write-Host '  [OSHIBKA] Ne udalos nayti WPy v relizakh GitHub.';
      exit 1
    }
  } catch {
    Write-Host ('  [OSHIBKA] ' + $_.Exception.Message);
    exit 1
  }
"

if errorlevel 1 (
  echo.
  echo  [OSHIBKA] Avtoskachivaniye ne udalos.
  echo  Proverte podklyucheniye k Internetu.
  echo  Ili skachain WPy vruchnuyu: https://winpython.github.io/
  goto :manual_path
)

echo.
echo  Raspakuyu WPy (mozhet zanyat 2-5 minut)...

:: Zapuskaem installer v tikhom rezhime v papku WPy
"%APP_DIR%WPy\wpy_setup.exe" -o"%APP_DIR%WPy" -y

:: Udalyaem installer
del /f /q "%APP_DIR%WPy\wpy_setup.exe" 2>nul

:: Ishchem raspakovanniy python
for /d %%A in ("%APP_DIR%WPy\WPy64*\python-*.amd64") do (
  if exist "%%A\python.exe" (
    set "PYTHON=%%A\python.exe"
    set "SITEPKG=%%A\Lib\site-packages"
  )
)

:: Takzhe proverkaem naploskim razlozheniem
for /d %%A in ("%APP_DIR%WPy\python-*.amd64") do (
  if exist "%%A\python.exe" (
    set "PYTHON=%%A\python.exe"
    set "SITEPKG=%%A\Lib\site-packages"
  )
)

if defined PYTHON (
  echo  OK: WPy ustanovlen: %PYTHON%
  goto :install_deps
)

echo.
echo  [OSHIBKA] WPy skachan, no python.exe ne nayden.
echo  Prover papku: %APP_DIR%WPy\
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
set /p MANUAL_PY=  Put k python.exe (ili Enter chtoby propustit): 

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
echo  Skopiruyte WPy\ v papku ryadom s install.bat i povtorite.
echo.
pause
exit /b 1

:: ============================================================
:: SHAG 3: Ustanovka zavisimostey
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
  echo  Proverte podklyucheniye k Internetu.
  pause
  exit /b 1
)
echo  OK: zavisimosti ustanovleny.

:: ============================================================
:: SHAG 4: Sozdaniye papok
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
:: SHAG 5: Sozdaniye BD
:: ============================================================
echo.
echo [5/6] Podgotovka bazy dannykh...

if exist "%APP_DIR%db\database.db" (
  echo  Baza dannykh uzhe sushchestvuyet - ne trogaem.
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
    echo  [PREDUPREZHDENIE] db.py vernul oshibku - BD budet sozdana pri pervom zapuske.
  ) else (
    echo  OK: Baza initializirovana cherez db.py
  )
) else (
  echo  [PREDUPREZHDENIE] db.py ne nayden. BD budet sozdana pri pervom zapuske SONAR.
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
  echo  Sozdayu .env s unikalnym SECRET_KEY...
  "%PYTHON%" -c "import secrets; open('.env','w').write('SECRET_KEY=' + secrets.token_hex(32) + '\n')"
  echo  OK: .env sozdan.
  echo.
  echo  [VAZHNO] Dlya avtobnovleniya dobavte v .env:
  echo  GITHUB_TOKEN=vash_token
)

:: ============================================================
:: ITOG
:: ============================================================
echo.
echo ============================================================
echo   Ustanovka zavershena!
echo.
echo   Teper zapusti start SONAR.bat
echo ============================================================
echo.
pause
exit /b 0

:quit
exit /b 0
