@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Land App - OCR setup

echo Используем Python из портативного пакета...
set "PYTHON=%~dp0WPy64-31100\\WPy64-31241\\python-3.12.4.amd64\\python.exe"

if not exist "%PYTHON%" (
  echo [ERROR] Python not found: %PYTHON%
  pause
  exit /b 1
)

echo OK: %PYTHON%
echo.
echo Установка библиотек для OCR (pdfplumber, python-docx)...
"%PYTHON%" -m pip install pdfplumber python-docx

echo.
echo Готово. Если ошибок не было, можно запускать сервер (start.bat).
pause