@echo off
setlocal
cd /d "%~dp0"

net session >nul 2>&1
if errorlevel 1 (
  echo Asking Windows for permission to allow this app through the firewall...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -Wait"
  exit /b %ERRORLEVEL%
)

set "PORT=5050"
set "PY="
if exist "%~dp0program\.venv\Scripts\python.exe" set "PY=%~dp0program\.venv\Scripts\python.exe"
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"

echo Adding firewall rules for port %PORT% (in and out)...
netsh advfirewall firewall delete rule name="MBD-TCP-%PORT%-in" >nul 2>&1
netsh advfirewall firewall delete rule name="MBD-TCP-%PORT%-out" >nul 2>&1
netsh advfirewall firewall delete rule name="MBD-UDP-%PORT%-in" >nul 2>&1
netsh advfirewall firewall add rule name="MBD-TCP-%PORT%-in" dir=in action=allow protocol=TCP localport=%PORT% profile=private,domain enable=yes
netsh advfirewall firewall add rule name="MBD-TCP-%PORT%-out" dir=out action=allow protocol=TCP remoteport=%PORT% profile=private,domain enable=yes
netsh advfirewall firewall add rule name="MBD-UDP-%PORT%-in" dir=in action=allow protocol=UDP localport=%PORT% profile=private,domain enable=yes
for /L %%P in (5051,1,5055) do (
  netsh advfirewall firewall delete rule name="MBD-UDP-%%P-in" >nul 2>&1
  netsh advfirewall firewall add rule name="MBD-UDP-%%P-in" dir=in action=allow protocol=UDP localport=%%P profile=private,domain enable=yes
)

if defined PY (
  echo Allowing %PY%
  netsh advfirewall firewall delete rule name="MBD-python-in" >nul 2>&1
  netsh advfirewall firewall delete rule name="MBD-python-out" >nul 2>&1
  netsh advfirewall firewall add rule name="MBD-python-in" dir=in action=allow program="%PY%" profile=private,domain enable=yes
  netsh advfirewall firewall add rule name="MBD-python-out" dir=out action=allow program="%PY%" profile=private,domain enable=yes
)

mkdir "%~dp0data" >nul 2>&1
echo ok>"%~dp0data\firewall.ok"
echo.
echo Windows Firewall now allows Matched Betting Documenter on this private network.
echo You can close this window.
timeout /t 4 >nul
exit /b 0
