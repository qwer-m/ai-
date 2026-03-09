@echo off
cd /d "%~dp0"

echo ==========================================
echo       AI Test Platform Launcher
echo ==========================================

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.11+ and add it to PATH.
    pause
    exit /b
)

:: Check Backend Dir
if not exist "backend" (
    echo [ERROR] Directory 'backend' not found.
    echo Current dir: %cd%
    pause
    exit /b
)

cd backend

:: Check Script
if not exist "start_dev.py" (
    echo [ERROR] Script 'start_dev.py' not found in backend directory.
    echo Current dir: %cd%
    pause
    exit /b
)

echo Starting development environment...
echo.
python start_dev.py

if %errorlevel% neq 0 (
    echo.
    echo [WARNING] Process exited with error code %errorlevel%.
)

pause
cd ..
