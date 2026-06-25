@echo off
REM Same as run.bat — use when you would have typed .\run.ps1 from cmd.
call "%~dp0run.bat" %*
exit /b %ERRORLEVEL%