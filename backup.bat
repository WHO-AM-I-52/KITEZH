@echo off
chcp 65001 >nul
setlocal

set "ROOT=%~dp0"
set "PYTHON=%ROOT%WPy\python313\python.exe"

if not exist "%PYTHON%" (
    echo [ERROR] Python не найден: %PYTHON%
    exit /b 1
)

pushd "%ROOT%" || exit /b 1
"%PYTHON%" -m services.backup_scheduler
set "RESULT=%ERRORLEVEL%"
popd

if not "%RESULT%"=="0" (
    echo [ERROR] Резервное копирование не завершено успешно.
    exit /b %RESULT%
)

echo [OK] Резервное копирование завершено.
exit /b 0
