[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [switch]$Background
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$stateDir = Join-Path $repoRoot ".state"
$logsDir = Join-Path $repoRoot "logs"
$pidFile = Join-Path $stateDir "mcp.pid"
$stdoutLog = Join-Path $logsDir "gateway.out.log"
$stderrLog = Join-Path $logsDir "gateway.err.log"

if (-not (Test-Path -LiteralPath ".env")) {
    & (Join-Path $PSScriptRoot "init-env.ps1")
}

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        & py.exe -3 -m venv .venv
    }
    elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
        & python.exe -m venv .venv
    }
    else {
        throw "Python 3.11+ was not found."
    }
    if ($LASTEXITCODE -ne 0) { throw "Failed to create virtual environment." }
}

if (-not $SkipInstall) {
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
    & $venvPython -m pip install -e .
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
}

if (-not $Background) {
    Write-Output "Starting China Apps MCP on http://127.0.0.1:8765"
    Write-Output "Health: http://127.0.0.1:8765/health"
    Write-Output "MCP:    http://127.0.0.1:8765/mcp"
    & $venvPython -m china_apps_mcp
    exit $LASTEXITCODE
}

New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

if (Test-Path -LiteralPath $pidFile) {
    $oldPidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
    $oldPid = 0
    if ([int]::TryParse($oldPidText, [ref]$oldPid)) {
        $oldProcess = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
        if ($null -ne $oldProcess) {
            Write-Output "China Apps MCP is already running. PID=$oldPid"
            exit 0
        }
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
}

$process = Start-Process `
    -FilePath $venvPython `
    -ArgumentList @("-m", "china_apps_mcp") `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

Set-Content -LiteralPath $pidFile -Value ([string]$process.Id) -Encoding ASCII

$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500

    if ($process.HasExited) {
        break
    }

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8765/health" -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    }
    catch {
        # Keep waiting until the startup deadline.
    }
}

if (-not $healthy) {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    throw "China Apps MCP failed to become healthy. Check logs\gateway.err.log."
}

Write-Output "China Apps MCP started in background. PID=$($process.Id)"
Write-Output "Health: http://127.0.0.1:8765/health"
Write-Output "MCP:    http://127.0.0.1:8765/mcp"
exit 0
