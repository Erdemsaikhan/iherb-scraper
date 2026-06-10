@echo off
REM PC #1 — scrapes even product IDs (shard 0 of 2)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0watchdog.ps1" -Shard 0 -Shards 2
pause
