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

& $tailscalePath funnel --https=443 --set-path=/mcp off
if ($LASTEXITCODE -ne 0) {
    throw "Failed to disable the /mcp Funnel mapping. Use 'tailscale funnel reset' only if you intend to clear all Funnel routes."
}

Write-Output "The public /mcp Funnel route is disabled."
