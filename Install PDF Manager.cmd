@echo off
rem Installs (or updates) PDF Manager for this Windows user. No administrator
rem rights needed. What it does is described in packaging\windows\install.ps1.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\windows\install.ps1" %*
if %errorlevel% neq 0 (
  echo.
  echo The installation did not finish. See the message above.
  pause
  exit /b 1
)
exit /b 0
