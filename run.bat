@echo off
REM Double-click this file to open the prospect finder.
REM It uses the Python launcher (py), which ships with python.org installs.
cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel%==0 (
    py run.py
) else (
    where python >nul 2>&1
    if %errorlevel%==0 (
        python run.py
    ) else (
        echo.
        echo Python was not found on this machine.
        echo Install it from https://www.python.org/downloads/ and tick
        echo "Add python.exe to PATH" during setup, then double-click this again.
        echo.
        pause
        exit /b 1
    )
)

if %errorlevel% neq 0 pause
