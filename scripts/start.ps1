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
