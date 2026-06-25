@echo off
REM chatxz on Windows — run from cmd (NOT .\run.ps1 — that opens in an editor).
setlocal EnableExtensions
cd /d "%~dp0"
set "CHATXZ_ROOT=%CD%"
set "PYTHONPATH=%CD%"
set "PYTHONUNBUFFERED=1"

set "SUB=%~1"
if /I "%SUB%"=="web" goto :web
if /I "%SUB%"=="server" goto :web
if /I "%SUB%"=="cli" goto :cli
if /I "%SUB%"=="install" goto :install
if "%SUB%"=="" goto :usage

:usage
echo.
echo chatxz - run from cmd:
echo   run.cmd web --share
echo   run.cmd web --share --debug
echo.
echo Do NOT use .\run.ps1 from cmd — Windows opens .ps1 in an editor, not PowerShell.
echo From PowerShell use:  .\run.ps1 web --share
echo From Git Bash use:     ./run.sh web --share
echo.
exit /b 1

:install
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" install
exit /b %ERRORLEVEL%

:cli
shift
call :ensure_venv
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -u -m chatxz.app %*
exit /b %ERRORLEVEL%

:web
shift
call :ensure_venv
if errorlevel 1 exit /b 1
echo chatxz web server
echo Web UI:  http://127.0.0.1:8742
echo Logs below — press Ctrl+C to stop
echo.
".venv\Scripts\python.exe" -u -m chatxz.web.server %*
exit /b %ERRORLEVEL%

:ensure_venv
if exist ".venv\Scripts\python.exe" exit /b 0
echo First run: installing dependencies into .venv ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" install
exit /b %ERRORLEVEL%