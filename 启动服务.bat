@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

echo ==========================================
echo       AI Test Platform Launcher
echo ==========================================

set "ROOT_DIR=%~dp0"
set "ENV_DIR=%ROOT_DIR%.conda"
set "PYTHON_EXE=%ENV_DIR%\python.exe"
if not exist "%PYTHON_EXE%" (
    where micromamba >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Install micromamba, then run this launcher to create the Python 3.15.2 environment.
        pause
        exit /b 1
    )
    micromamba create --yes --prefix "%ENV_DIR%" --file "%ROOT_DIR%environment.yml"
    if errorlevel 1 (
        pause
        exit /b 1
    )
)

set "PATH=%ENV_DIR%;%ENV_DIR%\Scripts;%ENV_DIR%\Library\bin;%PATH%"
"%PYTHON_EXE%" -c "import sys; raise SystemExit(sys.version_info[:3] != (3, 15, 2))"
if errorlevel 1 (
    echo [ERROR] The project environment must use Python 3.15.2: %PYTHON_EXE%
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
echo Using Python: %PYTHON_EXE%
echo.

set "PYTHONUNBUFFERED=1"
"%PYTHON_EXE%" "%ROOT_DIR%backend\bootstrap_dependencies.py"
if errorlevel 1 (
    echo.
    echo [ERROR] Python dependency synchronization failed. Services were not started.
    pause
    exit /b 1
)

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
