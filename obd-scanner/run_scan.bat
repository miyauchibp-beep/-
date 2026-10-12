@echo off
rem Read trouble codes and live data (double-click this file; it asks for the COM port)
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python obd_scan.py
) else (
    py obd_scan.py
)
