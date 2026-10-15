@echo off
chcp 65001 > nul
rem 整備写真のフォルダをこのファイルにドラッグ＆ドロップしてください
if "%~1"=="" (
  echo 整備写真のフォルダを、このファイルの上にドラッグ＆ドロップしてください。
  pause
  exit /b
)
py -m pip install --quiet pillow numpy pillow-heif
py "%~dp0photo_sort.py" "%~1"
pause
