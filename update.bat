@echo off
chcp 65001 >nul
setlocal

:: ============================================================
::  SONAR Updater
:: ============================================================

set "APP_DIR=%~dp0"
set "UPDATER=%APP_DIR%_updater.py"
set "PYTHON="

:: [1] WPy\python313 (osnovnoy variant)
if exist "%APP_DIR%WPy\python313\python.exe" (
  set "PYTHON=%APP_DIR%WPy\python313\python.exe"
)

:: [2] Lyuboy WPy\python* ryadom
if not defined PYTHON (
  for /d %%A in ("%APP_DIR%WPy\python*") do (
    if exist "%%A\python.exe" set "PYTHON=%%A\python.exe"
  )
)

:: [3] WPy v sosednikh papkakh
if not defined PYTHON (
  for /d %%B in ("%APP_DIR%..\") do (
    for /d %%A in ("%%B\WPy\python*") do (
      if exist "%%A\python.exe" set "PYTHON=%%A\python.exe"
    )
  )
)

:: [4] Fallback: sistemnyy Python
if not defined PYTHON (
  where python >nul 2>&1 && set "PYTHON=python"
)

echo.
echo  ================================================
echo   SONAR - Obnovleniye koda iz GitHub
echo  ================================================
echo.

if not defined PYTHON (
  echo  [OSHIBKA] Python ne nayden.
  echo  Ubedis chto papka WPy nakhoditsya ryadom s SONAR.
  pause
  exit /b 1
)

echo  Python: %PYTHON%
echo.

"%PYTHON%" "%UPDATER%"
set UPDATER_EXIT=%ERRORLEVEL%

echo.
pause

:: Yesli _updater.py vernul 2 — bat obnovilsya
if "%UPDATER_EXIT%"=="2" (
  echo.
  echo  [!] start SONAR.bat byl obnovlen.
  echo  [!] Zakroy eto okno i zapusti start SONAR.bat zanovo vruchnuyu.
  echo.
  pause
  exit /b 0
)

exit /b 0
