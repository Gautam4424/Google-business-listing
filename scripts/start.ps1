# Starts Local SEO Audit on Windows. Run it by double-clicking start.bat in the main folder.
param([switch]$NoPause)  # -NoPause: for automated runs
$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Say($Message, $Color = "White") { Write-Host $Message -ForegroundColor $Color }
function Fail($Message) {
    Say ""
    Say "  PROBLEM: $Message" Red
    Say ""
    if (-not $NoPause) { Read-Host "Press Enter to close this window" }
    exit 1
}
function DockerReady {
    docker info 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0)
}
function EnvValue($Text, $Name) {
    if ($Text -match "(?m)^$Name=(.*)$") { return $Matches[1].Trim() }
    return ""
}
function NewPassword {
    $chars = (48..57) + (65..90) + (97..122)
    return -join ($chars | Get-Random -Count 24 | ForEach-Object { [char]$_ })
}

Say ""
Say "  Local SEO Audit - starting" Cyan
Say "  ----------------------------" Cyan

# 1. Docker Desktop installed?
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Start-Process "https://www.docker.com/products/docker-desktop/"
    Fail "Docker Desktop is not installed. The download page has opened in your browser. Install it, restart the computer, open Docker Desktop once, then double-click start.bat again."
}

# 2. Docker Desktop running?
if (-not (DockerReady)) {
    Say "  Starting Docker Desktop (this can take a minute)..." Yellow
    $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (Test-Path $desktop) { Start-Process $desktop }
    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 3
        if (DockerReady) { $ready = $true; break }
    }
    if (-not $ready) {
        Fail "Docker Desktop is not running. Open Docker Desktop from the Start menu, wait until it shows 'Engine running', then double-click start.bat again."
    }
}
Say "  [ok] Docker is running" Green

# 3. Settings file (.env) with the API keys
if (-not (Test-Path ".env")) {
    if (-not (Test-Path ".env.example")) { Fail "The file .env.example is missing. Download the app again." }
    $text = Get-Content ".env.example" -Raw
    $text = $text -replace "(?m)^POSTGRES_PASSWORD=.*$", ("POSTGRES_PASSWORD=" + (NewPassword))
    [System.IO.File]::WriteAllText((Join-Path $Root ".env"), $text)
    Say ""
    Say "  A settings file was created. Notepad will open now." Yellow
    Say "  1) Paste your Google key right after  GOOGLE_API_KEY=" Yellow
    Say "  2) Paste your SerpApi key right after SERPAPI_KEY=" Yellow
    Say "  3) Save (Ctrl+S) and close Notepad." Yellow
    Say ""
    Start-Process notepad.exe -ArgumentList ".env" -Wait
}

$envText = Get-Content ".env" -Raw
if (-not (EnvValue $envText "GOOGLE_API_KEY")) {
    Start-Process notepad.exe -ArgumentList ".env"
    Fail "GOOGLE_API_KEY is empty. Notepad has opened the settings file: paste your Google key after GOOGLE_API_KEY=, save, close Notepad, then double-click start.bat again."
}
if ((EnvValue $envText "POSTGRES_PASSWORD") -in @("", "change-me")) {
    $envText = $envText -replace "(?m)^POSTGRES_PASSWORD=.*$", ("POSTGRES_PASSWORD=" + (NewPassword))
    [System.IO.File]::WriteAllText((Join-Path $Root ".env"), $envText)
}
if (-not (EnvValue $envText "SERPAPI_KEY")) {
    Say "  [note] SERPAPI_KEY is empty: the app works, but ranking checks and top-10 reviews are unavailable." Yellow
}
Say "  [ok] Settings file found" Green

# 4. Build and start
Say ""
Say "  Starting the app. The FIRST time this downloads and builds everything (5-15 minutes)." Cyan
Say "  Later starts take under a minute." Cyan
Say ""
docker compose up -d --build
if ($LASTEXITCODE -ne 0) { Fail "Docker could not start the app. Read the messages above, or see Troubleshooting in README.md." }

# 5. Wait until it answers
Say ""
Say "  Waiting for the app to be ready..." Cyan
$ok = $false
for ($i = 0; $i -lt 90; $i++) {
    try {
        $r = Invoke-WebRequest "http://localhost:8000/health" -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $ok = $true; break }
    } catch { }
    Start-Sleep -Seconds 2
}
if (-not $ok) { Fail "The app did not start in time. Run start.bat again; if it keeps failing, see Troubleshooting in README.md." }

if (-not $NoPause) { Start-Process "http://localhost:8000" }
Say ""
Say "  DONE. The app is open in your browser: http://localhost:8000" Green
Say "  It keeps running in the background. To stop it, double-click stop.bat." Green
Say ""
if (-not $NoPause) { Read-Host "Press Enter to close this window (the app keeps running)" }
