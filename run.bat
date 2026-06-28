@echo off
REM Run chatxz from this git clone folder in cmd. Ctrl+C stops everything.
setlocal EnableExtensions EnableDelayedExpansion
break off
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
echo Open cmd in this folder and run the command above.
echo Ctrl+C stops the server and releases all ports.
echo Cleanup:  uninstall.bat
echo.
exit /b 1

:cli
call :resolve_python
if errorlevel 1 exit /b 1
call "%~dp0scripts\stop-chatxz.bat"
set "CHATXZ_MODULE=chatxz.app"
"%CHATXZ_PYTHON%" -u -m %CHATXZ_MODULE% %2 %3 %4 %5 %6 %7 %8 %9
set "EXIT_CODE=%ERRORLEVEL%"
call "%~dp0scripts\stop-chatxz.bat"
exit /b %EXIT_CODE%

:web
call :resolve_python
if errorlevel 1 exit /b 1
call "%~dp0scripts\stop-chatxz.bat"
echo.
echo chatxz web server
echo Web UI:  http://127.0.0.1:8742
echo Press Ctrl+C to stop - all ports will be released
echo.
set "CHATXZ_MODULE=chatxz.web.server"
"%CHATXZ_PYTHON%" -u -m %CHATXZ_MODULE% %2 %3 %4 %5 %6 %7 %8 %9
set "EXIT_CODE=%ERRORLEVEL%"
call "%~dp0scripts\stop-chatxz.bat"
if "%EXIT_CODE%"=="0" (
  echo [stopped] Server and ports closed.
) else if "%EXIT_CODE%"=="130" (
  echo [stopped] Server and ports closed.
  set "EXIT_CODE=0"
) else (
  echo [stopped] Server stopped ^(exit %EXIT_CODE%^). Ports cleaned up.
)
exit /b %EXIT_CODE%

:resolve_python
set "CHATXZ_PYTHON="
if exist "%VENV_PY%" if exist ".venv\.ready" (
  "%VENV_PY%" -c "import RNS, aiohttp" >nul 2>&1
  if not errorlevel 1 (
    call :ensure_voice_deps
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

:ensure_voice_deps
"%VENV_PY%" -c "from chatxz.core.call_audio_engine import call_audio_available; import sys; sys.exit(0 if call_audio_available() else 1)" >nul 2>&1
if not errorlevel 1 exit /b 0
echo Installing voice dependencies (pyaudio)...
"%VENV_PY%" -m pip install -q pyaudio 2>nul
exit /b 0

:ensure_deps
if exist "%VENV_PY%" (
  "%VENV_PY%" -m pip --version >nul 2>&1
  if not errorlevel 1 (
    "%VENV_PY%" -c "import RNS, aiohttp" >nul 2>&1
    if not errorlevel 1 (
      call :ensure_voice_deps
      echo. > ".venv\.ready"
      set "CHATXZ_PYTHON=%VENV_PY%"
      exit /b 0
    )
  )
  call "%~dp0scripts\stop-chatxz.bat"
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
call :ensure_voice_deps
echo. > ".venv\.ready"
set "CHATXZ_PYTHON=%VENV_PY%"
exit /b 0