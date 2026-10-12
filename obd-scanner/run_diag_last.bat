@echo off
rem Ask Claude to diagnose the newest saved scan (no car needed)
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python obd_scan.py --from-scan
) else (
    py obd_scan.py --from-scan
)
