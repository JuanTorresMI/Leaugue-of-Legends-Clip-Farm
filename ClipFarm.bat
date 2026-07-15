@echo off
title LeagueClipFarm
cd /d "%~dp0"

REM ---------------------------------------------------------------------------
REM One-click launcher. Double-click to (re)start LeagueClipFarm safely:
REM   1. Stops any ClipFarm python process already running (the "exactly one
REM      watch" rule -- two instances compete and only one can own port 8000).
REM   2. Starts a fresh watch (dashboard + all background workers).
REM   3. Opens the dashboard in your browser.
REM Keep this window open while it runs; close it (or Ctrl+C) to stop ClipFarm.
REM ---------------------------------------------------------------------------

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: .venv\Scripts\python.exe not found next to this script.
    echo Run this from the LeagueClipFarm folder after setting up the venv.
    pause
    exit /b 1
)

echo Stopping any ClipFarm instance that is already running...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'clipfarm' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

REM Give the old process a moment to release port 8000.
timeout /t 2 /nobreak >nul

REM Open the dashboard once the server has had a few seconds to come up.
start "" /min cmd /c "timeout /t 5 /nobreak >nul & start http://localhost:8000"

echo.
echo Starting LeagueClipFarm... (leave this window open; close it to stop)
echo Dashboard: http://localhost:8000
echo.
".venv\Scripts\python.exe" -m clipfarm.cli watch

echo.
echo ClipFarm stopped. If that was unexpected, the error is above.
pause
