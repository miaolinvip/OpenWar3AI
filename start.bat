@echo off
rem ======================================================================
rem  OpenWar3 one-click launcher (the only script you need).
rem
rem  First run on a bare Windows 10/11: the only thing it installs is a private Python
rem  under bin\env (no admin rights, system untouched). Everything else is already there:
rem  Windows PowerShell 5.1 comes with Windows, the web UI ships prebuilt (Node.js is only
rem  fetched if you change the web UI source, or with "start.bat node" for the website preview).
rem  Set your Warcraft III 1.27a folder in Farsight; it extracts the game data for you.
rem  Every run after that: a few seconds of checks, then the web console "Farsight"
rem  (http://127.0.0.1:8866) opens on its home page "Control Center", where the gateway,
rem  chat bubbles, local LLM and website preview are started / stopped.
rem
rem  Double-click            check + install what is missing, start Farsight, open the browser
rem  start.bat setup         full check: reinstall packages, retry AMAI
rem  start.bat stop          stop EVERYTHING (same as double-clicking stop.bat)
rem  start.bat restart       restart only the Farsight backend (games and services keep running)
rem  start.bat node          also fetch a private Node.js (only the website preview needs it)
rem
rem  This window closes by itself after a few seconds: Farsight keeps running in the
rem  background, closing the browser does not stop it. To stop it all: stop.bat, or
rem  "Stop all" at the top right of the Control Center page.
rem  start.bat 5 6           also start tests on instances 5 and 6 (game + reference AI)
rem  Extra options           --director 5   --hours 12   --no-browser   --port 8867
rem
rem  The logic lives in setup.ps1 (runs on the built-in Windows PowerShell 5.1).
rem  Log of the last deployment: bin\env\setup.log
rem ======================================================================
setlocal
chcp 65001 >nul
if not exist "%~dp0setup.ps1" (
  echo [x] setup.ps1 was not found next to start.bat.
  echo     Extract the whole archive into a folder first, then run start.bat from there.
  echo.
  pause
  exit /b 1
)
set "PYTHONIOENCODING=utf-8"
set "OW3_ARGS=%*"
rem Run from a PowerShell 7 terminal, Windows PowerShell 5.1 would inherit PS7's module path and lose
rem cmdlets such as Get-FileHash. Reset it to the 5.1 defaults for the child process.
set "PSModulePath=%ProgramFiles%\WindowsPowerShell\Modules;%SystemRoot%\System32\WindowsPowerShell\v1.0\Modules"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -Launch
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  pause
  exit /b %RC%
)
timeout /t 12 >nul 2>&1
endlocal
exit /b 0
