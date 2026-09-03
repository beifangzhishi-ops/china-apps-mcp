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
    $null = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 5
}
catch {
    throw "Local gateway health check failed at $healthUrl. Start the MCP gateway first."
}

# CAM owns HTTPS 8443. During migration, the legacy CAM routes on 443 are left
# untouched so the existing ChatGPT connection keeps working until 8443 is verified.
& $tailscalePath funnel --bg --https=8443 --set-path=/mcp "http://127.0.0.1:$Port/mcp"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure CAM /mcp on HTTPS 8443."
}

# OAuth needs /.well-known, /authorize, /token, /register, /revoke, and
# /oauth/consent on the same HTTPS origin. The root fallback exposes those routes
# on CAM's dedicated 8443 origin.
& $tailscalePath funnel --bg --https=8443 --set-path=/ "http://127.0.0.1:$Port"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure CAM root mapping on HTTPS 8443."
}

try {
    $statusJson = (& $tailscalePath status --json | ConvertFrom-Json)
    $dnsName = ([string]$statusJson.Self.DNSName).TrimEnd('.')
    if (-not [string]::IsNullOrWhiteSpace($dnsName)) {
        Write-Output "CAM public base URL: https://${dnsName}:8443"
        Write-Output "CAM OAuth MCP URL:    https://${dnsName}:8443/mcp"
        Write-Output "For OAuth migration, set MCP_PUBLIC_BASE_URL=https://${dnsName}:8443 and include $dnsName in MCP_ALLOWED_HOSTS only after the 8443 Funnel path is verified."
        Write-Output "Legacy CAM routes on HTTPS 443 were not modified."
    }
}
catch {
    Write-Output "Could not read the Tailscale DNS name automatically. Use 'tailscale funnel status' below."
}

& $tailscalePath funnel status
