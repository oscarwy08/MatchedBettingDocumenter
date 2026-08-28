@echo off
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo Python 3 is required. Install it from https://www.python.org/downloads/ then run this again.
  pause
  exit /b 1
)

if not exist .venv (
  python -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt

echo.
echo Matched Betting Documenter is starting.
echo This computer:  http://127.0.0.1:5050
echo Other devices on Wi-Fi can use this PC's IP on port 5050 (see Devices).
echo Leave this window open. Press Ctrl+C to stop.
echo.
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:5050"
python run.py
pause
