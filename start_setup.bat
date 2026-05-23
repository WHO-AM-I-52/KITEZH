@echo off
chcp 65001 >nul
echo Установка библиотек...
pip install flask openpyxl werkzeug
echo.
echo Готово! Теперь запускайте start.bat
pause
