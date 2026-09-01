[CmdletBinding()]
param(
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$healthUrl = "http://127.0.0.1:$Port/health"

try {
    $response = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 5
    Write-Output "Local gateway: OK"
    $response | ConvertTo-Json -Depth 4
}
catch {
    Write-Warning "Local gateway is not reachable at $healthUrl"
}

$tailscalePath = Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"
if (Test-Path -LiteralPath $tailscalePath) {
    & $tailscalePath funnel status
}
elseif (Get-Command tailscale.exe -ErrorAction SilentlyContinue) {
    & tailscale.exe funnel status
}
else {
    Write-Warning "Tailscale CLI was not found."
}
