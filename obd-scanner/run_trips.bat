@echo off
rem Drag and drop driving logs onto this file to summarize each drive
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python analyze_trips.py %*
) else (
    py analyze_trips.py %*
)
