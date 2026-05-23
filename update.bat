@echo off
chcp 65001 >nul
setlocal

:: ============================================================
::  SONAR Updater — скачивает последние изменения кода с GitHub
::  Не трогает: db\, uploads\, reports\, WPy\
:: ============================================================

set "APP_DIR=%~dp0"
set "UPDATER=%APP_DIR%_updater.py"
set "PYTHON="

:: ── Автопоиск python.exe ─────────────────────────────────────
for /d %%A in ("%APP_DIR%WPy\python-*.amd64") do (
  if exist "%%A\python.exe" set "PYTHON=%%A\python.exe"
)

echo.
echo  ================================================
echo   SONAR — Обновление кода из GitHub
echo  ================================================
echo.

if not defined PYTHON (
  echo [ОШИБКА] Python не найден в WPy\python-*.amd64\
  pause
  exit /b 1
)

"%PYTHON%" "%UPDATER%"

echo.
pause
