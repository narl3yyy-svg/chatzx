@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
set "CHATXZ_ROOT=%CD%"
set "CHATXZ_BIN=%CD%\target\release\chatxz.exe"

call "%~dp0stop-chatxz.bat"

if not exist "%CHATXZ_BIN%" (
  where cargo >nul 2>&1
  if errorlevel 1 (
    if exist "%USERPROFILE%\.cargo\bin\cargo.exe" (
      set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"
    ) else (
      echo [rust] ERROR: cargo not found.
      echo Install Rust from https://rustup.rs then restart cmd.
      echo Then: run.bat install ^&^& run.bat web
      exit /b 1
    )
  )
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