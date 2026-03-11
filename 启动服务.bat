@echo off
setlocal
cd /d "%~dp0"

echo ==========================================
echo       AI Test Platform Launcher
echo ==========================================

set "ROOT_DIR=%~dp0"
set "PYTHON_EXE=%ROOT_DIR%.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    set "PYTHON_EXE=python"
)

if not exist "%ROOT_DIR%backend\start_dev.py" (
    echo [ERROR] backend\start_dev.py not found.
    echo Current dir: %CD%
    pause
    exit /b 1
)

echo Starting development environment...
echo.
pushd "%ROOT_DIR%backend"
"%PYTHON_EXE%" start_dev.py
set "EXIT_CODE=%ERRORLEVEL%"
popd

if %EXIT_CODE% neq 0 (
    echo.
    echo [WARNING] Process exited with error code %EXIT_CODE%.
)

pause
exit /b %EXIT_CODE%
