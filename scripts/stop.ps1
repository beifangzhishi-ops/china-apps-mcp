[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$stateDir = Join-Path $repoRoot ".state"
$pidFile = Join-Path $stateDir "mcp.pid"
$envPath = Join-Path $repoRoot ".env"

$port = 8765
if (Test-Path -LiteralPath $envPath) {
    foreach ($line in [IO.File]::ReadAllLines($envPath)) {
        if ($line -match '^\s*MCP_PORT\s*=\s*([0-9]+)\s*(?:#.*)?$') {
            $port = [int]$matches[1]
            break
        }
    }
}
if ($port -lt 1 -or $port -gt 65535) {
    throw "MCP_PORT must be between 1 and 65535."
}

function Get-LoopbackListenerPid {
    param([Parameter(Mandatory = $true)][int]$Port)

    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1") } |
        Select-Object -First 1
    if ($null -ne $connection) {
        return [int]$connection.OwningProcess
    }

    $netstat = Join-Path $env:SystemRoot "System32\netstat.exe"
    foreach ($line in (& $netstat -ano -p TCP 2>$null)) {
        if ($line -notmatch '^\s*TCP\s+(\S+):([0-9]+)\s+\S+\s+LISTENING\s+([0-9]+)\s*$') {
            continue
        }
        $localAddress = $matches[1] -replace '^\[|\]$', ''
        if ([int]$matches[2] -eq $Port -and $localAddress -in @("127.0.0.1", "::1")) {
            return [int]$matches[3]
        }
    }

    return $null
}

function Test-CamHealth {
    param([Parameter(Mandatory = $true)][int]$Port)

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
        if ($response.StatusCode -ne 200) {
            return $false
        }

        $payload = $response.Content | ConvertFrom-Json
        return (
            $payload.ok -eq $true -and
            $payload.service -eq "china-apps-mcp" -and
            $payload.mcp_path -eq "/mcp"
        )
    }
    catch {
        return $false
    }
}

function Get-OptionalProcessIdentity {
    param([Parameter(Mandatory = $true)][int]$ProcessId)

    $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $cim) {
        return "unknown"
    }

    $commandLine = [string]$cim.CommandLine
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        return "unknown"
    }
    if ($commandLine -match "china_apps_mcp") {
        return "valid"
    }
    return "invalid"
}

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Output "China Apps MCP is not running (PID file not found)."
    exit 0
}

$pidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
$targetPid = 0
if (-not [int]::TryParse($pidText, [ref]$targetPid)) {
    throw "Invalid PID file: $pidFile"
}

$process = Get-Process -Id $targetPid -ErrorAction SilentlyContinue
if ($null -eq $process) {
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    Write-Output "China Apps MCP is already stopped. Removed stale PID file."
    exit 0
}

$listenerPid = Get-LoopbackListenerPid -Port $port

if ($null -eq $listenerPid -or [int]$listenerPid -ne $targetPid) {
    throw "Refusing to stop PID $targetPid because it is not the loopback listener on MCP_PORT=$port."
}

if (-not (Test-CamHealth -Port $port)) {
    throw "Refusing to stop PID $targetPid because the CAM health check failed on MCP_PORT=$port."
}

$identity = Get-OptionalProcessIdentity -ProcessId $targetPid
if ($identity -eq "invalid") {
    throw "Refusing to stop PID $targetPid because its available process identity is not china_apps_mcp."
}

Stop-Process -Id $targetPid -Force

for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 250
    if ($null -eq (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) {
        break
    }
}

if ($null -ne (Get-Process -Id $targetPid -ErrorAction SilentlyContinue)) {
    throw "Failed to stop China Apps MCP process PID=$targetPid"
}

Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Write-Output "China Apps MCP stopped. PID=$targetPid"
exit 0
