<#
.SYNOPSIS
    Restores or validates MediaForge media volume backups into a Docker volume.
.DESCRIPTION
    Extracts host-backed tar.gz archives into a target Docker volume with comprehensive security controls:
    - Pre-restore SHA-256 checksum verification.
    - Path traversal and root escape inspection before extraction.
    - Protection against overwriting active production media volumes.
    - Non-root UID/GID (1000:1000) ownership remediation for API container usability.
.PARAMETER BackupFile
    Path to the .tar.gz volume backup archive.
.PARAMETER TargetVolume
    Name of the Docker volume to restore into.
.PARAMETER ExpectedHash
    Optional expected SHA-256 hash. If omitted, companion .sha256 file is checked automatically.
.PARAMETER Force
    If specified, creates target volume if it does not exist.
.PARAMETER AllowProductionOverwrite
    Explicit switch required to target production volumes like 'mediaforge_api_downloads'.
.PARAMETER AllowActiveVolumeOverwrite
    Explicit switch required to target volumes currently attached to running containers.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BackupFile,

    [Parameter(Mandatory = $true)]
    [string]$TargetVolume,

    [string]$ExpectedHash = "",

    [switch]$Force,

    [switch]$AllowProductionOverwrite,

    [switch]$AllowActiveVolumeOverwrite
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not (Test-Path $BackupFile)) {
    Write-Error "Backup file does not exist: $BackupFile"
    exit 1
}

$fullBackupPath = (Resolve-Path $BackupFile).Path
$backupDir = Split-Path -Parent $fullBackupPath
$archiveName = Split-Path -Leaf $fullBackupPath

Write-Host "=== MediaForge Media Volume Restore Utility ===" -ForegroundColor Cyan
Write-Host "Backup Archive:   $fullBackupPath" -ForegroundColor White
Write-Host "Target Volume:    $TargetVolume" -ForegroundColor White

# 1. Checksum Verification before extraction
$shaFile = "${fullBackupPath}.sha256"
if ([string]::IsNullOrWhiteSpace($ExpectedHash)) {
    if (Test-Path $shaFile) {
        $rawSha = (Get-Content $shaFile -Raw).Trim()
        $ExpectedHash = ($rawSha -split '\s+')[0]
        Write-Host "Found companion checksum file: $shaFile (Expected SHA-256: $ExpectedHash)" -ForegroundColor Cyan
    }
}

if (-not [string]::IsNullOrWhiteSpace($ExpectedHash)) {
    Write-Host "Verifying backup archive cryptographic checksum..." -ForegroundColor Yellow
    $actualHash = (Get-FileHash -Path $fullBackupPath -Algorithm SHA256).Hash
    if ($actualHash.ToLower() -ne $ExpectedHash.ToLower()) {
        Write-Error "Integrity Check Failed: Archive SHA-256 ($actualHash) does not match expected checksum ($ExpectedHash). Refusing to restore corrupted or tampered archive."
        exit 1
    }
    Write-Host "[VERIFIED] Archive SHA-256 checksum matches: $actualHash" -ForegroundColor Green
} else {
    Write-Warning "No expected SHA-256 checksum provided or found alongside archive. Pre-restore checksum verification skipped."
}

# 2. Production Volume & Active Volume Guardrails
$protectedVolumes = @("mediaforge_api_downloads", "mediaforge_pgdata", "mediaforge_redis_data")
if ($TargetVolume -in $protectedVolumes -and -not $AllowProductionOverwrite) {
    Write-Error "Security Policy Violation: Target volume '$TargetVolume' is a designated production volume. Restoring over production media is blocked. Use a disposable volume or pass -AllowProductionOverwrite."
    exit 1
}

$activeContainers = @((docker ps -q --filter "volume=$TargetVolume") -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
if ($activeContainers.Count -gt 0 -and -not $AllowActiveVolumeOverwrite) {
    Write-Error "Active Volume Conflict: Target volume '$TargetVolume' is currently mounted by running containers ($($activeContainers -join ', ')). Refusing to restore into active volume. Pass -AllowActiveVolumeOverwrite to bypass."
    exit 1
}

# 3. Path Traversal & Root Escape Security Audit
Write-Host "Auditing archive entries against path traversal and root escapes..." -ForegroundColor Yellow
$tocEntries = docker run --rm `
    -v "${backupDir}:/backup:ro" `
    postgres:16-alpine `
    tar -tzf "/backup/$archiveName" 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to read archive table of contents. Archive is invalid or corrupted."
    exit 1
}

foreach ($entry in ($tocEntries -split "`r?`n")) {
    $trimmed = $entry.Trim()
    if ([string]::IsNullOrWhiteSpace($trimmed)) { continue }
    if ($trimmed.StartsWith("/") -or $trimmed.StartsWith("\") -or $trimmed.Contains("../") -or $trimmed.Contains("..\")) {
        Write-Error "Security Violation: Archive contains unsafe or escaping path: '$trimmed'. Extraction aborted."
        exit 1
    }
}
Write-Host "[VERIFIED] All archive entries are safe relative paths within the target root." -ForegroundColor Green

# 4. Check or create target volume
$existingVols = (docker volume ls --format "{{.Name}}") -split "`r?`n"
if ($TargetVolume -notin $existingVols) {
    if ($Force) {
        Write-Host "Creating target volume: $TargetVolume..." -ForegroundColor Yellow
        docker volume create $TargetVolume | Out-Null
    } else {
        Write-Error "Target volume '$TargetVolume' does not exist. Use -Force to automatically create it."
        exit 1
    }
}

# 5. Extract archive into target volume
Write-Host "Extracting archive into volume '$TargetVolume'..." -ForegroundColor Yellow
docker run --rm `
    -v "${TargetVolume}:/target" `
    -v "${backupDir}:/backup:ro" `
    postgres:16-alpine `
    tar -xzf "/backup/$archiveName" -C /target

if ($LASTEXITCODE -ne 0) {
    Write-Error "Volume extraction failed with exit code $LASTEXITCODE."
    exit 1
}

# 6. Ensure restored files are owned by mediaforge (UID 1000, GID 1000) for non-root API container usability
Write-Host "Remediating file permissions for non-root container user (1000:1000)..." -ForegroundColor Yellow
docker run --rm `
    -v "${TargetVolume}:/target" `
    postgres:16-alpine `
    chown -R 1000:1000 /target

if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to set file permissions on restored volume."
    exit 1
}

# 7. Verify restored volume structure
Write-Host "Verifying restored filesystem structure..." -ForegroundColor Yellow
$listing = docker run --rm `
    -v "${TargetVolume}:/target:ro" `
    postgres:16-alpine `
    find /target -maxdepth 3

Write-Host "`n[SUCCESS] Volume restore completed successfully!" -ForegroundColor Green
Write-Host "Restored Volume: $TargetVolume" -ForegroundColor White
Write-Host "Listing Overview:`n$listing" -ForegroundColor Gray

return @{
    TargetVolume = $TargetVolume
    BackupFile   = $fullBackupPath
    Success      = $true
}
