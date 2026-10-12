@echo off
rem Run the K-line init test on COM6 (double-click this file)
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python kline_init_test.py COM6
) else (
    py kline_init_test.py COM6
)
