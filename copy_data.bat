@echo off
chcp 65001 >nul
setlocal

echo ╔══════════════════════════════════════════════════╗
echo ║         Копирование данных в LandApp             ║
echo ╚══════════════════════════════════════════════════╝
echo.

:: ── Пути ────────────────────────────────────────────────────
set "SRC=%~dp0..\LandApp.bacup"
set "DST=%~dp0"

:: Проверяем что папка-источник существует
if not exist "%SRC%" (
    echo  [ОШИБКА] Папка LandApp.bacup не найдена по пути:
    echo           %SRC%
    echo.
    echo  Убедись что LandApp и LandApp.bacup лежат рядом.
    pause
    exit /b 1
)

echo  Источник : %SRC%
echo  Назначение: %DST%
echo.

:: ── .env ────────────────────────────────────────────────────
echo  Копирую .env...
if exist "%SRC%\.env" (
    copy /Y "%SRC%\.env" "%DST%\.env" >nul
    echo  [OK] .env
) else (
    echo  [--] .env не найден в источнике — пропуск
)

:: ── database.db ─────────────────────────────────────────────
echo  Копирую database.db...
if exist "%SRC%\database.db" (
    copy /Y "%SRC%\database.db" "%DST%\database.db" >nul
    echo  [OK] database.db
) else (
    echo  [--] database.db не найден — пропуск
)

:: ── uploads\ ────────────────────────────────────────────────
echo  Копирую uploads\...
if exist "%SRC%\uploads" (
    xcopy /E /I /Y /Q "%SRC%\uploads" "%DST%\uploads" >nul
    echo  [OK] uploads\
) else (
    echo  [--] uploads\ не найден — пропуск
)

:: ── reports\ ────────────────────────────────────────────────
echo  Копирую reports\...
if exist "%SRC%\reports" (
    xcopy /E /I /Y /Q "%SRC%\reports" "%DST%\reports" >nul
    echo  [OK] reports\
) else (
    echo  [--] reports\ не найден — пропуск
)

echo.
echo ══════════════════════════════════════════════════
echo  Готово! Теперь можно запускать start SONAR.bat
echo ══════════════════════════════════════════════════
echo.
pause
