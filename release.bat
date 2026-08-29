@echo off
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe release.py %*
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    py -3 release.py %*
  ) else (
    python release.py %*
  )
)
if errorlevel 1 pause
