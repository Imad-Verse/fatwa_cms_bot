@echo off
title Fatwa CMS Bot - Runner
echo Starting Fatwa CMS Bot...

:: 1. Check for Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed or not in PATH.
    pause
    exit /b
)

:: 2. Create Venv if not exists
if not exist venv\Scripts\python.exe (
    echo [1/2] Creating virtual environment...
    if exist venv rmdir /s /q venv
    python -m venv venv
    if %errorlevel% neq 0 (
        echo ERROR: Failed to create virtual environment. 
        echo Trying 'py -m venv' fallback...
        py -m venv venv
    )
)

:: 3. Verify Venv
if not exist venv\Scripts\python.exe (
    echo ERROR: Virtual environment folder is incomplete. 
    echo Please try running 'python -m venv venv' manually in this folder.
    pause
    exit /b
)

:: 4. Install dependencies
echo [2/2] Checking/Installing dependencies...
venv\Scripts\python -m pip install --upgrade pip
venv\Scripts\pip install -r requirements.txt

echo.
echo ==========================================
echo    BOT IS STARTING NOW...
echo ==========================================
echo.

venv\Scripts\python main.py
pause
