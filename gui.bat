@echo off
REM Double-click this file to open the prospect finder window.
REM pythonw runs it without a black console behind it; if that fails the
REM ordinary python is used so any error is at least visible.
cd /d "%~dp0"

where pyw >nul 2>&1
if %errorlevel%==0 (
    start "" pyw gui.py
    exit /b 0
)
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw gui.py
    exit /b 0
)
where py >nul 2>&1
if %errorlevel%==0 (
    py gui.py
) else (
    where python >nul 2>&1
    if %errorlevel%==0 (
        python gui.py
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
