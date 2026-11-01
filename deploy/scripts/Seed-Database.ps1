<#
.SYNOPSIS
    Explicitly seeds the MediaForge database with default tenant and system user.
.DESCRIPTION
    Runs the idempotent database seeding routine using the configured runtime account.
    Never run automatically during API startup.
.EXAMPLE
    .\Seed-Database.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

Write-Host "=== MediaForge Explicit Database Seeding Utility ===" -ForegroundColor Cyan

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DeployDir = Split-Path -Parent $ScriptDir
$RepoRoot = Split-Path -Parent $DeployDir

Push-Location $RepoRoot
try {
    python -m api.services.seed
    if ($LASTEXITCODE -eq 0) {
        Write-Host "SUCCESS: Database seeding executed successfully." -ForegroundColor Green
    } else {
        Write-Error "Database seeding failed with exit code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
