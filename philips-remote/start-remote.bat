@echo off
rem Double-click on Windows to start the remote and open it in the browser.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 server.py --listen 0.0.0.0 --open
  goto end
)
python server.py --listen 0.0.0.0 --open
if errorlevel 1 echo Python 3 is needed. Install it from python.org, then run this file again.
:end
pause
