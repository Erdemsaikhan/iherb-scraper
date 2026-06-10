@echo off
REM PC #2 — scrapes odd product IDs (shard 1 of 2)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0watchdog.ps1" -Shard 1 -Shards 2
pause
