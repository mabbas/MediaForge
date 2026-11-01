<#
.SYNOPSIS
    Initializes local development credentials and configuration for MediaForge.
.DESCRIPTION
    Generates cryptographically secure random passwords and writes deploy/.env.
    Guarantees that existing credentials are never overwritten without explicit -Force.
    Never exposes secrets in standard output.
.PARAMETER Force
    Overwrite existing deploy/.env if it already exists.
#>
[CmdletBinding()]
param(
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$deployDir = Split-Path -Parent $scriptDir
$envPath = Join-Path $deployDir ".env"
$envExamplePath = Join-Path $deployDir ".env.example"

Write-Host "=== MediaForge Local Environment Initializer ===" -ForegroundColor Cyan

if ((Test-Path $envPath) -and (-not $Force)) {
    Write-Warning "Configuration file '$envPath' already exists."
    Write-Host "Preserving existing credentials. Use -Force if you wish to overwrite." -ForegroundColor Yellow
    exit 0
}

function Generate-SecurePassword([int]$length = 32) {
    $chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!#%*+,-./:=?@_"
    $bytes = New-Object byte[] $length
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()

    $result = New-Object char[] $length
    for ($i = 0; $i -lt $length; $i++) {
        $result[$i] = $chars[$bytes[$i] % $chars.Length]
    }
    return -join $result
}

Write-Host "Generating cryptographically secure credentials..." -ForegroundColor Green

$postgresPassword = Generate-SecurePassword 32
$gidAppPassword   = Generate-SecurePassword 32
$pgadminPassword  = Generate-SecurePassword 32

$envContent = @"
# ==============================================================================
# MediaForge / GrabItDown — Local Development Environment Configuration (.env)
# Generated: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss UTC")
# Managed locally — NEVER commit to version control.
# ==============================================================================

COMPOSE_PROJECT_NAME=mediaforge

# --- PostgreSQL 16 ---
POSTGRES_PORT=55432
POSTGRES_DB=grabitdown
POSTGRES_USER=postgres
POSTGRES_PASSWORD=$postgresPassword

# --- Application Role (Non-Superuser) ---
GID_DB_USER=gid_user
GID_DB_PASSWORD=$gidAppPassword
GID_DB_SCHEMA=gid

# --- Redis 7 ---
REDIS_PORT=26379

# --- Mailpit (Local SMTP & Web UI) ---
MAILPIT_SMTP_PORT=51025
MAILPIT_WEB_PORT=58025

# --- pgAdmin 4 ---
PGADMIN_PORT=58080
PGADMIN_DEFAULT_EMAIL=admin@mediaforge.com
PGADMIN_DEFAULT_PASSWORD=$pgadminPassword

# --- Convenience Application Database URLs (Localhost Development) ---
GID_API_DATABASE_URL=postgresql+asyncpg://gid_user:${gidAppPassword}@127.0.0.1:55432/grabitdown
DATABASE_URL=postgresql://gid_user:${gidAppPassword}@127.0.0.1:55432/grabitdown
"@

[System.IO.File]::WriteAllText($envPath, $envContent, [System.Text.UTF8Encoding]::new($false))

Write-Host "Environment file generated successfully at: $envPath" -ForegroundColor Green
Write-Host "All credentials created with high-entropy random generation." -ForegroundColor Green
Write-Host "Secrets are protected and excluded from console output." -ForegroundColor Cyan
