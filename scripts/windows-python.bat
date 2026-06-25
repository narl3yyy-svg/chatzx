@echo off
REM Find Python 3.10+ for install.bat / run.bat. Sets PYTHON_EXE or USE_PY_LAUNCHER=1.
set "PYTHON_EXE="
set "USE_PY_LAUNCHER="

if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" (
  "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" 2>nul && set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
)
if not defined PYTHON_EXE if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" 2>nul && set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
)
if not defined PYTHON_EXE if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
  "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" 2>nul && set "PYTHON_EXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
)
if not defined PYTHON_EXE if exist "C:\Program Files\Python312\python.exe" (
  "C:\Program Files\Python312\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" 2>nul && set "PYTHON_EXE=C:\Program Files\Python312\python.exe"
)

if not defined PYTHON_EXE (
  where py >nul 2>&1 && (
    py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" 2>nul && set "USE_PY_LAUNCHER=1"
  )
)

if not defined PYTHON_EXE if not defined USE_PY_LAUNCHER (
  for %%P in (python3.exe python.exe) do (
    where %%P >nul 2>&1 && (
      for /f "delims=" %%F in ('where %%P 2^>nul ^| findstr /i /v WindowsApps') do (
        if not defined PYTHON_EXE (
          "%%F" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" 2>nul && set "PYTHON_EXE=%%F"
        )
      )
    )
  )
)

if not defined PYTHON_EXE if not defined USE_PY_LAUNCHER exit /b 1
exit /b 0