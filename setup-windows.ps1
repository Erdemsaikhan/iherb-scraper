# One-time Windows setup: creates a venv, installs deps + Chromium, disables sleep.
# Run from the iherb-scraper folder:
#   powershell -ExecutionPolicy Bypass -File setup-windows.ps1
Set-Location $PSScriptRoot

Write-Host "== Creating virtual environment ==" -ForegroundColor Cyan
python -m venv .venv
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "Python not found. Install it first:  winget install Python.Python.3.12" -ForegroundColor Red
    exit 1
}
$py = ".\.venv\Scripts\python.exe"

Write-Host "== Installing dependencies ==" -ForegroundColor Cyan
& $py -m pip install --upgrade pip
& $py -m pip install -r requirements.txt
& $py -m playwright install chromium

Write-Host "== Disabling sleep / hibernate (keeps the scrape alive) ==" -ForegroundColor Cyan
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /change hibernate-timeout-ac 0
powercfg /change hibernate-timeout-dc 0

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host "Next: run  run-shard0.bat  (PC #1)  or  run-shard1.bat  (PC #2)"
