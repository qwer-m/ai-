@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

for %%I in ("%~dp0..\..") do set "ROOT_DIR=%%~fI"
set "PYTHON_EXE=%ROOT_DIR%\.venv\Scripts\python.exe"
if not exist "!PYTHON_EXE!" (
    set "PYTHON_EXE=python"
)

REM Fallback check: virtualenv python may be corrupted and fail with Windows error 9020.
REM Verify executable first; if it fails, fallback to system python in PATH.
"!PYTHON_EXE!" -V >nul 2>&1
if errorlevel 1 (
    echo [WARN] Python executable check failed: !PYTHON_EXE!
    echo [WARN] Fallback to system python from PATH.
    set "PYTHON_EXE=python"
)

echo Using Python: !PYTHON_EXE!
echo Starting dev services from: %ROOT_DIR%\backend

if not exist "%ROOT_DIR%\backend\start_dev.py" (
    echo [ERROR] start_dev.py not found at %ROOT_DIR%\backend\start_dev.py
    pause
    exit /b 1
)

pushd "%ROOT_DIR%\backend"
"!PYTHON_EXE!" start_dev.py
set "EXIT_CODE=%ERRORLEVEL%"
popd

if %EXIT_CODE% neq 0 (
    echo [ERROR] Startup failed with code %EXIT_CODE%
)

pause
exit /b %EXIT_CODE%
