<#
.SYNOPSIS
    Provisions or reconciles database roles for MediaForge (GrabItDown).
.DESCRIPTION
    Reconciles the migration role (gid_user) and runtime role (gid_app).
    Enforces least-privilege permissions:
      - gid_user: Schema owner with full DDL permissions for Alembic migrations.
      - gid_app:  Runtime application user with SELECT, INSERT, UPDATE, DELETE only.
                  Zero DDL privileges (cannot CREATE, ALTER, or DROP tables).
.EXAMPLE
    .\Provision-Roles.ps1
#>
[CmdletBinding()]
param(
    [string]$ComposeFile = "deploy/docker-compose.yml",
    [string]$EnvFile = "deploy/.env"
)

$ErrorActionPreference = "Stop"

Write-Host "=== MediaForge Role Provisioning Utility ===" -ForegroundColor Cyan

# Resolve paths
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DeployDir = Split-Path -Parent $ScriptDir
$RepoRoot = Split-Path -Parent $DeployDir

if (-not (Test-Path $EnvFile)) {
    $EnvFile = Join-Path $DeployDir ".env"
}
if (-not (Test-Path $EnvFile)) {
    Write-Error "Environment file not found at: $EnvFile"
}

# Load credentials from deploy/.env
Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $parts = $line.Split("=", 2)
        [System.Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1].Trim(), "Process")
    }
}

$DbName = [System.Environment]::GetEnvironmentVariable("POSTGRES_DB")
if (-not $DbName) { $DbName = "grabitdown" }

$SuperUser = [System.Environment]::GetEnvironmentVariable("POSTGRES_USER")
if (-not $SuperUser) { $SuperUser = "postgres" }

$MigratorUser = [System.Environment]::GetEnvironmentVariable("GID_DB_USER")
if (-not $MigratorUser) { $MigratorUser = "gid_user" }

$AppUser = [System.Environment]::GetEnvironmentVariable("GID_APP_USER")
if (-not $AppUser) { $AppUser = "gid_app" }

$AppPass = [System.Environment]::GetEnvironmentVariable("GID_APP_PASSWORD")
if (-not $AppPass) {
    Write-Error "GID_APP_PASSWORD is not set in $EnvFile"
}

$Schema = [System.Environment]::GetEnvironmentVariable("GID_DB_SCHEMA")
if (-not $Schema) { $Schema = "gid" }

Write-Host "Target Database:   $DbName"
Write-Host "Migration Role:    $MigratorUser (DDL / Owner)"
Write-Host "Runtime Role:      $AppUser (DML Only)"
Write-Host "Application Schema: $Schema"

$Sql = @"
DO `$do`$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '$AppUser') THEN
        CREATE ROLE "$AppUser" WITH LOGIN PASSWORD '$AppPass' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    ELSE
        ALTER ROLE "$AppUser" WITH PASSWORD '$AppPass' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    END IF;
END
`$do`$;

REVOKE ALL ON DATABASE "$DbName" FROM "$AppUser";
GRANT CONNECT, TEMPORARY ON DATABASE "$DbName" TO "$AppUser";

REVOKE ALL ON SCHEMA public FROM "$AppUser";
GRANT USAGE ON SCHEMA "$Schema" TO "$AppUser";
REVOKE CREATE ON SCHEMA "$Schema" FROM "$AppUser";

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA "$Schema" TO "$AppUser";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA "$Schema" TO "$AppUser";

ALTER DEFAULT PRIVILEGES FOR ROLE "$MigratorUser" IN SCHEMA "$Schema"
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "$AppUser";
ALTER DEFAULT PRIVILEGES FOR ROLE "$MigratorUser" IN SCHEMA "$Schema"
    GRANT USAGE, SELECT ON SEQUENCES TO "$AppUser";

ALTER ROLE "$AppUser" SET search_path TO "$Schema", public;
"@

$Sql | docker compose -f $ComposeFile exec -T postgres psql -U $SuperUser -d $DbName

if ($LASTEXITCODE -eq 0) {
    Write-Host "SUCCESS: Roles successfully provisioned and reconciled." -ForegroundColor Green
} else {
    Write-Error "Failed to provision roles in PostgreSQL."
}
