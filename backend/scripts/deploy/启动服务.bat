@echo off
chcp 65001 >nul
call "%~dp0..\..\..\启动服务.bat"
exit /b %ERRORLEVEL%
