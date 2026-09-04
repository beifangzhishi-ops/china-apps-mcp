[CmdletBinding()]
param(
    [int]$Port = 8765,
    [switch]$ConfirmLegacyCamRemoval
)

$ErrorActionPreference = "Stop"
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

# Trust the running production process as the source of truth. A .env file can be
# stale or edited without restart, so it must not authorize destructive cleanup.
$authMetadataUrl = "http://127.0.0.1:$Port/.well-known/oauth-authorization-server"
try {
    $authMetadata = Invoke-RestMethod -Uri $authMetadataUrl -TimeoutSec 5
    $runningIssuer = ([string]$authMetadata.issuer).TrimEnd('/')
    $runningIssuerUri = [Uri]$runningIssuer
}
catch {
    throw "Could not verify the running production CAM issuer at $authMetadataUrl."
}

if (
    $runningIssuerUri.Scheme -ne "https" -or
    [string]::IsNullOrWhiteSpace($runningIssuerUri.Host) -or
    -not $runningIssuerUri.IsDefaultPort -or
    $runningIssuerUri.AbsolutePath.TrimEnd('/') -ne "/cam" -or
    $runningIssuerUri.Query -or
    $runningIssuerUri.Fragment
) {
    throw "Legacy cleanup requires the running production CAM issuer to be exactly an HTTPS 443 /cam identity. Current issuer: $runningIssuer"
}

$publicBase = $runningIssuer

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
