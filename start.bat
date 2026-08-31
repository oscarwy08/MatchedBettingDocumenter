@echo off
cd /d "%~dp0"
set "MBD_ROOT=%~dp0"
set "MBD_LAUNCHER=1"
if exist "%~dp0start.sh" del /q "%~dp0start.sh" >nul 2>&1
if exist "%~dp0Start.sh" del /q "%~dp0Start.sh" >nul 2>&1
if exist "%~dp0Start.command" del /q "%~dp0Start.command" >nul 2>&1
attrib -h "%~dp0start.bat" >nul 2>&1
attrib -h "%~dp0README.txt" >nul 2>&1
if exist "%~dp0allow-firewall.bat" del /q "%~dp0allow-firewall.bat" >nul 2>&1
if exist "%~dp0allow-firewall.sh" del /q "%~dp0allow-firewall.sh" >nul 2>&1

if exist "program\run.py" goto use_program
set "APP_DIR=%~dp0"
goto have_app
:use_program
set "APP_DIR=%~dp0program"
:have_app
cd /d "%APP_DIR%"

set "PY=python"
where python >nul 2>&1
if errorlevel 1 (
  where py >nul 2>&1
  if errorlevel 1 (
    echo Python 3 is required. Install it from https://www.python.org/downloads/ then tick Add python.exe to PATH.
    pause
    exit /b 1
  )
  set "PY=py -3"
)

if not exist .venv (
  %PY% -m venv .venv
)

call .venv\Scripts\activate.bat
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt
python update.py --auto
python -m pip install -q -r requirements.txt

:run
cd /d "%MBD_ROOT%"
if exist "program\run.py" goto after_program
set "APP_DIR=%~dp0"
goto after_app
:after_program
set "APP_DIR=%~dp0program"
attrib +h "%~dp0program" >nul 2>&1
if exist "%~dp0start.sh" del /q "%~dp0start.sh" >nul 2>&1
:after_app
cd /d "%APP_DIR%"
if not exist .venv (
  %PY% -m venv .venv
  call .venv\Scripts\activate.bat
)
python -m pip install -q -r requirements.txt

echo.
echo Matched Betting Documenter is starting.
echo Leave this window open. Press Ctrl+C to stop.
echo.
python run.py
if %ERRORLEVEL% EQU 42 goto run
pause
