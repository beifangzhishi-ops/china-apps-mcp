[CmdletBinding()]
param()

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

# Remove only the routes owned by china-apps-mcp. Do not reset Funnel because
# the same HTTPS listener may also host CPA /v1 or other services.
& $tailscalePath funnel --https=443 --set-path=/mcp off
if ($LASTEXITCODE -ne 0) {
    throw "Failed to disable the /mcp Funnel mapping."
}

& $tailscalePath funnel --https=443 --set-path=/ off
if ($LASTEXITCODE -ne 0) {
    throw "Failed to disable the root Funnel mapping."
}

Write-Output "China Apps MCP Funnel routes are disabled. Other path-specific routes were left unchanged."
