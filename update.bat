@echo off
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  where py >nul 2>&1
  if errorlevel 1 (
    echo Python 3 is required. Install it from https://www.python.org/downloads/ then tick Add python.exe to PATH.
    pause
    exit /b 1
  )
  py -3 update.py %*
) else (
  python update.py %*
)
if errorlevel 1 pause
pause
