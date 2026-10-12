@echo off
rem Run the K-line init test (double-click this file; it asks for the COM port)
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python kline_init_test.py
) else (
    py kline_init_test.py
)
