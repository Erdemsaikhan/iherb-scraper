# iHerb scraper watchdog — restarts the scrape until the shard is fully captured.
# Usage:
#   PC #1:  powershell -ExecutionPolicy Bypass -File watchdog.ps1 -Shard 0
#   PC #2:  powershell -ExecutionPolicy Bypass -File watchdog.ps1 -Shard 1
param(
    [int]$Shard = 0,
    [int]$Shards = 2,
    [int]$Concurrency = 2,
    [double]$Delay = 2.0
)

$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"

# Prefer the project venv; fall back to python on PATH.
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }

$log = Join-Path $PSScriptRoot ("watchdog.shard{0}.log" -f $Shard)
Write-Host "Watchdog: shard $Shard/$Shards, concurrency $Concurrency, delay $Delay. Logging to $log"

while ($true) {
    $ts = Get-Date -Format o
    "[$ts] starting shard $Shard/$Shards" | Tee-Object -FilePath $log -Append | Write-Host
    & $py scrape.py --shard $Shard --shards $Shards --concurrency $Concurrency --delay $Delay 2>&1 |
        Tee-Object -FilePath $log -Append
    $code = $LASTEXITCODE
    "[$(Get-Date -Format o)] scrape exited with code $code" | Tee-Object -FilePath $log -Append | Write-Host

    if ($code -eq 0) { Write-Host "Shard $Shard complete. Stopping watchdog."; break }
    if ($code -eq 2) { Write-Host "Browser failed to launch. Check install. Retrying in 60s..."; Start-Sleep -Seconds 60; continue }
    Write-Host "Restarting in 20s..."
    Start-Sleep -Seconds 20
}
