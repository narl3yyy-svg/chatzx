@echo off
REM chatxz on Windows — use THIS from cmd (NOT .\run.ps1 — that opens VS Code).
setlocal EnableExtensions
cd /d "%~dp0"
set "CHATXZ_ROOT=%CD%"
set "PYTHONPATH=%CD%"
set "PYTHONUNBUFFERED=1"

if "%~1"=="" goto :usage
if /I "%~1"=="help" goto :usage
if /I "%~1"=="-h" goto :usage
if /I "%~1"=="--help" goto :usage
if /I "%~1"=="install" goto :install
if /I "%~1"=="cli" goto :cli
if /I "%~1"=="web" goto :web
if /I "%~1"=="server" goto :web
if "%~1:~0,1%"=="-" goto :web

:usage
echo.
echo ============================================================
echo   FROM CMD use:
echo     run web --share
echo     run web --share --debug
echo.
echo   Do NOT use:  .\run.ps1
echo   Windows opens .ps1 in VS Code — the server never starts.
echo ============================================================
echo.
echo   PowerShell:  .\run.ps1 web --share --debug
echo   Git Bash:    ./run.sh web --share --debug
echo.
exit /b 1

:install
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" install
exit /b %ERRORLEVEL%

:cli
call :ensure_venv
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -u -m chatxz.app %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:web
call :ensure_venv
if errorlevel 1 exit /b 1
echo.
echo chatxz web server
echo Web UI:  http://127.0.0.1:8742
echo Press Ctrl+C to stop — all logs print in this window
echo.
".venv\Scripts\python.exe" -u -m chatxz.web.server %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:ensure_venv
if exist ".venv\Scripts\python.exe" exit /b 0
echo First run: installing dependencies into .venv ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" install
exit /b %ERRORLEVEL%