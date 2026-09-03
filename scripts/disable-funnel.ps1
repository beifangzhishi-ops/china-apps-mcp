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

# CAM owns only HTTPS 8443 after the port split. Never reset Funnel and never
# touch CPA's HTTPS 443 routes or BMG's future HTTPS 10000 listener here.
& $tailscalePath funnel --https=8443 --set-path=/mcp off
if ($LASTEXITCODE -ne 0) {
    throw "Failed to disable CAM /mcp on HTTPS 8443. Other Funnel listeners were not modified."
}

& $tailscalePath funnel --https=8443 --set-path=/ off
if ($LASTEXITCODE -ne 0) {
    throw "Failed to disable CAM root mapping on HTTPS 8443. Other Funnel listeners were not modified."
}

Write-Output "CAM HTTPS 8443 Funnel routes are disabled. HTTPS 443 and 10000 were not modified."
