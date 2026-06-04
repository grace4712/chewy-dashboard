@echo off
:: Chewy Dashboard Watcher — Windows Task Scheduler Setup
:: Run this ONCE as Administrator to register the background watcher.
:: After that, the watcher starts automatically every time you log in.

set PYTHON=C:\Users\retai\AppData\Local\Python\pythoncore-3.14-64\python.exe
set SCRIPT=C:\Users\retai\OneDrive\Desktop\Claude Code\scripts\chewy_watcher.py
set TASKNAME=ChewyDashboardWatcher

echo.
echo ============================================
echo  Chewy Dashboard Watcher — Setup
echo ============================================
echo.
echo Registering scheduled task: %TASKNAME%
echo Python: %PYTHON%
echo Script: %SCRIPT%
echo.

:: Delete old task if it exists
schtasks /delete /tn "%TASKNAME%" /f >nul 2>&1

:: Create new task: runs at login, hidden window, for current user
schtasks /create ^
  /tn "%TASKNAME%" ^
  /tr "\"%PYTHON%\" \"%SCRIPT%\"" ^
  /sc onlogon ^
  /rl limited ^
  /f ^
  /it

if %errorlevel% equ 0 (
    echo.
    echo SUCCESS! Watcher registered.
    echo It will now start automatically every time you log into Windows.
    echo.
    echo Starting it right now for this session...
    start "" /min "%PYTHON%" "%SCRIPT%"
    echo.
    echo Watcher is running in the background.
    echo Check the log at:
    echo   C:\Users\retai\OneDrive\Desktop\Claude Code\scripts\watcher.log
) else (
    echo.
    echo ERROR: Could not register task. Try right-clicking this file
    echo and selecting "Run as administrator".
)

echo.
pause
