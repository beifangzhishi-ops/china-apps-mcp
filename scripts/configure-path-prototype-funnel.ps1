[CmdletBinding()]
param(
    [int]$Port = 8767
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
    $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 5
    if ($response.StatusCode -ne 200) { throw "unexpected status" }
}
catch {
    throw "CAM path prototype is not healthy at $healthUrl. Start scripts/start-path-prototype.ps1 first."
}

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
try {
    foreach ($route in $routes) {
        & $tailscalePath funnel --bg --https=443 ("--set-path=" + $route.Public) $route.Target
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to configure Funnel route $($route.Public)."
        }
        $configured.Add($route.Public)
    }
}
catch {
    foreach ($publicPath in @($configured) | Select-Object -Reverse) {
        & $tailscalePath funnel --https=443 ("--set-path=" + $publicPath) off 2>$null
    }
    throw
}

$statusJson = (& $tailscalePath status --json | ConvertFrom-Json)
$dnsName = ([string]$statusJson.Self.DNSName).TrimEnd('.')
if (-not [string]::IsNullOrWhiteSpace($dnsName)) {
    Write-Output "CAM path prototype MCP: https://$dnsName/cam/mcp"
    Write-Output "Issuer:                 https://$dnsName/cam/"
}
Write-Output "Only CAM prototype paths on HTTPS 443 were added. Existing /, /mcp, /v1, 8443, and 10000 listeners were not modified."
& $tailscalePath funnel status
