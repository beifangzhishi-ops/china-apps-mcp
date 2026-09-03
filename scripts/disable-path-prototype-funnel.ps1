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

$paths = @(
    "/cam/mcp/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/cam/mcp",
    "/cam/.well-known/oauth-authorization-server",
    "/.well-known/oauth-authorization-server/cam",
    "/cam/health",
    "/cam/oauth/consent",
    "/cam/revoke",
    "/cam/register",
    "/cam/token",
    "/cam/authorize",
    "/cam/mcp"
)

$failed = [System.Collections.Generic.List[string]]::new()
foreach ($publicPath in $paths) {
    & $tailscalePath funnel --https=443 ("--set-path=" + $publicPath) off
    if ($LASTEXITCODE -ne 0) {
        $failed.Add($publicPath)
    }
}

if ($failed.Count -gt 0) {
    throw "Some CAM prototype Funnel paths could not be removed: $($failed -join ', '). No global Funnel reset was attempted."
}

Write-Output "CAM path-prototype Funnel routes are disabled."
Write-Output "Production /, /mcp, CPA /v1, HTTPS 8443, and HTTPS 10000 were not modified."
& $tailscalePath funnel status
