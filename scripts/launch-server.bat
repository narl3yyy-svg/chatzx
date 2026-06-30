@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
set "CHATXZ_ROOT=%CD%"
set "CHATXZ_BIN=%CD%\target\release\chatxz.exe"

call "%~dp0stop-chatxz.bat"

if not exist "%CHATXZ_BIN%" (
  echo [rust] Building chatxz application...
  cargo build --release -p chatxz-server
  if errorlevel 1 (
    echo [rust] Build failed — install Rust from https://rustup.rs
    exit /b 1
  )
)

echo [chatxz] Rust application on port 8742 (RNS daemon auto-started)
"%CHATXZ_BIN%" %*
set "EXIT_CODE=%ERRORLEVEL%"
call "%~dp0stop-chatxz.bat"
exit /b %EXIT_CODE%