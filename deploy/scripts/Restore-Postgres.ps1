<#
.SYNOPSIS
    Safely restores PostgreSQL backups into an explicitly designated database.
.DESCRIPTION
    Restores .dump or .sql backups using container tools (pg_restore / psql).
    Protects the primary authoritative database (grabitdown) from accidental overwrite
    unless explicit confirmation is provided via -AllowAuthoritativeOverwrite.
.PARAMETER BackupFile
    Path to the PostgreSQL backup file to restore.
.PARAMETER TargetDatabase
    Target database name. Defaults to 'grabitdown_restore_test' to ensure safety.
.PARAMETER AllowAuthoritativeOverwrite
    Required switch to allow restoring directly into 'grabitdown'.
.PARAMETER CleanFirst
    Drops existing objects in target database before restoring (default: $false).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile,
    [string]$TargetDatabase = "grabitdown_restore_test",
    [switch]$AllowAuthoritativeOverwrite,
    [switch]$CleanFirst
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$deployDir = Split-Path -Parent $scriptDir
$composeFile = Join-Path $deployDir "docker-compose.yml"

Write-Host "=== MediaForge PostgreSQL Safe Restore Utility ===" -ForegroundColor Cyan

# 1. Validate Backup File
if (-not (Test-Path $BackupFile)) {
    Write-Error "Backup file '$BackupFile' does not exist."
    exit 1
}

$fileItem = Get-Item $BackupFile
if ($fileItem.Length -le 0) {
    Write-Error "Backup file is empty (0 bytes). Cannot restore."
    exit 1
}

# 2. Safety Check for Authoritative Database Overwrite
if ($TargetDatabase -eq "grabitdown" -and (-not $AllowAuthoritativeOverwrite)) {
    Write-Error @"
SAFETY GUARD TRIGGERED:
Attempting to restore directly into the primary authoritative database 'grabitdown'.
To prevent accidental data loss, you must supply the -AllowAuthoritativeOverwrite switch:
    .\Restore-Postgres.ps1 -BackupFile '$BackupFile' -TargetDatabase 'grabitdown' -AllowAuthoritativeOverwrite
Alternatively, restore safely into a sandbox database (e.g. -TargetDatabase 'grabitdown_restore_test').
"@
    exit 1
}

# 3. Verify Container is running
$pgStatus = docker compose -f $composeFile ps -a --format "{{.Status}}" postgres 2>&1
if ($LASTEXITCODE -ne 0 -or ($pgStatus -notlike "Up*")) {
    Write-Error "PostgreSQL container is not running (Status: $pgStatus). Cannot execute restore."
    exit 1
}

# 4. Check if Target Database exists; create if missing
Write-Host "Checking target database '$TargetDatabase'..." -ForegroundColor White
$dbCheck = docker compose -f $composeFile exec -T postgres psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname = '$TargetDatabase';" 2>&1
if ([string]::IsNullOrWhiteSpace($dbCheck) -or "$dbCheck".Trim() -ne "1") {
    docker compose -f $composeFile exec -T postgres createdb -U postgres $TargetDatabase
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to create target database '$TargetDatabase'."
        exit 1
    }
    Write-Host "Created database '$TargetDatabase'." -ForegroundColor Green
}

# 5. Execute Restore based on format
Write-Host "Restoring '$($fileItem.Name)' into '$TargetDatabase'..." -ForegroundColor Yellow

$isCustomFormat = $false
$bytes = [System.IO.File]::ReadAllBytes($fileItem.FullName)
if ($bytes.Length -ge 5 -and [System.Text.Encoding]::ASCII.GetString($bytes[0..4]) -eq "PGDMP") {
    $isCustomFormat = $true
}

if ($isCustomFormat -or $fileItem.Extension -eq ".dump") {
    $cleanArg = if ($CleanFirst) { "--clean --if-exists" } else { "" }
    # Pass binary dump file through container stdin using cmd.exe type pipe
    cmd.exe /c "type `"$($fileItem.FullName)`" | docker compose -f `"$composeFile`" exec -T postgres pg_restore -U postgres -d $TargetDatabase $cleanArg --no-owner --no-privileges"
} else {
    cmd.exe /c "type `"$($fileItem.FullName)`" | docker compose -f `"$composeFile`" exec -T postgres psql -U postgres -d $TargetDatabase -v ON_ERROR_STOP=1"
}

# pg_restore exit code 0 is clean; 1 is warnings; >1 is error
if ($LASTEXITCODE -gt 1) {
    Write-Error "Restore failed with exit code $LASTEXITCODE."
    exit 1
}

Write-Host "SUCCESS: Database '$TargetDatabase' restored successfully from '$($fileItem.Name)'." -ForegroundColor Green
exit 0
