@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ==========================================
echo       AI Test Platform Launcher
echo ==========================================

set "ROOT_DIR=%~dp0"
set "VENV_DIR=%ROOT_DIR%.venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
if not exist "!PYTHON_EXE!" (
    echo Project virtual environment not found. Creating: !VENV_DIR!
    python -m venv "!VENV_DIR!"
    if errorlevel 1 (
        echo [ERROR] Failed to create project virtual environment.
        pause
        exit /b 1
    )
)

REM Never fall back to global Python; keep all dependencies inside the project.
"!PYTHON_EXE!" -V >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Project virtual environment Python is unavailable: !PYTHON_EXE!
    echo [ERROR] Recreate .venv before starting the platform.
    pause
    exit /b 1
)

if not exist "%ROOT_DIR%backend\start_dev.py" (
    echo [ERROR] backend\start_dev.py not found.
    echo Current dir: %CD%
    pause
    exit /b 1
)

echo Starting development environment...
echo Using Python: !PYTHON_EXE!
echo.

set "PYTHONUNBUFFERED=1"
"!PYTHON_EXE!" "%ROOT_DIR%backend\bootstrap_dependencies.py"
if errorlevel 1 (
    echo.
    echo [ERROR] Python dependency synchronization failed. Services were not started.
    pause
    exit /b 1
)

pushd "%ROOT_DIR%backend"
"!PYTHON_EXE!" start_dev.py
set "EXIT_CODE=%ERRORLEVEL%"
popd

if %EXIT_CODE% neq 0 (
    echo.
    echo [WARNING] Process exited with error code %EXIT_CODE%.
)

pause
exit /b %EXIT_CODE%
