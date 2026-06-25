@echo off
REM Install chatxz on Windows: Python venv + dependencies. Then use run.bat web --share
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo chatxz Windows Install
echo ======================
echo.

call "%~dp0scripts\windows-python.bat"
if errorlevel 1 (
  echo Python 3.10+ not found.
  echo.
  echo Install from https://www.python.org/downloads/windows/
  echo Check "Add python.exe to PATH" during setup, then run install.bat again.
  echo.
  where winget >nul 2>&1 && (
    set /p WINGET=Install Python 3.12 with winget now? [Y/n]:
    if /I not "!WINGET!"=="n" (
      winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements
      call "%~dp0scripts\windows-python.bat"
    )
  )
)
if not defined PYTHON_EXE if not defined USE_PY_LAUNCHER (
  echo.
  echo Still no Python 3.10+. Install Python and re-run install.bat
  exit /b 1
)

if defined PYTHON_EXE (
  for /f "usebackq delims=" %%V in (`"%PYTHON_EXE%" -c "import sys; print(sys.version.split()[0])" 2^>nul`) do set "PYVER=%%V"
  if defined PYVER (echo Using Python !PYVER!) else (echo Using Python: %PYTHON_EXE%)
) else (
  echo Using Python via py -3
)

call :stop_server

set "VENV_PY=%CD%\.venv\Scripts\python.exe"
set "REUSE_VENV=0"
if exist "%VENV_PY%" if /I not "%~1"=="--reinstall" (
  "%VENV_PY%" -m pip --version >nul 2>&1
  if not errorlevel 1 set "REUSE_VENV=1"
)
if "%REUSE_VENV%"=="1" (
  echo Using existing .venv ^(pass --reinstall to recreate^)
  goto :install_deps
)

if exist ".venv" (
  echo Removing broken or old .venv ...
  rmdir /s /q ".venv" 2>nul
  ping -n 3 127.0.0.1 >nul
)

echo Creating virtual environment .venv ...
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

:install_deps
echo Upgrading pip...
"%VENV_PY%" -m pip install --upgrade pip
echo Installing rns + aiohttp...
"%VENV_PY%" -m pip install "rns>=1.3.0" "aiohttp>=3.9.0"
echo Installing chatxz...
"%VENV_PY%" -m pip install -e .

echo.
echo Install complete.
echo.
echo Start server (logs in this cmd window):
echo   run.bat web --share
echo.
echo Debug logs:
echo   run.bat web --share --debug
echo.

if /I "%~1"=="--start" (
  call "%~dp0run.bat" web --share %2 %3 %4 %5 %6 %7 %8 %9
)
endlocal
exit /b 0

:stop_server
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8742" ^| findstr "LISTENING"') do (
  taskkill /F /PID %%a >nul 2>&1
)
exit /b 0