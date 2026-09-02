@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-browser.ps1"
set "BROWSER_EXIT_CODE=%ERRORLEVEL%"
if not "%BROWSER_EXIT_CODE%"=="0" pause
exit /b %BROWSER_EXIT_CODE%
