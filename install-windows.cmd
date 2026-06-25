@echo off
REM First-time chatxz setup on Windows (same role as install.sh on Linux).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-windows.ps1" %*
if errorlevel 1 exit /b 1
echo.
echo Start server:  run.ps1 web --share
pause