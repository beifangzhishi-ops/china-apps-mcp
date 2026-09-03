[CmdletBinding()]
param(
    [int]$Port = 8767
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $repoRoot ".state\cam-path-prototype.pid"

function Get-LoopbackListenerPid {
    param([Parameter(Mandatory = $true)][int]$LocalPort)

    $connection = Get-NetTCPConnection -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1") } |
        Select-Object -First 1
    if ($null -ne $connection) {
        return [int]$connection.OwningProcess
    }

    $netstat = Join-Path $env:SystemRoot "System32\netstat.exe"
    foreach ($line in (& $netstat -ano -p TCP 2>$null)) {
        if ($line -notmatch '^\s*TCP\s+(\S+):([0-9]+)\s+\S+\s+LISTENING\s+([0-9]+)\s*$') { continue }
        $address = $matches[1] -replace '^\[|\]$', ''
        if ([int]$matches[2] -eq $LocalPort -and $address -in @("127.0.0.1", "::1")) {
            return [int]$matches[3]
        }
    }
    return $null
}

$listenerPid = Get-LoopbackListenerPid -LocalPort $Port
if ($null -eq $listenerPid) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Output "CAM path prototype is not listening on port $Port."
    exit 0
}

$listenerPid = [int]$listenerPid
$proc = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerPid" -ErrorAction SilentlyContinue
if ($null -eq $proc -or -not $proc.CommandLine -or $proc.CommandLine -notmatch "china_apps_mcp\.path_prototype") {
    throw "Port $Port is owned by PID=$listenerPid, but it is not a verified CAM path-prototype process. Refusing to stop it."
}

if (Test-Path -LiteralPath $pidFile) {
    $recorded = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    if ($recorded -and $recorded -ne [string]$listenerPid) {
        throw "Prototype PID file records $recorded but port $Port belongs to $listenerPid. Refusing to stop anything."
    }
}

Stop-Process -Id $listenerPid -Force
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Write-Output "CAM path prototype stopped. PID=$listenerPid"
Write-Output "Production CAM and Edge were not modified."
