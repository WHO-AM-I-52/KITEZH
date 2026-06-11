@echo off
chcp 65001 >nul
setlocal

:: ============================================================
::  KITEZH - Скачивание архива обновления с GitHub
:: ============================================================

set "APP_DIR=%~dp0"
set "UPDATER=%APP_DIR%_updater.py"
set "PYTHON="

:: [1] WPy\python313 (основной вариант)
if exist "%APP_DIR%WPy\python313\python.exe" (
  set "PYTHON=%APP_DIR%WPy\python313\python.exe"
)

:: [2] Любой WPy\python* рядом
if not defined PYTHON (
  for /d %%A in ("%APP_DIR%WPy\python*") do (
    if exist "%%A\python.exe" set "PYTHON=%%A\python.exe"
  )
)

:: [3] WPy в соседних папках
if not defined PYTHON (
  for /d %%B in ("%APP_DIR%..\") do (
    for /d %%A in ("%%B\WPy\python*") do (
      if exist "%%A\python.exe" set "PYTHON=%%A\python.exe"
    )
  )
)

:: [4] Запасной вариант: системный Python
if not defined PYTHON (
  where python >nul 2>&1 && set "PYTHON=python"
)

echo.
echo  ================================================
echo   KITEZH - Скачивание архива обновления с GitHub
echo  ================================================
echo.

if not defined PYTHON (
  echo  [ОШИБКА] Python не найден.
  echo  Убедись, что папка WPy находится рядом с KITEZH.
  pause
  exit /b 1
)

echo  Python: %PYTHON%
echo.

"%PYTHON%" "%UPDATER%"
set UPDATER_EXIT=%ERRORLEVEL%

echo.
pause

:: Если _updater.py вернул 2 — bat-файл был обновлён
if "%UPDATER_EXIT%"=="2" (
  echo.
  echo  [!] start KITEZH.bat был обновлён.
  echo  [!] Закрой это окно и запусти start KITEZH.bat заново вручную.
  echo.
  pause
  exit /b 0
)

exit /b 0
