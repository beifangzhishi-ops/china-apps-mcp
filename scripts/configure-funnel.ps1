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

& $tailscalePath funnel --bg --https=443 --set-path=/mcp "http://127.0.0.1:$Port/mcp"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to configure the /mcp Funnel mapping."
}

& $tailscalePath funnel status
