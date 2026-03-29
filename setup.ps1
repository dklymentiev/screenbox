# Screenbox setup & update for Windows
# First run: generates .env, builds images, shows onboarding
# Update: rebuilds images, restarts services

$ErrorActionPreference = "Continue"
Push-Location $PSScriptRoot

# Clear stale env from previous runs
$env:DOCKER_BUILDKIT = $null

Write-Host "Checking Docker..." -NoNewline

# Pre-flight: Docker must be running
docker version >$null 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host " NOT RUNNING" -ForegroundColor Red
    Write-Host ""
    Write-Host "[ERROR] Docker daemon is not running."
    Write-Host "  Start Docker Desktop, wait for it to be ready, then rerun .\setup.ps1"
    Pop-Location; exit 1
}
Write-Host " OK" -ForegroundColor Green

# Detect: first install or update
$IsUpdate = $false
if (Test-Path .env) {
    $running = docker compose ps --quiet 2>$null
    if ($running -match '\S') { $IsUpdate = $true }
}

if ($IsUpdate) {
    Write-Host ""
    Write-Host "Screenbox update" -ForegroundColor Cyan
    Write-Host "================"
} else {
    Write-Host ""
    Write-Host "Screenbox setup" -ForegroundColor Cyan
    Write-Host "==============="
}
Write-Host ""

# 1. Generate .env if missing
if (-not (Test-Path .env)) {
    $Token = -join ((1..32) | ForEach-Object { "{0:x2}" -f (Get-Random -Max 256) })
    Copy-Item .env.example .env
    (Get-Content .env) -replace '^SCREENBOX_API_TOKEN=$', "SCREENBOX_API_TOKEN=$Token" | Set-Content .env
    Write-Host "[OK] Created .env with API token"
} else {
    Write-Host "[OK] .env already exists"
}

# Check token is set
$TokenVal = (Select-String -Path .env -Pattern '^SCREENBOX_API_TOKEN=(.+)$').Matches.Groups[1].Value
if (-not $TokenVal) {
    $Token = -join ((1..32) | ForEach-Object { "{0:x2}" -f (Get-Random -Max 256) })
    (Get-Content .env) -replace '^SCREENBOX_API_TOKEN=$', "SCREENBOX_API_TOKEN=$Token" | Set-Content .env
    $TokenVal = $Token
    Write-Host "[OK] Generated API token"
}

# 2. Create data directories
foreach ($d in @("data\desktops", "data\logs", "data\knowledge", "data\recordings")) {
    New-Item -ItemType Directory -Path $d -Force | Out-Null
}
if (-not (Select-String -Path .env -Pattern '^SCREENBOX_RECORDINGS_HOST_DIR=' -Quiet)) {
    Add-Content .env "SCREENBOX_RECORDINGS_HOST_DIR=$((Get-Location).Path)\data\recordings"
}
Write-Host "[OK] Data directories"

# 3. Generate .mcp.json for Claude Code
@"
{
  "mcpServers": {
    "screenbox": {
      "url": "http://localhost:8080/mcp",
      "headers": {
        "Authorization": "Bearer $TokenVal"
      }
    }
  }
}
"@ | Set-Content .mcp.json -Encoding UTF8
Write-Host "[OK] .mcp.json"

# Helper: run a command with elapsed timer
function Invoke-Step {
    param([string]$Label, [scriptblock]$Command)
    Write-Host ""
    Write-Host "$Label" -ForegroundColor Yellow
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $timerJob = Start-Job -ScriptBlock {
        param($pid)
        while ($true) {
            Start-Sleep -Seconds 5
            # Signal parent via file
            [IO.File]::WriteAllText("$env:TEMP\screenbox_timer.txt", "1")
        }
    }
    try {
        & $Command 2>&1 | ForEach-Object {
            if (Test-Path "$env:TEMP\screenbox_timer.txt") {
                Remove-Item "$env:TEMP\screenbox_timer.txt" -Force
                $elapsed = [math]::Round($sw.Elapsed.TotalSeconds)
                Write-Host "  [$elapsed`s]" -ForegroundColor DarkGray -NoNewline
                Write-Host " $_"
            } else {
                Write-Host $_
            }
        }
    } finally {
        Stop-Job $timerJob -ErrorAction SilentlyContinue
        Remove-Job $timerJob -ErrorAction SilentlyContinue
        Remove-Item "$env:TEMP\screenbox_timer.txt" -Force -ErrorAction SilentlyContinue
    }
    $sw.Stop()
    $total = [math]::Round($sw.Elapsed.TotalSeconds)
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] $Label ($total`s)" -ForegroundColor Red
        Pop-Location; exit 1
    }
    Write-Host "[OK] Done ($total`s)" -ForegroundColor Green
}

# 4. Build all images
Invoke-Step "[1/4] Building desktop image" {
    docker build -f docker/Dockerfile -t screenbox:latest docker/
}

Invoke-Step "[2/4] Building proxy, MCP server and dashboard" {
    $env:DOCKER_BUILDKIT = "0"
    docker compose build screenbox-socket-proxy screenbox-mcp screenbox-dashboard
    $env:DOCKER_BUILDKIT = $null
}

Invoke-Step "[3/4] Starting services" {
    if ($IsUpdate) {
        Write-Host "Stopping old services..."
        docker compose down 2>$null
    }
    docker compose up -d
}

# 5b. Create demo desktop (first install only)
if (-not $IsUpdate) {
    Write-Host ""
    Write-Host "[4/4] Creating demo desktop..." -ForegroundColor Yellow
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    for ($i = 0; $i -lt 12; $i++) {
        try {
            $health = Invoke-RestMethod -Uri "http://localhost:8080/api/health" -TimeoutSec 5 -ErrorAction SilentlyContinue
            if ($health) { break }
        } catch {}
        $elapsed = [math]::Round($sw.Elapsed.TotalSeconds)
        Write-Host "  Waiting for MCP... ($elapsed`s)" -ForegroundColor Gray
        Start-Sleep -Seconds 5
    }
    try {
        $result = Invoke-RestMethod -Uri "http://localhost:8080/api/desktop/create" -Method Post `
            -Headers @{ "Authorization" = "Bearer $TokenVal"; "Content-Type" = "application/json" } `
            -Body '{"id":"desktop-1","label":"My Desktop"}' -TimeoutSec 60
        $total = [math]::Round($sw.Elapsed.TotalSeconds)
        if ($result.ok) {
            Write-Host "[OK] Demo desktop created ($total`s)" -ForegroundColor Green
        } else {
            Write-Host "[WARN] Could not create demo desktop ($total`s)" -ForegroundColor Yellow
        }
    } catch {
        $total = [math]::Round($sw.Elapsed.TotalSeconds)
        Write-Host "[WARN] Could not create demo desktop ($total`s): $_" -ForegroundColor Yellow
        Write-Host "       Create one from Dashboard: http://localhost:16000"
    }
}

# 6. Show result
Write-Host ""
Write-Host "===============" -ForegroundColor Cyan
if ($IsUpdate) {
    Write-Host "Update complete!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Services restarted on new images."
    Write-Host "Existing desktops use the old image -- recreate them from Dashboard."
} else {
    Write-Host "Screenbox ready!" -ForegroundColor Green
    Write-Host ""
    Write-Host "Dashboard:"
    Write-Host "  http://localhost:16000?token=$TokenVal" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Connect Claude Code (from this directory):"
    Write-Host "  cd $((Get-Location).Path); claude"
    Write-Host "  (.mcp.json is already configured)"
}

Pop-Location
