@echo off
REM Run chatxz from this git clone folder in cmd. No install step.
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
set "CHATXZ_ROOT=%CD%"
set "PYTHONPATH=%CD%"
set "PYTHONUNBUFFERED=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "VENV_PY=%CD%\.venv\Scripts\python.exe"

if "%~1"=="" goto usage
if /I "%~1"=="help" goto usage
if /I "%~1"=="-h" goto usage
if /I "%~1"=="--help" goto usage
if /I "%~1"=="cli" goto cli
if /I "%~1"=="web" goto web
if /I "%~1"=="server" goto web
if "%~1:~0,1%"=="-" goto web
goto usage

:usage
echo.
echo Usage:
echo   run.bat web --share
echo   run.bat web --share --debug
echo   run.bat cli [options]
echo.
echo Clone the repo, open cmd in this folder, run the command above.
echo First start only: downloads rns + aiohttp into local .venv
echo Cleanup:  uninstall.bat
echo.
echo Do NOT use run.ps1 or PowerShell.
echo.
exit /b 1

:cli
call :resolve_python
if errorlevel 1 exit /b 1
"%CHATXZ_PYTHON%" -u -m chatxz.app %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:web
call :resolve_python
if errorlevel 1 exit /b 1
echo.
echo chatxz web server
echo Web UI:  http://127.0.0.1:8742
echo Logs below - press Ctrl+C to stop
echo.
"%CHATXZ_PYTHON%" -u -m chatxz.web.server %2 %3 %4 %5 %6 %7 %8 %9
exit /b %ERRORLEVEL%

:resolve_python
set "CHATXZ_PYTHON="
if exist "%VENV_PY%" if exist ".venv\.ready" (
  "%VENV_PY%" -c "import rns, aiohttp" >nul 2>&1
  if not errorlevel 1 (
    set "CHATXZ_PYTHON=%VENV_PY%"
    exit /b 0
  )
)
call :ensure_python
if errorlevel 1 exit /b 1
call :ensure_deps
if errorlevel 1 exit /b 1
exit /b 0

:ensure_python
call "%~dp0scripts\windows-python.bat"
if errorlevel 1 (
  echo.
  echo Python 3.10+ not found.
  echo Install from https://www.python.org/downloads/windows/
  echo Check "Add python.exe to PATH", then run:  run.bat web --share
  echo.
  exit /b 1
)
exit /b 0

:ensure_deps
if exist "%VENV_PY%" (
  "%VENV_PY%" -m pip --version >nul 2>&1
  if not errorlevel 1 (
    "%VENV_PY%" -c "import rns, aiohttp" >nul 2>&1
    if not errorlevel 1 (
      echo. > ".venv\.ready"
      set "CHATXZ_PYTHON=%VENV_PY%"
      exit /b 0
    )
  )
  call :stop_server
  rmdir /s /q ".venv" 2>nul
  ping -n 2 127.0.0.1 >nul
)

echo First run: setting up rns + aiohttp in .venv ...
if defined USE_PY_LAUNCHER (
  py -3 -m venv ".venv"
) else (
  "%PYTHON_EXE%" -m venv ".venv"
)
if errorlevel 1 (
  echo Failed to create .venv
  exit /b 1
)

set "VENV_PY=%CD%\.venv\Scripts\python.exe"
"%VENV_PY%" -m pip install -q "rns>=1.3.0" "aiohttp>=3.9.0"
if errorlevel 1 (
  echo Failed to install dependencies.
  exit /b 1
)
echo. > ".venv\.ready"
set "CHATXZ_PYTHON=%VENV_PY%"
exit /b 0

:stop_server
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8742" ^| findstr "LISTENING"') do (
  taskkill /F /PID %%a >nul 2>&1
)
exit /b 0