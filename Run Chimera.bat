@echo off
REM ============================================================
REM  Run Chimera.bat
REM  Double-click shortcut to launch Chimera without opening a
REM  terminal and typing commands manually.
REM ============================================================

REM Move to the folder this .bat file lives in (the project root,
REM next to backend/ and frontend/), so it works no matter where a
REM shortcut to it is placed.
cd /d "%~dp0"

echo Starting Chimera...
echo.

REM Prefer the "py" launcher — installed by python.org and NOT
REM affected by the Windows Store's fake python.exe alias (see the
REM earlier fix for the original app's launcher for why "python"
REM alone can silently fail here).
where py >nul 2>nul
if not errorlevel 1 (
    py backend\app.py
    goto :check_result
)

python --version 2>nul | findstr /b "Python" >nul
if errorlevel 1 (
    echo.
    echo [ERROR] Only the Windows Store's Python alias was found,
    echo not a real Python install.
    echo.
    echo Fix this with EITHER of the following:
    echo   1^) Turn off the alias: Settings ^> Apps ^> Advanced app
    echo      settings ^> App execution aliases ^> turn OFF both
    echo      "App Installer python.exe" entries.
    echo   2^) Install/reinstall Python from https://www.python.org/downloads/
    echo      and make sure "Add Python to PATH" is checked.
    echo.
    pause
    exit /b 1
)

python backend\app.py

:check_result
if errorlevel 1 (
    echo.
    echo [ERROR] Chimera closed with an error - see the message above.
    pause
)
