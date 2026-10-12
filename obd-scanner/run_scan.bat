@echo off
rem Read trouble codes and live data on COM6 (double-click this file)
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python obd_scan.py COM6
) else (
    py obd_scan.py COM6
)
