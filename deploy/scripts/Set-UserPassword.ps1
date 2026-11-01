<#
.SYNOPSIS
    Safely provisions or updates a password for an existing authorized local test user.

.DESCRIPTION
    Uses Argon2id password hashing via python -m api.services.manage_user.
    Never exposes passwords in logs. Operates using the least-privilege runtime role.

.PARAMETER Email
    Email of the authorized test user (e.g. system@grabitdown.local).

.PARAMETER Tenant
    Tenant slug (default: default).

.PARAMETER Password
    Optional password string. If not provided, prompts securely without echo.

.EXAMPLE
    .\deploy\scripts\Set-UserPassword.ps1 -Email "system@grabitdown.local"
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Email,

    [Parameter(Mandatory = $false)]
    [string]$Tenant = "default",

    [Parameter(Mandatory = $false)]
    [string]$Password
)

$RepoRoot = (Resolve-Path "$PSScriptRoot\..\..").Path
Push-Location $RepoRoot

try {
    if (-not $Password) {
        $SecurePass = Read-Host -Prompt "Enter password for $Email" -AsSecureString
        $BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecurePass)
        $Password = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($BSTR)

        $SecureConfirm = Read-Host -Prompt "Confirm password" -AsSecureString
        $BSTR2 = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureConfirm)
        $Confirm = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR2)
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($BSTR2)

        if ($Password -ne $Confirm) {
            Write-Error "Passwords do not match."
            exit 1
        }
    }

    python -m api.services.manage_user set-password --email $Email --tenant $Tenant --password $Password
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to update password for $Email"
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}
