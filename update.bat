@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:: ============================================================
::  KITEZH - Update archive download from GitHub
:: ============================================================

set "APP_DIR=%~dp0"
set "UPDATER=%APP_DIR%updater\_updater.py"
set "PYTHON="

:: [1] WPy\python313 (primary)
if exist "%APP_DIR%WPy\python313\python.exe" (
  set "PYTHON=%APP_DIR%WPy\python313\python.exe"
)

:: [2] Any WPy\python* nearby
if not defined PYTHON (
  for /d %%A in ("%APP_DIR%WPy\python*") do (
    if exist "%%A\python.exe" set "PYTHON=%%A\python.exe"
  )
)

:: [3] WPy in sibling folders
if not defined PYTHON (
  for /d %%B in ("%APP_DIR%..\*") do (
    for /d %%A in ("%%B\WPy\python*") do (
      if exist "%%A\python.exe" set "PYTHON=%%A\python.exe"
    )
  )
)

:: [4] Fallback: system Python
if not defined PYTHON (
  where python >nul 2>&1 && set "PYTHON=python"
)

echo.
echo  ================================================
echo   KITEZH - Update archive download from GitHub
echo  ================================================
echo.

if not defined PYTHON (
  echo  [ERROR] Python not found.
  echo  Make sure WPy folder is located next to KITEZH.
  pause
  exit /b 1
)

echo  Python: %PYTHON%
echo.




"%PYTHON%" "%UPDATER%"
set "UPDATER_EXIT=!ERRORLEVEL!"

:: FIX #5: удаляем .maintenance как страховка — если Python упал до удаления или
:: обновление запущено вручную — флаг не останется висеть
del /f /q "%APP_DIR%.maintenance" 2>nul

echo.
pause

:: If _updater.py returned 2 - bat file was updated
if "%UPDATER_EXIT%"=="2" (
  echo.
  echo  [!] start KITEZH.bat was updated.
  echo  [!] Close this window and run start KITEZH.bat manually.
  echo.
  pause
  exit /b 0
)

exit /b 0
