@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [.venv] не найден. Создайте окружение и установите зависимости:
    echo   python -m venv .venv
    echo   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

".venv\Scripts\python.exe" bot.py
