@echo off
REM Stop chatxz server and release ports 8742 (HTTP), 4242 (RNS), 8743 (beacon).
setlocal EnableExtensions
cd /d "%~dp0.."

for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":8742" ^| findstr "LISTENING"') do (
  taskkill /F /PID %%a >nul 2>&1
)

for %%P in (4242 8743) do (
  for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":%%P" ^| findstr "UDP"') do (
    taskkill /F /PID %%a >nul 2>&1
  )
)

for /f "skip=1 tokens=1" %%a in ('wmic process where "commandline like '%%chatxz.web.server%%' or commandline like '%%chatxz.app%%'" get processid 2^>nul') do (
  if not "%%a"=="" taskkill /F /PID %%a >nul 2>&1
)

endlocal
exit /b 0