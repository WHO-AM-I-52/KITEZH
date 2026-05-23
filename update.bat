@echo off
chcp 65001 >nul
setlocal

:: ============================================================
::  SONAR Updater — скачивает последние изменения кода с GitHub
::  Не трогает: db\, uploads\, reports\, WPy64-31100\
:: ============================================================

set REPO_URL=https://github.com/WHO-AM-I-52/SONAR
set APP_DIR=%~dp0
set PYTHON=%APP_DIR%WPy64-31100\WPy64-31241\python-3.12.4.amd64\python.exe
set UPDATER=%APP_DIR%_updater.py

echo.
echo  ================================================
echo   SONAR — Обновление кода из GitHub
echo  ================================================
echo.

:: Проверяем наличие Python
if not exist "%PYTHON%" (
    echo [ОШИБКА] Python не найден: %PYTHON%
    pause
    exit /b 1
)

:: Запускаем Python-скрипт обновления
"%PYTHON%" "%UPDATER%"

echo.
pause