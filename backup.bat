@echo off
chcp 65001 >nul
setlocal

echo ================================================
echo   SONAR — Резервное копирование данных
echo ================================================
echo.

set "ROOT=%~dp0"
set "DATE_TAG=%date:~6,4%-%date:~3,2%-%date:~0,2%"
set "DST=%ROOT%backups\%DATE_TAG%"

echo Дата резервной копии: %DATE_TAG%
echo Папка назначения    : %DST%
echo.

if not exist "%DST%" mkdir "%DST%"

:: -- database.db
echo Копирую database.db...
if exist "%ROOT%db\database.db" (
    if not exist "%DST%\db" mkdir "%DST%\db"
    copy /Y "%ROOT%db\database.db" "%DST%\db\database.db" >nul
    echo [OK] database.db
) else (
    echo [--] database.db не найден — пропуск
)

:: -- uploads\
echo Копирую uploads\...
if exist "%ROOT%uploads" (
    robocopy "%ROOT%uploads" "%DST%\uploads" /MIR /NFL /NDL /NJH /NJS 2>nul
    echo [OK] uploads\
) else (
    echo [--] uploads\ не найдена — пропуск
)

:: -- reports\
echo Копирую reports\...
if exist "%ROOT%reports" (
    robocopy "%ROOT%reports" "%DST%\reports" /MIR /NFL /NDL /NJH /NJS 2>nul
    echo [OK] reports\
) else (
    echo [--] reports\ не найдена — пропуск
)

echo.
echo ================================================
echo  Готово! Копия сохранена в:
echo  %DST%
echo ================================================
echo.
