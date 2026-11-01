<#
.SYNOPSIS
    Automated verification suite for MediaForge Docker development infrastructure.
.DESCRIPTION
    Validates Docker daemon, Compose specification, network, volumes, ports,
    and runtime service health (PostgreSQL, Redis, Mailpit, pgAdmin).
.OUTPUTS
    Summary table with PASS, FAIL, and NOT RUN counters and exit code.
#>
[CmdletBinding()]
param(
    [switch]$SkipRuntimeTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$deployDir = Split-Path -Parent $scriptDir

$results = [System.Collections.Generic.List[PSCustomObject]]::new()

function Record-Check([string]$category, [string]$testName, [string]$status, [string]$details) {
    $obj = [PSCustomObject]@{
        Category = $category
        Check    = $testName
        Status   = $status
        Details  = $details
    }
    $results.Add($obj)

    $color = switch ($status) {
        "PASS"    { "Green" }
        "FAIL"    { "Red" }
        "NOT RUN" { "Yellow" }
        default   { "White" }
    }
    Write-Host "[$status] $category :: $testName - $details" -ForegroundColor $color
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host " MediaForge Development Infrastructure Verification Suite   " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

# 1. Environment and Configuration
$envPath = Join-Path $deployDir ".env"
if (Test-Path $envPath) {
    Record-Check "Config" ".env file exists" "PASS" "Found at $envPath"
} else {
    Record-Check "Config" ".env file exists" "FAIL" "Missing deploy/.env. Run Initialize-Dev.ps1 first."
}

# 2. Docker Daemon Availability
$dockerInfo = docker info 2>&1
if ($LASTEXITCODE -eq 0) {
    Record-Check "Docker" "Docker daemon connection" "PASS" "Docker engine is operational"
} else {
    Record-Check "Docker" "Docker daemon connection" "FAIL" "Docker daemon not responding: $dockerInfo"
}

# 3. Docker Compose Configuration Validity
Push-Location $deployDir
try {
    $composeOutput = docker compose config --quiet 2>&1
    if ($LASTEXITCODE -eq 0) {
        Record-Check "Compose" "Compose YAML & interpolation" "PASS" "docker-compose.yml validated successfully"
    } else {
        Record-Check "Compose" "Compose YAML & interpolation" "FAIL" "Compose validation failed: $composeOutput"
    }
} finally {
    Pop-Location
}

# 4. Named Volumes and Network Definition Check
$volumesExpected = @("mediaforge_pgdata", "mediaforge_redis_data", "mediaforge_mailpit_data", "mediaforge_pgadmin_data")
$existingVolumes = docker volume ls --format "{{.Name}}" 2>&1

foreach ($vol in $volumesExpected) {
    if ($existingVolumes -contains $vol) {
        Record-Check "Storage" "Volume $vol" "PASS" "Volume exists in Docker engine"
    } else {
        Record-Check "Storage" "Volume $vol" "NOT RUN" "Volume not created yet (creates on first docker compose up)"
    }
}

$existingNetworks = docker network ls --format "{{.Name}}" 2>&1
if ($existingNetworks -contains "mediaforge_net") {
    Record-Check "Network" "Network mediaforge_net" "PASS" "Network exists in Docker engine"
} else {
    Record-Check "Network" "Network mediaforge_net" "NOT RUN" "Network not created yet (creates on first docker compose up)"
}

# 5. Service Runtime and Health Checks
$containers = docker compose -f (Join-Path $deployDir "docker-compose.yml") ps -a --format "{{.Name}}|{{.Status}}|{{.Health}}" 2>&1

$serviceNames = @("postgres", "redis", "mailpit", "pgadmin")
$containerMap = @{}

if ($LASTEXITCODE -eq 0 -and $containers) {
    foreach ($line in $containers) {
        if ($line -match '^(.+?)\|(.+?)\|(.*)$') {
            $name = $matches[1].Trim()
            $status = $matches[2].Trim()
            $health = $matches[3].Trim()
            $containerMap[$name] = @{ Status = $status; Health = $health }
        }
    }
}

foreach ($svc in $serviceNames) {
    if ($containerMap.ContainsKey($svc)) {
        $st = $containerMap[$svc].Status
        $hl = $containerMap[$svc].Health
        if ($st -like "Up*") {
            if ($hl -eq "healthy" -or [string]::IsNullOrEmpty($hl)) {
                Record-Check "Container" "Service '$svc' state" "PASS" "Running ($st, $hl)"
            } else {
                Record-Check "Container" "Service '$svc' health" "FAIL" "Unhealthy ($hl)"
            }
        } else {
            Record-Check "Container" "Service '$svc' state" "FAIL" "Container is stopped ($st)"
        }
    } else {
        Record-Check "Container" "Service '$svc' state" "NOT RUN" "Container not created/running"
    }
}

# 6. Deep Service Diagnostics (only if services are running)
if (-not $SkipRuntimeTests) {
    # PostgreSQL Deep Check
    if ($containerMap.ContainsKey("postgres") -and ($containerMap["postgres"].Status -like "Up*")) {
        $pgVersion = docker compose -f (Join-Path $deployDir "docker-compose.yml") exec -T postgres psql -U postgres -d grabitdown -tAc "SELECT version();" 2>&1
        if ($LASTEXITCODE -eq 0 -and $pgVersion -like "*PostgreSQL 16*") {
            Record-Check "PostgreSQL" "PostgreSQL 16 Version" "PASS" ($pgVersion.Trim().Split("`n")[0])
        } else {
            Record-Check "PostgreSQL" "PostgreSQL 16 Version" "FAIL" "Version check failed: $pgVersion"
        }

        $pgSchema = docker compose -f (Join-Path $deployDir "docker-compose.yml") exec -T postgres psql -U postgres -d grabitdown -tAc "SELECT schema_name FROM information_schema.schemata WHERE schema_name = 'gid';" 2>&1
        if ($LASTEXITCODE -eq 0 -and ($pgSchema.Trim() -eq "gid")) {
            Record-Check "PostgreSQL" "Schema 'gid' exists" "PASS" "Schema 'gid' present"
        } else {
            Record-Check "PostgreSQL" "Schema 'gid' exists" "FAIL" "Schema 'gid' not found"
        }

        $pgRole = docker compose -f (Join-Path $deployDir "docker-compose.yml") exec -T postgres psql -U postgres -d grabitdown -tAc "SELECT rolname FROM pg_roles WHERE rolname = 'gid_user';" 2>&1
        if ($LASTEXITCODE -eq 0 -and ($pgRole.Trim() -eq "gid_user")) {
            Record-Check "PostgreSQL" "Role 'gid_user' exists" "PASS" "Role 'gid_user' present"
        } else {
            Record-Check "PostgreSQL" "Role 'gid_user' exists" "FAIL" "Role 'gid_user' not found"
        }

        $pgExt = docker compose -f (Join-Path $deployDir "docker-compose.yml") exec -T postgres psql -U postgres -d grabitdown -tAc "SELECT extname FROM pg_extension WHERE extname IN ('uuid-ossp', 'pgcrypto');" 2>&1
        if ($LASTEXITCODE -eq 0 -and ($pgExt -match "uuid-ossp") -and ($pgExt -match "pgcrypto")) {
            Record-Check "PostgreSQL" "Extensions uuid-ossp & pgcrypto" "PASS" "Extensions installed"
        } else {
            Record-Check "PostgreSQL" "Extensions uuid-ossp & pgcrypto" "FAIL" "Extensions missing: $pgExt"
        }
    } else {
        Record-Check "PostgreSQL" "Database verification" "NOT RUN" "PostgreSQL container not running"
    }

    # Redis Ping Check
    if ($containerMap.ContainsKey("redis") -and ($containerMap["redis"].Status -like "Up*")) {
        $redisPing = docker compose -f (Join-Path $deployDir "docker-compose.yml") exec -T redis redis-cli ping 2>&1
        if ($LASTEXITCODE -eq 0 -and ($redisPing.Trim() -eq "PONG")) {
            Record-Check "Redis" "Redis PING test" "PASS" "Response: PONG"
        } else {
            Record-Check "Redis" "Redis PING test" "FAIL" "Ping failed: $redisPing"
        }
    } else {
        Record-Check "Redis" "Redis PING test" "NOT RUN" "Redis container not running"
    }

    # Mailpit API Check
    if ($containerMap.ContainsKey("mailpit") -and ($containerMap["mailpit"].Status -like "Up*")) {
        try {
            $resp = Invoke-RestMethod -Uri "http://127.0.0.1:58025/api/v1/info" -TimeoutSec 5 -ErrorAction Stop
            Record-Check "Mailpit" "Mailpit Web API" "PASS" "Version: $($resp.version)"
        } catch {
            Record-Check "Mailpit" "Mailpit Web API" "FAIL" "HTTP request failed: $_"
        }
    } else {
        Record-Check "Mailpit" "Mailpit Web API" "NOT RUN" "Mailpit container not running"
    }

    # pgAdmin Ping Check
    if ($containerMap.ContainsKey("pgadmin") -and ($containerMap["pgadmin"].Status -like "Up*")) {
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:58080/misc/ping" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
            if ($resp.StatusCode -eq 200) {
                Record-Check "pgAdmin" "pgAdmin HTTP Ping" "PASS" "HTTP 200 OK"
            } else {
                Record-Check "pgAdmin" "pgAdmin HTTP Ping" "FAIL" "Returned status $($resp.StatusCode)"
            }
        } catch {
            Record-Check "pgAdmin" "pgAdmin HTTP Ping" "FAIL" "HTTP request failed: $_"
        }
    } else {
        Record-Check "pgAdmin" "pgAdmin HTTP Ping" "NOT RUN" "pgAdmin container not running"
    }

    # FastAPI Readiness Check (if mediaforge-api is running)
    if ($containerMap.ContainsKey("mediaforge-api") -and ($containerMap["mediaforge-api"].Status -like "Up*")) {
        try {
            $resp = Invoke-RestMethod -Uri "http://127.0.0.1:58000/api/v1/health/ready" -TimeoutSec 5 -ErrorAction Stop
            if ($resp.status -eq "healthy" -and $resp.database -eq "ok") {
                Record-Check "FastAPI" "FastAPI Readiness" "PASS" "Status: healthy, Database: ok, FFmpeg: $($resp.ffmpeg)"
            } else {
                Record-Check "FastAPI" "FastAPI Readiness" "FAIL" "Unhealthy response: $($resp | ConvertTo-Json -Compress)"
            }
        } catch {
            Record-Check "FastAPI" "FastAPI Readiness" "FAIL" "HTTP request failed: $_"
        }
    }
}

# 7. Summary
Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host " Verification Summary Results                               " -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

$passCount   = @($results | Where-Object { $_.Status -eq "PASS" }).Count
$failCount   = @($results | Where-Object { $_.Status -eq "FAIL" }).Count
$notRunCount = @($results | Where-Object { $_.Status -eq "NOT RUN" }).Count


$failColor = if ($failCount -gt 0) { "Red" } else { "Gray" }
$notRunColor = if ($notRunCount -gt 0) { "Yellow" } else { "Gray" }

Write-Host "PASS:    $passCount" -ForegroundColor Green
Write-Host "FAIL:    $failCount" -ForegroundColor $failColor
Write-Host "NOT RUN: $notRunCount" -ForegroundColor $notRunColor
Write-Host "TOTAL:   $($results.Count)" -ForegroundColor White


if ($failCount -gt 0) {
    exit 1
}
exit 0
