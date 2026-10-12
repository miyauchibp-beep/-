@echo off
rem ダブルクリックで K-line 初期化テストを実行する（ポートは COM6）
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (
    python kline_init_test.py COM6
) else (
    py kline_init_test.py COM6
)
pause
