@echo off
REM Run chatxz web server from cmd. Install first with install.bat if needed.
setlocal EnableExtensions
cd /d "%~dp0"
set "CHATXZ_ROOT=%CD%"
set "PYTHONPATH=%CD%"
set "PYTHONUNBUFFERED=1"

if "%~1"=="" goto :usage
if /I "%~1"=="help" goto :usage
if /I "%~1"=="-h" goto :usage
if /I "%~1"=="--help" goto :usage
if /I "%~1"=="cli" goto :cli
if /I "%~1"=="web" goto :web
if /I "%~1"=="server" goto :web
if "%~1:~0,1%"=="-" goto :web

:usage
echo.
echo Usage:
echo   run.bat web --share
echo   run.bat web --share --debug
echo   run.bat cli [options]
echo.
echo First time:  install.bat
echo Remove all:  uninstall.bat
echo.
exit /b 1

:cli
call :ensure_install
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -u -m chatxz.app %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:web
call :ensure_install
if errorlevel 1 exit /b 1
echo.
echo chatxz web server
echo Web UI:  http://127.0.0.1:8742
echo Logs below — press Ctrl+C to stop
echo.
".venv\Scripts\python.exe" -u -m chatxz.web.server %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:ensure_install
if not exist ".venv\Scripts\python.exe" goto :do_install
".venv\Scripts\python.exe" -m pip --version >nul 2>&1
if not errorlevel 1 exit /b 0
echo Broken install ^(pip missing^). Re-running install.bat ...
:do_install
if not exist ".venv\Scripts\python.exe" echo Not installed. Running install.bat ...
call "%~dp0install.bat"
exit /b %ERRORLEVEL%