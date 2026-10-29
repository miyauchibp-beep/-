@echo off
rem Record the actual repair result for a saved diagnosis
cd /d "%~dp0"
where python >/dev/null 2>nul
if %errorlevel%==0 (
    python record_result.py
) else (
    py record_result.py
)
