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

$cim = Get-CimInstance Win32_Process -Filter "ProcessId = $targetPid" -ErrorAction SilentlyContinue
if ($null -eq $cim -or -not $cim.CommandLine -or $cim.CommandLine -notmatch "china_apps_mcp") {
    throw "Refusing to stop PID $targetPid because it is not a verified china_apps_mcp process."
}

$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
    Where-Object {
        $_.OwningProcess -eq $targetPid -and
        $_.LocalAddress -in @("127.0.0.1", "::1")
    } |
    Select-Object -First 1

if ($null -eq $listener) {
    throw "Refusing to stop PID $targetPid because it is not the loopback listener on MCP_PORT=$port."
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
