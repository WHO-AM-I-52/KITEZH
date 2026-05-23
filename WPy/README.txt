=================================================
  SONAR — Установка портативного Python
  SONAR — WinPython Portable Installation Guide
=================================================

╔═══════════════════════════════════════════════╗
║  РУС  ║  Русский                              ║
╚═══════════════════════════════════════════════╝

Эта папка предназначена для портативной версии
WinPython (Python 3.12). Сам Python не входит
в поставку и устанавливается отдельно.

-------------------------------------------------
  ШАГ 1 — Скачать WinPython
-------------------------------------------------

Открой в браузере:
https://github.com/winpython/winpython/releases

Найди последний релиз WinPython 3.12.x (64-bit).
Скачай файл вида:
  Winpython64-3.12.X.Xdot.exe  (рекомендуется)
  или
  Winpython64-3.12.X.X.exe     (полная версия)

-------------------------------------------------
  ШАГ 2 — Установка
-------------------------------------------------

1. Запусти скачанный .exe
2. В окне выбора пути УКАЖИ ЭТУ ПАПКУ:

   ...\SONAR\WPy\

   Пример:
   G:\Programs\SONAR\WPy\

3. Дождись распаковки.
4. После установки в папке WPy\ появится:
   python-3.12.X.amd64\python.exe

-------------------------------------------------
  ШАГ 3 — Установка зависимостей
-------------------------------------------------

Открой командную строку в папке SONAR и выполни:

  WPy\python-3.12.4.amd64\python.exe -m pip install -r requirements.txt

-------------------------------------------------
  ШАГ 4 — Запуск
-------------------------------------------------

Дважды кликни start.bat — система запустится
и откроет сервер на http://localhost:5000

-------------------------------------------------
  Поддержка: @whoami52
  GitHub: https://github.com/WHO-AM-I-52/SONAR
-------------------------------------------------


╔═══════════════════════════════════════════════╗
║  ENG  ║  English                              ║
╚═══════════════════════════════════════════════╝

This folder is intended for a portable WinPython
(Python 3.12) installation. Python is not bundled
and must be installed separately.

-------------------------------------------------
  STEP 1 — Download WinPython
-------------------------------------------------

Open in your browser:
https://github.com/winpython/winpython/releases

Find the latest WinPython 3.12.x (64-bit) release.
Download a file like:
  Winpython64-3.12.X.Xdot.exe  (recommended)
  or
  Winpython64-3.12.X.X.exe     (full version)

-------------------------------------------------
  STEP 2 — Installation
-------------------------------------------------

1. Run the downloaded .exe
2. When asked for installation path, SELECT THIS FOLDER:

   ...\SONAR\WPy\

   Example:
   G:\Programs\LandApp.bacup\WPy\

3. Wait for extraction to complete.
4. After installation, this folder should contain:
   python-3.12.X.amd64\python.exe

-------------------------------------------------
  STEP 3 — Install dependencies
-------------------------------------------------

Open a command prompt in the SONAR folder and run:

  WPy\python-3.12.4.amd64\python.exe -m pip install -r requirements.txt

-------------------------------------------------
  STEP 4 — Launch
-------------------------------------------------

Double-click start.bat — the server will start
and be available at http://localhost:5000

-------------------------------------------------
  Support: @whoami52
  GitHub: https://github.com/WHO-AM-I-52/SONAR
-------------------------------------------------