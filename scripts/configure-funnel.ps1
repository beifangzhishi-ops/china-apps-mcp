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

# Keep an explicit /mcp route for compatibility with the original PoC.
& $tailscalePath funnel --bg --https=443 --set-path=/mcp "http://127.0.0.1:$Port/mcp"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure the /mcp Funnel mapping."
}

# OAuth needs /.well-known, /authorize, /token, /register, /revoke, and
# /oauth/consent on the same HTTPS origin. A root fallback exposes those routes
# while more-specific existing routes such as CPA /v1 keep taking precedence.
& $tailscalePath funnel --bg --https=443 --set-path=/ "http://127.0.0.1:$Port"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure the root Funnel mapping required for OAuth."
}

try {
    $statusJson = (& $tailscalePath status --json | ConvertFrom-Json)
    $dnsName = ([string]$statusJson.Self.DNSName).TrimEnd('.')
    if (-not [string]::IsNullOrWhiteSpace($dnsName)) {
        Write-Output "Public base URL: https://$dnsName"
        Write-Output "OAuth MCP URL:    https://$dnsName/mcp"
        Write-Output "For OAuth, set MCP_PUBLIC_BASE_URL=https://$dnsName and include $dnsName in MCP_ALLOWED_HOSTS."
    }
}
catch {
    Write-Output "Could not read the Tailscale DNS name automatically. Use 'tailscale funnel status' below."
}

& $tailscalePath funnel status
