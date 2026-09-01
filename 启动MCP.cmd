@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" -Background
set "MCP_EXIT_CODE=%ERRORLEVEL%"
if not "%MCP_EXIT_CODE%"=="0" pause
exit /b %MCP_EXIT_CODE%
