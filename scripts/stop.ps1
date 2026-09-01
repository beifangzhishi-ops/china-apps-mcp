[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$stateDir = Join-Path $repoRoot ".state"
$pidFile = Join-Path $stateDir "mcp.pid"

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
if ($null -ne $cim -and $cim.CommandLine -and $cim.CommandLine -notmatch "china_apps_mcp") {
    throw "Refusing to stop PID $targetPid because its command line does not contain china_apps_mcp."
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
