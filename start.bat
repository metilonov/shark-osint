@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 goto :fail
)
".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 11))"
if errorlevel 1 (
    echo Install Python 3.11 or later and recreate .venv.
    goto :fail
)
if not exist ".venv\.shark-deps-installed" (
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :fail
    type nul > ".venv\.shark-deps-installed"
)
if not exist ".env" (
    copy .env.example .env >nul
    echo Fill BOT_TOKEN and ADMIN_TELEGRAM_ID in .env, then run start.bat again.
    notepad .env
    exit /b 1
)
".venv\Scripts\python.exe" main.py
if errorlevel 1 goto :fail
exit /b 0
:fail
echo Startup failed. See the output above and README.md.
pause
exit /b 1
