[CmdletBinding()]
param(
    [int]$Port = 8765
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

$healthUrl = "http://127.0.0.1:$Port/health"
try {
    $health = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 5
    if ($health.StatusCode -ne 200) { throw "unexpected status" }
}
catch {
    throw "Local CAM gateway health check failed at $healthUrl. Start the production gateway first."
}

# Refuse to move the public /cam routes until the production process itself is
# already advertising the canonical path-based OAuth identity. This prevents an
# accidental cutover while 8765 still identifies the legacy root /mcp resource.
$authMetadataUrl = "http://127.0.0.1:$Port/.well-known/oauth-authorization-server"
try {
    $authMetadata = Invoke-RestMethod -Uri $authMetadataUrl -TimeoutSec 5
    $issuerText = ([string]$authMetadata.issuer).TrimEnd('/')
    $issuerUri = [Uri]$issuerText
}
catch {
    throw "Could not read production CAM OAuth metadata at $authMetadataUrl. Ensure OAuth mode is enabled before configuring Funnel."
}

if ($issuerUri.Scheme -ne "https" -or [string]::IsNullOrWhiteSpace($issuerUri.Host)) {
    throw "Production CAM issuer must be an absolute HTTPS URL. Current issuer: $issuerText"
}
if ($issuerUri.AbsolutePath.TrimEnd('/') -ne "/cam" -or $issuerUri.Query -or $issuerUri.Fragment) {
    throw "Production CAM must advertise an issuer ending exactly in /cam before Funnel cutover. Current issuer: $issuerText"
}

$resourceMetadataUrl = "http://127.0.0.1:$Port/.well-known/oauth-protected-resource/mcp"
try {
    $resourceMetadata = Invoke-RestMethod -Uri $resourceMetadataUrl -TimeoutSec 5
}
catch {
    throw "Could not read production CAM protected-resource metadata at $resourceMetadataUrl."
}

$expectedResource = "$issuerText/mcp"
if ([string]$resourceMetadata.resource -ne $expectedResource) {
    throw "Production CAM resource metadata mismatch. Expected $expectedResource but got $($resourceMetadata.resource)."
}
$authorizationServers = @($resourceMetadata.authorization_servers | ForEach-Object { [string]$_ })
if ($authorizationServers.Count -ne 1 -or $authorizationServers[0].TrimEnd('/') -ne $issuerText) {
    throw "Production CAM authorization_servers must contain exactly its canonical issuer: $issuerText"
}

# CAM owns only these exact HTTPS 443 paths. Do not add a root catch-all here:
# /v1 belongs to CPA; /bmg and future service prefixes are separate ownership domains.
$routes = @(
    @{ Public = "/cam/mcp"; Target = "http://127.0.0.1:$Port/mcp" },
    @{ Public = "/cam/authorize"; Target = "http://127.0.0.1:$Port/authorize" },
    @{ Public = "/cam/token"; Target = "http://127.0.0.1:$Port/token" },
    @{ Public = "/cam/register"; Target = "http://127.0.0.1:$Port/register" },
    @{ Public = "/cam/revoke"; Target = "http://127.0.0.1:$Port/revoke" },
    @{ Public = "/cam/oauth/consent"; Target = "http://127.0.0.1:$Port/oauth/consent" },
    @{ Public = "/cam/health"; Target = "http://127.0.0.1:$Port/health" },
    @{ Public = "/.well-known/oauth-authorization-server/cam"; Target = "http://127.0.0.1:$Port/.well-known/oauth-authorization-server" },
    @{ Public = "/cam/.well-known/oauth-authorization-server"; Target = "http://127.0.0.1:$Port/.well-known/oauth-authorization-server" },
    @{ Public = "/.well-known/oauth-protected-resource/cam/mcp"; Target = "http://127.0.0.1:$Port/.well-known/oauth-protected-resource/mcp" },
    @{ Public = "/cam/mcp/.well-known/oauth-protected-resource"; Target = "http://127.0.0.1:$Port/mcp/.well-known/oauth-protected-resource" }
)

$configured = [System.Collections.Generic.List[string]]::new()
foreach ($route in $routes) {
    & $tailscalePath funnel --bg --https=443 ("--set-path=" + $route.Public) $route.Target
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to configure CAM Funnel route $($route.Public). $($configured.Count) earlier CAM routes may already point to production. No automatic rollback was attempted because those paths may have had a pre-existing prototype target; inspect 'tailscale funnel status' and rerun after fixing the error."
    }
    $configured.Add($route.Public)
}

Write-Output "Production CAM Funnel routes now point to 127.0.0.1:$Port."
Write-Output "CAM issuer: $issuerText"
Write-Output "CAM MCP:    $expectedResource"
Write-Output "Only CAM-owned HTTPS 443 paths were updated. Legacy /, /mcp and HTTPS 8443 routes were intentionally left untouched for rollback."
& $tailscalePath funnel status
