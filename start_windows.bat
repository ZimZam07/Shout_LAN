@echo off
rem SHOUT LAN - start this Windows laptop as node 2. Double-click this file.
rem Works with Wi-Fi and Bluetooth off: everything runs on this laptop and the sound card.
cd /d "%~dp0"
rem stop any node that is still running, so the sound card and the ports are free
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*shout.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 2 /nobreak >nul
if not exist .venv\Scripts\python.exe (
  echo First run: setting up Python ^(needs internet once^)...
  py -3.13 -m venv .venv
  .venv\Scripts\python -m pip install -r requirements.txt
)
.venv\Scripts\python shout.py --node 2 --peer 1
pause
