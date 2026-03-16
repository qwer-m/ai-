@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo ==========================================
echo       AI Test Platform Launcher
echo ==========================================

set "ROOT_DIR=%~dp0"
set "PYTHON_EXE=%ROOT_DIR%.venv\Scripts\python.exe"
if not exist "!PYTHON_EXE!" (
    set "PYTHON_EXE=python"
)

REM Fallback check: virtualenv python may exist but be corrupted (Windows error 9020).
REM Probe executable first; if it fails, fallback to system python in PATH.
"!PYTHON_EXE!" -V >nul 2>&1
if errorlevel 1 (
    echo [WARN] Python executable check failed: !PYTHON_EXE!
    echo [WARN] Fallback to system python from PATH.
    set "PYTHON_EXE=python"
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
