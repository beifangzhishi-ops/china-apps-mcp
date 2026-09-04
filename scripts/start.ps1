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
$venvDir = Join-Path $repoRoot ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

function Test-Python311 {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$PrefixArgs = @()
    )

    try {
        & $FilePath @PrefixArgs -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" 2>$null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

function New-CompatibleVenv {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($null -ne $py) {
        foreach ($selector in @("-3.14", "-3.13", "-3.12", "-3.11", "-3")) {
            if (Test-Python311 -FilePath $py.Source -PrefixArgs @($selector)) {
                Write-Output "Creating virtual environment with py.exe $selector"
                & $py.Source $selector -m venv $venvDir
                if ($LASTEXITCODE -eq 0) { return }
            }
        }
    }

    foreach ($name in @("python3.14.exe", "python3.13.exe", "python3.12.exe", "python3.11.exe", "python.exe")) {
        $candidate = Get-Command $name -ErrorAction SilentlyContinue
        if ($null -ne $candidate -and (Test-Python311 -FilePath $candidate.Source)) {
            Write-Output "Creating virtual environment with $($candidate.Source)"
            & $candidate.Source -m venv $venvDir
            if ($LASTEXITCODE -eq 0) { return }
        }
    }

    throw "Python 3.11+ was not found. Install Python 3.11, 3.12, 3.13, or 3.14, then run this script again."
}

function Get-McpPort {
    param([Parameter(Mandatory = $true)][string]$EnvPath)

    $port = 8765
    if (Test-Path -LiteralPath $EnvPath) {
        foreach ($line in [IO.File]::ReadAllLines($EnvPath)) {
            if ($line -match '^\s*MCP_PORT\s*=\s*([0-9]+)\s*(?:#.*)?$') {
                $port = [int]$matches[1]
                break
            }
        }
    }

    if ($port -lt 1 -or $port -gt 65535) {
        throw "MCP_PORT must be between 1 and 65535."
    }
    return $port
}

function Get-LoopbackListenerPid {
    param([Parameter(Mandatory = $true)][int]$Port)

    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1") } |
        Select-Object -First 1
    if ($null -ne $connection) {
        return [int]$connection.OwningProcess
    }

    # Get-NetTCPConnection may return no rows in restricted shells even when
    # netstat can see the listener. Keep launch checks fail-safe in that case.
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

function Normalize-LoopbackProxy {
    foreach ($name in @("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")) {
        $value = [Environment]::GetEnvironmentVariable($name, "Process")
        if ([string]::IsNullOrWhiteSpace($value)) { continue }

        if ($value -match '^https://(127\.0\.0\.1|localhost|\[::1\])(?=[:/])') {
            $normalized = "http://" + $value.Substring(8)
            [Environment]::SetEnvironmentVariable($name, $normalized, "Process")
            Write-Output "Normalized $name from HTTPS loopback proxy to HTTP loopback proxy for this process."
        }
    }
}

if (-not (Test-Path -LiteralPath ".env")) {
    & (Join-Path $PSScriptRoot "init-env.ps1")
}

Normalize-LoopbackProxy

$port = Get-McpPort -EnvPath (Join-Path $repoRoot ".env")
$healthUrl = "http://127.0.0.1:$port/health"
$mcpUrl = "http://127.0.0.1:$port/mcp"

if (Test-Path -LiteralPath $venvPython) {
    if (-not (Test-Python311 -FilePath $venvPython)) {
        Write-Output "Existing .venv uses Python older than 3.11. Recreating it."
        Remove-Item -LiteralPath $venvDir -Recurse -Force
    }
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    New-CompatibleVenv
}

if (-not (Test-Python311 -FilePath $venvPython)) {
    throw "Virtual environment Python is not 3.11+. Remove .venv and install Python 3.11+."
}

$pythonVersion = (& $venvPython -c "import sys; print('.'.join(map(str, sys.version_info[:3])))").Trim()
Write-Output "Using Python $pythonVersion"

if (-not $SkipInstall) {
    # Do not force a pip self-upgrade on every start. The venv-bundled pip is
    # sufficient to install this project and avoids an unnecessary network step.
    & $venvPython -m pip install --disable-pip-version-check -e .
    if ($LASTEXITCODE -ne 0) {
        Write-Output "Dependency installation failed."
        Write-Output "If FlClash is used as a local HTTP proxy, use http://127.0.0.1:<port>, not https://127.0.0.1:<port>."
        throw "Dependency installation failed."
    }
}

if (-not $Background) {
    Write-Output "Starting China Apps MCP on http://127.0.0.1:$port"
    Write-Output "Health: $healthUrl"
    Write-Output "MCP:    $mcpUrl"
    & $venvPython -m china_apps_mcp
    exit $LASTEXITCODE
}

New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

# Refuse to launch a second process onto the configured port. Only an exact
# PID-file/listener match with a healthy CAM may be reported as already running.
$existingPid = Get-LoopbackListenerPid -Port $port

if ($null -ne $existingPid) {
    $existingPid = [int]$existingPid
    $pidFilePid = 0
    $pidFileMatches = $false
    if (Test-Path -LiteralPath $pidFile -PathType Leaf) {
        $pidFileText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
        if ([int]::TryParse($pidFileText, [ref]$pidFilePid) -and $pidFilePid -gt 0 -and $pidFilePid -eq $existingPid) {
            $pidFileMatches = $true
        }
    }

    if ($pidFileMatches -and (Test-CamHealth -Port $port)) {
        $identity = Get-OptionalProcessIdentity -ProcessId $existingPid
        if ($identity -eq "invalid") {
            throw "Port $port is held by a process whose available identity is not china_apps_mcp."
        }

        Set-Content -LiteralPath $pidFile -Value ([string]$existingPid) -Encoding ASCII
        Write-Output "China Apps MCP is already running. PID=$existingPid"
        Write-Output "Health: $healthUrl"
        Write-Output "MCP:    $mcpUrl"
        exit 0
    }

    throw "Port $port is already in use by PID=$existingPid. Refusing to start China Apps MCP."
}

# A PID file without a matching listener is stale. Never trust it by itself:
# Windows may have already reused that PID for an unrelated process.
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue

$process = Start-Process `
    -FilePath $venvPython `
    -ArgumentList @("-m", "china_apps_mcp") `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500

    if (Test-CamHealth -Port $port) {
        $healthy = $true
        break
    }
}

if (-not $healthy) {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }

    $failedPid = Get-LoopbackListenerPid -Port $port
    if ($null -ne $failedPid) {
        $failedPid = [int]$failedPid
        $failedCim = Get-CimInstance Win32_Process -Filter "ProcessId = $failedPid" -ErrorAction SilentlyContinue
        if ($null -ne $failedCim -and $failedCim.CommandLine -and $failedCim.CommandLine -match "china_apps_mcp") {
            Stop-Process -Id $failedPid -Force -ErrorAction SilentlyContinue
        }
    }

    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    throw "China Apps MCP failed to become healthy. Check logs\gateway.err.log."
}

$listenerPid = Get-LoopbackListenerPid -Port $port

if ($null -eq $listenerPid) {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    throw "MCP became healthy but no loopback listener PID was found on port $port."
}

$listenerPid = [int]$listenerPid
$listenerIdentity = Get-OptionalProcessIdentity -ProcessId $listenerPid
if ($listenerIdentity -eq "invalid") {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
    if ($process.Id -ne $listenerPid) {
        $listenerProcess = Get-Process -Id $listenerPid -ErrorAction SilentlyContinue
        if ($null -ne $listenerProcess) {
            Stop-Process -Id $listenerPid -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    throw "The listener on port $port has an available identity that is not china_apps_mcp."
}

Set-Content -LiteralPath $pidFile -Value ([string]$listenerPid) -Encoding ASCII

Write-Output "China Apps MCP started in background. PID=$listenerPid"
if ($process.Id -ne $listenerPid) {
    Write-Output "Launcher PID=$($process.Id); listener PID=$listenerPid"
}
Write-Output "Health: $healthUrl"
Write-Output "MCP:    $mcpUrl"
exit 0
