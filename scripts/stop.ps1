# Stops Local SEO Audit on Windows. Run it by double-clicking stop.bat. Your data is kept.
param([switch]$NoPause)  # -NoPause: for automated runs
$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)

Write-Host ""
Write-Host "  Stopping Local SEO Audit..." -ForegroundColor Cyan
docker compose down
if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "  Stopped. Your projects and results are kept; double-click start.bat to start again." -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "  Could not stop the app. Is Docker Desktop running?" -ForegroundColor Red
}
Write-Host ""
if (-not $NoPause) { Read-Host "Press Enter to close this window" }
