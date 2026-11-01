<#
.SYNOPSIS
    Creates durable host-backed backups of MediaForge Docker media volumes.
.DESCRIPTION
    Uses a minimal container utility to create compressed tar.gz archives of named Docker volumes.
    Enforces a read-only source mount, quiescent writer checks, overwrite prevention,
    companion SHA-256 generation, and immediate archive integrity verification.
.PARAMETER VolumeName
    The Docker volume name to back up (default: mediaforge_api_downloads).
.PARAMETER BackupDir
    Target host directory for storing backup files (default: deploy/backups).
.PARAMETER AllowNonQuiescent
    If set, allows backup to proceed even if active media processing processes are detected.
#>
[CmdletBinding()]
param(
    [string]$VolumeName = "mediaforge_api_downloads",
    [string]$BackupDir = "",
    [switch]$AllowNonQuiescent
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
$fileName = "${VolumeName}_backup_${timestamp}.tar.gz"
$targetFile = Join-Path $BackupDir $fileName
$shaFile = "${targetFile}.sha256"

Write-Host "=== MediaForge Media Volume Backup Utility ===" -ForegroundColor Cyan
Write-Host "Volume Name:      $VolumeName" -ForegroundColor White
Write-Host "Backup Directory: $BackupDir" -ForegroundColor White
Write-Host "Target Archive:   $fileName" -ForegroundColor White

# 1. Prevent accidental overwrite of an existing backup file
if (Test-Path $targetFile) {
    Write-Error "Security/Integrity Error: Destination backup file already exists: $targetFile. Overwrite blocked."
    exit 1
}

# 2. Verify volume exists in Docker engine
$allVolumes = (docker volume ls --format "{{.Name}}") -split "`r?`n"
if ($VolumeName -notin $allVolumes) {
    Write-Error "Docker volume '$VolumeName' does not exist in Docker engine."
    exit 1
}

# 3. Quiescent consistency check: detect active writer processes in containers using the volume
Write-Host "Checking for active media processing writers (quiescent audit)..." -ForegroundColor Yellow
$usingContainers = (docker ps -q --filter "volume=$VolumeName") -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }

$activeWritersDetected = $false
foreach ($cId in $usingContainers) {
    $cName = (docker inspect --format "{{.Name}}" $cId).TrimStart('/')
    # Inspect /proc cmdlines for active ffmpeg or yt-dlp processes
    $pyCheck = "import os, glob; writers = [p for p in glob.glob('/proc/[0-9]*/cmdline') if p != f'/proc/{os.getpid()}/cmdline' and any(w in open(p, 'rb').read().decode('latin1', 'replace').lower() for w in ['ffmpeg', 'yt-dlp'])]; print(len(writers))"
    $writerCountStr = docker exec $cId python3 -c $pyCheck 2>$null
    if ($LASTEXITCODE -eq 0 -and [int]::TryParse($writerCountStr.Trim(), [ref]$null)) {
        $count = [int]$writerCountStr.Trim()
        if ($count -gt 0) {
            $activeWritersDetected = $true
            Write-Warning "Active media writer process(es) ($count) detected in container '$cName'."
        }
    }
}

if ($activeWritersDetected -and -not $AllowNonQuiescent) {
    Write-Error "Volume '$VolumeName' is currently being modified by active media processing operations. Refusing to create inconsistent snapshot. Wait for operations to complete or specify -AllowNonQuiescent."
    exit 1
}

# 4. Execute compressed tar export using container mount (enforces read-only :ro source mount)
Write-Host "Exporting compressed archive to host storage (read-only source mount)..." -ForegroundColor Yellow

# Use postgres:16-alpine which is already cached locally
docker run --rm `
    -v "${VolumeName}:/source:ro" `
    -v "${BackupDir}:/backup" `
    postgres:16-alpine `
    tar -czf "/backup/$fileName" -C /source .

if ($LASTEXITCODE -ne 0) {
    if (Test-Path $targetFile) { Remove-Item $targetFile -Force }
    Write-Error "Volume backup failed with exit code $LASTEXITCODE. Partial archive removed."
    exit 1
}

if (-not (Test-Path $targetFile)) {
    Write-Error "Expected backup file was not created: $targetFile"
    exit 1
}

$fileItem = Get-Item $targetFile
$fileSize = $fileItem.Length
$fileSizeKB = [math]::Round($fileSize / 1024, 2)

# 5. Compute SHA-256 Checksum and write companion checksum file
Write-Host "Calculating SHA-256 checksum..." -ForegroundColor Yellow
$hashResult = Get-FileHash -Path $targetFile -Algorithm SHA256
$sha256 = $hashResult.Hash

# Write companion checksum file in standard format: <hash>  <filename>
"$sha256  $fileName" | Out-File -FilePath $shaFile -Encoding ascii -Force

# 6. Verify Archive Integrity (test tar table of contents)
Write-Host "Validating archive integrity..." -ForegroundColor Yellow
$tocOutput = docker run --rm `
    -v "${BackupDir}:/backup:ro" `
    postgres:16-alpine `
    tar -tzf "/backup/$fileName" 2>&1

if ($LASTEXITCODE -ne 0) {
    if (Test-Path $targetFile) { Remove-Item $targetFile -Force }
    if (Test-Path $shaFile) { Remove-Item $shaFile -Force }
    Write-Error "Archive validation failed! The backup file appears corrupted. Corrupt archive removed."
    exit 1
}

$entryCount = ($tocOutput | Measure-Object -Line).Lines

Write-Host "`n[SUCCESS] Media volume backup completed successfully!" -ForegroundColor Green
Write-Host "Archive File:     $targetFile" -ForegroundColor White
Write-Host "File Size:        $fileSize bytes ($fileSizeKB KB)" -ForegroundColor White
Write-Host "Archive Entries:  $entryCount items" -ForegroundColor White
Write-Host "SHA-256 Checksum: $sha256" -ForegroundColor Green
Write-Host "Checksum File:    $shaFile" -ForegroundColor Green

# Return hashtable with backup details for programmatic use
return @{
    BackupFile   = $targetFile
    ChecksumFile = $shaFile
    VolumeName   = $VolumeName
    FileSize     = $fileSize
    SHA256       = $sha256
    EntryCount   = $entryCount
}
