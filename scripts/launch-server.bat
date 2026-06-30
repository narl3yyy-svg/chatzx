@echo off
setlocal EnableExtensions
cd /d "%~dp0\.."
set "CHATXZ_ROOT=%CD%"
set "PYTHONPATH=%CD%"
set "PUBLIC_PORT=8742"
set "INTERNAL_PORT=8743"
set "RUST_BIN=%CD%\target\release\chatxz-server.exe"

call "%~dp0stop-chatxz.bat"

if not exist "%RUST_BIN%" (
  echo [rust] Building chatxz-server...
  cargo build --release -p chatxz-server
  if errorlevel 1 (
    echo [rust] Build failed — install Rust from https://rustup.rs
    exit /b 1
  )
)

echo [python] RNS backend on port %INTERNAL_PORT%
start "chatxz-python" /B "%CHATXZ_PYTHON%" -u -m chatxz.web.server --internal --port %INTERNAL_PORT% --public-port %PUBLIC_PORT% %*

echo [rust] Primary server on port %PUBLIC_PORT%
"%RUST_BIN%" --port %PUBLIC_PORT% --backend http://127.0.0.1:%INTERNAL_PORT%
set "EXIT_CODE=%ERRORLEVEL%"
call "%~dp0stop-chatxz.bat"
exit /b %EXIT_CODE%