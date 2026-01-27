@echo off
cd /d "%~dp0"
title AI测试平台启动脚本
echo ========================================================
echo 正在启动 AI 测试平台开发环境...
echo ========================================================

:: 尝试定位虚拟环境 Python 路径
:: 1. 检查上级目录的 .venv (项目根目录)
if exist "..\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=..\.venv\Scripts\python.exe"
    goto FoundEnv
)

:: 2. 检查当前目录的 .venv
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
    goto FoundEnv
)

:: 3. 检查根目录 (硬编码路径兜底)
if exist "C:\Users\Administrator\Desktop\ai技术辅助测试\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=C:\Users\Administrator\Desktop\ai技术辅助测试\.venv\Scripts\python.exe"
    goto FoundEnv
)

echo [警告] 未自动找到虚拟环境 (.venv)。
echo 将尝试使用系统默认 python 命令...
set "PYTHON_EXE=python"

:FoundEnv
echo 使用 Python 解释器: %PYTHON_EXE%
echo 正在运行启动脚本 (start_dev.py)...
echo --------------------------------------------------------

"%PYTHON_EXE%" start_dev.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [错误] 服务启动失败，退出码: %ERRORLEVEL%
    pause
) else (
    echo.
    echo 服务已停止。
    pause
)
