@echo off
REM Remove chatxz install (.venv) and optionally app data on Windows.
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo chatxz Windows Uninstall
echo ========================
echo.

echo [1/4] Stopping chatxz server and releasing ports...
call "%~dp0scripts\stop-chatxz.bat"
echo   Done.

echo [2/4] Removing Python environment...
if exist ".venv" (
  rmdir /s /q ".venv"
  echo   Removed .venv
) else (
  echo   No .venv found
)
if exist "chatxz.egg-info" (
  rmdir /s /q "chatxz.egg-info"
  echo   Removed chatxz.egg-info
)

echo [3/4] Application data (identity, settings, chat history)...
set "CONFIG_DIR=%USERPROFILE%\.config\chatxz"
set "DATA_DIR=%USERPROFILE%\.local\share\chatxz"
if defined CHATXZ_PORTABLE set "PORTABLE_DIR=%CHATXZ_PORTABLE%\chatxz-data"
if not defined PORTABLE_DIR if exist "chatxz-data" set "PORTABLE_DIR=%CD%\chatxz-data"

if exist "%CONFIG_DIR%" (
  echo   Config: %CONFIG_DIR%
  set /p RM1=   Remove config? [y/N]:
  if /I "!RM1!"=="y" rmdir /s /q "%CONFIG_DIR%" && echo   Removed config.
)
if exist "%DATA_DIR%" (
  echo   Data: %DATA_DIR%
  set /p RM2=   Remove data? [y/N]:
  if /I "!RM2!"=="y" rmdir /s /q "%DATA_DIR%" && echo   Removed data.
)
if defined PORTABLE_DIR if exist "!PORTABLE_DIR!" (
  echo   Portable: !PORTABLE_DIR!
  set /p RM3=   Remove portable data? [y/N]:
  if /I "!RM3!"=="y" rmdir /s /q "!PORTABLE_DIR!" && echo   Removed portable data.
)

echo [4/4] Cleanup complete.
echo.
echo To run again:  run.bat web --share
echo.
endlocal
exit /b 0