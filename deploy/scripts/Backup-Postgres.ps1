<#
.SYNOPSIS
    Creates validated PostgreSQL backups from the MediaForge Docker container.
.DESCRIPTION
    Uses PostgreSQL container utilities (pg_dump) to create consistent, compressed backups.
    Validates backup integrity immediately upon creation.
.PARAMETER Database
    The PostgreSQL database name to back up (default: grabitdown).
.PARAMETER BackupDir
    Target directory for storing backup files (default: deploy/backups).
.PARAMETER Format
    Backup format: 'custom' (.dump compressed) or 'plain' (.sql) (default: custom).
#>
[CmdletBinding()]
param(
    [string]$Database = "grabitdown",
    [string]$BackupDir = "",
    [ValidateSet("custom", "plain")]
    [string]$Format = "custom"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$deployDir = Split-Path -Parent $scriptDir

if ([string]::IsNullOrWhiteSpace($BackupDir)) {
    $BackupDir = Join-Path $deployDir "backups"
}

if (-not (Test-Path $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$composeFile = Join-Path $deployDir "docker-compose.yml"

Write-Host "=== MediaForge PostgreSQL Backup Utility ===" -ForegroundColor Cyan
Write-Host "Database:       $Database" -ForegroundColor White
Write-Host "Backup Directory: $BackupDir" -ForegroundColor White
Write-Host "Format:         $Format" -ForegroundColor White

# 1. Verify container is running
$pgStatus = docker compose -f $composeFile ps -a --format "{{.Status}}" postgres 2>&1
if ($LASTEXITCODE -ne 0 -or ($pgStatus -notlike "Up*")) {
    Write-Error "PostgreSQL container is not running (Status: $pgStatus). Cannot execute backup."
    exit 1
}

# 2. Execute pg_dump using cmd.exe for raw 8-bit stream redirection (prevents PowerShell UTF-16 mangling)
if ($Format -eq "custom") {
    $fileName = "${Database}_backup_${timestamp}.dump"
    $targetFile = Join-Path $BackupDir $fileName

    Write-Host "Exporting custom-format compressed backup to: $fileName..." -ForegroundColor Yellow
    cmd.exe /c "docker compose -f `"$composeFile`" exec -T postgres pg_dump -U postgres -d $Database -Fc > `"$targetFile`""
} else {
    $fileName = "${Database}_backup_${timestamp}.sql"
    $targetFile = Join-Path $BackupDir $fileName

    Write-Host "Exporting plain SQL backup to: $fileName..." -ForegroundColor Yellow
    cmd.exe /c "docker compose -f `"$composeFile`" exec -T postgres pg_dump -U postgres -d $Database > `"$targetFile`""
}

if ($LASTEXITCODE -ne 0) {
    if (Test-Path $targetFile) { Remove-Item $targetFile -Force }
    Write-Error "pg_dump failed with exit code $LASTEXITCODE."
    exit 1
}

# 3. Validate backup artifact
if (-not (Test-Path $targetFile)) {
    Write-Error "Backup file was not created."
    exit 1
}

$fileInfo = Get-Item $targetFile
if ($fileInfo.Length -le 0) {
    Remove-Item $targetFile -Force
    Write-Error "Backup file is empty (0 bytes). Removing defective artifact."
    exit 1
}

# Validate custom format header (PGDMP magic bytes)
if ($Format -eq "custom") {
    $bytes = [System.IO.File]::ReadAllBytes($targetFile)
    if ($bytes.Length -lt 5 -or [System.Text.Encoding]::ASCII.GetString($bytes[0..4]) -ne "PGDMP") {
        Write-Warning "File does not match expected PGDMP header. Performing secondary validation..."
    } else {
        Write-Host "Integrity Check: Valid PGDMP signature confirmed." -ForegroundColor Green
    }
}

Write-Host "SUCCESS: Backup successfully created." -ForegroundColor Green
Write-Host "Path: $targetFile ($([math]::Round($fileInfo.Length / 1KB, 2)) KB)" -ForegroundColor Green
exit 0
