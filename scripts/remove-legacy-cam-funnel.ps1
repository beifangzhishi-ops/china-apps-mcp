[CmdletBinding()]
param(
    [int]$Port = 8765,
    [switch]$ConfirmLegacyCamRemoval
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot ".env"
$tailscalePath = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
if (-not (Test-Path -LiteralPath $tailscalePath)) {
    $tailscale = Get-Command tailscale.exe -ErrorAction Stop
    $tailscalePath = $tailscale.Source
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script from an elevated PowerShell window."
}
if (-not $ConfirmLegacyCamRemoval) {
    throw "Refusing to remove legacy CAM routes without -ConfirmLegacyCamRemoval. Run this only after the production /cam connector and browser tools are verified."
}
if (-not (Test-Path -LiteralPath $envPath)) {
    throw "Missing .env. Refusing legacy cleanup without verifying the production CAM identity."
}

$publicBase = ""
foreach ($line in [IO.File]::ReadAllLines($envPath)) {
    if ($line -match '^\s*MCP_PUBLIC_BASE_URL\s*=\s*(.+?)\s*$') {
        $publicBase = $matches[1].Trim().TrimEnd('/')
        break
    }
}
if ([string]::IsNullOrWhiteSpace($publicBase)) {
    throw "MCP_PUBLIC_BASE_URL is missing from .env."
}

try { $publicUri = [Uri]$publicBase } catch { throw "MCP_PUBLIC_BASE_URL is not a valid URL: $publicBase" }
if ($publicUri.Scheme -ne "https" -or $publicUri.AbsolutePath.TrimEnd('/') -ne "/cam" -or -not $publicUri.IsDefaultPort -or $publicUri.Query -or $publicUri.Fragment) {
    throw "Legacy cleanup requires the production public base to be exactly an HTTPS 443 /cam issuer. Current value: $publicBase"
}

# Verify the running 8765 process has actually loaded the /cam identity. Editing
# .env without restarting production CAM is not enough to permit cleanup.
$authMetadataUrl = "http://127.0.0.1:$Port/.well-known/oauth-authorization-server"
try {
    $authMetadata = Invoke-RestMethod -Uri $authMetadataUrl -TimeoutSec 5
    $runningIssuer = ([string]$authMetadata.issuer).TrimEnd('/')
}
catch {
    throw "Could not verify the running production CAM issuer at $authMetadataUrl."
}
if ($runningIssuer -ne $publicBase) {
    throw "Running production CAM issuer does not match .env. Expected $publicBase but got $runningIssuer."
}

# Verify the new public path is healthy before removing any rollback route.
$publicHealthUrl = "$publicBase/health"
try {
    $health = Invoke-WebRequest -UseBasicParsing -Uri $publicHealthUrl -TimeoutSec 10
    if ($health.StatusCode -ne 200) { throw "unexpected status" }
}
catch {
    throw "New production CAM path is not publicly healthy at $publicHealthUrl. Legacy routes were not modified."
}

# One-time historical cleanup only. These four entries are deliberately not part
# of normal CAM disable lifecycle because they are not part of final CAM ownership.
$legacyRoutes = @(
    @{ Https = 443; Path = "/mcp" },
    @{ Https = 443; Path = "/" },
    @{ Https = 8443; Path = "/mcp" },
    @{ Https = 8443; Path = "/" }
)

$failed = [System.Collections.Generic.List[string]]::new()
foreach ($route in $legacyRoutes) {
    & $tailscalePath funnel ("--https=" + $route.Https) ("--set-path=" + $route.Path) off
    if ($LASTEXITCODE -ne 0) {
        $failed.Add("$($route.Https):$($route.Path)")
    }
}

if ($failed.Count -gt 0) {
    throw "Some legacy CAM routes could not be removed: $($failed -join ', '). No other Funnel paths were touched."
}

Write-Output "Legacy CAM root /mcp and root catch-all routes on HTTPS 443/8443 were removed."
Write-Output "Final /cam paths, CPA /v1, and other service prefixes were not modified."
& $tailscalePath funnel status
