[CmdletBinding()]
param(
    [int]$Port = 8767,
    [string]$PublicBaseUrl = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$stateDir = Join-Path $repoRoot ".state"
$logsDir = Join-Path $repoRoot "logs"
$pidFile = Join-Path $stateDir "cam-path-prototype.pid"
$secretFile = Join-Path $stateDir "cam-path-prototype-secret.txt"
$stateFile = Join-Path $stateDir "oauth-state-cam-path.json"
$stdoutLog = Join-Path $logsDir "cam-path-prototype.out.log"
$stderrLog = Join-Path $logsDir "cam-path-prototype.err.log"

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Missing .venv. Start the normal CAM once before launching the path prototype."
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw "Port must be between 1 and 65535."
}

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

function New-RandomSecret {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

if ([string]::IsNullOrWhiteSpace($PublicBaseUrl)) {
    $envPath = Join-Path $repoRoot ".env"
    if (-not (Test-Path -LiteralPath $envPath)) {
        throw "Missing .env and no -PublicBaseUrl was supplied."
    }
    $productionBase = ""
    foreach ($line in [IO.File]::ReadAllLines($envPath)) {
        if ($line -match '^\s*MCP_PUBLIC_BASE_URL\s*=\s*(.+?)\s*$') {
            $productionBase = $matches[1].Trim()
            break
        }
    }
    if ([string]::IsNullOrWhiteSpace($productionBase)) {
        throw "MCP_PUBLIC_BASE_URL is missing from .env."
    }
    $productionUri = [Uri]$productionBase
    $PublicBaseUrl = $productionUri.GetLeftPart([UriPartial]::Authority).TrimEnd('/') + "/cam"
}

$publicUri = [Uri]$PublicBaseUrl
if ($publicUri.Scheme -ne "https" -or [string]::IsNullOrWhiteSpace($publicUri.Host)) {
    throw "PublicBaseUrl must be an HTTPS URL."
}
if ($publicUri.AbsolutePath.TrimEnd('/') -ne "/cam" -or $publicUri.Query -or $publicUri.Fragment) {
    throw "The path prototype requires PublicBaseUrl to end exactly in /cam with no query or fragment."
}
$normalizedBase = $publicUri.GetLeftPart([UriPartial]::Authority).TrimEnd('/') + "/cam"

$existingPid = Get-LoopbackListenerPid -LocalPort $Port
if ($null -ne $existingPid) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $existingPid" -ErrorAction SilentlyContinue
    if ($null -ne $proc -and $proc.CommandLine -match "china_apps_mcp\.path_prototype") {
        Set-Content -LiteralPath $pidFile -Value ([string]$existingPid) -Encoding ASCII
        Write-Output "CAM path prototype is already running. PID=$existingPid"
        Write-Output "Public MCP: $normalizedBase/mcp"
        exit 0
    }
    throw "Port $Port is already in use by PID=$existingPid. Refusing to start the prototype."
}

if (-not (Test-Path -LiteralPath $secretFile)) {
    Set-Content -LiteralPath $secretFile -Value (New-RandomSecret) -Encoding ASCII -NoNewline
}
$approvalSecret = (Get-Content -LiteralPath $secretFile -Raw).Trim()
if ($approvalSecret.Length -lt 16) {
    throw "Prototype approval secret is unexpectedly short. Delete $secretFile and retry."
}

Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue

$env:MCP_HOST = "127.0.0.1"
$env:MCP_PORT = [string]$Port
$env:MCP_AUTH_MODE = "oauth"
$env:MCP_PUBLIC_BASE_URL = $normalizedBase
$env:MCP_OAUTH_APPROVAL_SECRET = $approvalSecret
$env:MCP_OAUTH_STATE_FILE = ".state/oauth-state-cam-path.json"
$env:MCP_ALLOWED_HOSTS = $publicUri.Host
$env:MCP_OAUTH_DEBUG_LOG_SECRETS = "0"
$env:BROWSER_ENABLED = "0"

$process = Start-Process `
    -FilePath $venvPython `
    -ArgumentList @("-m", "china_apps_mcp.path_prototype") `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

$healthUrl = "http://127.0.0.1:$Port/health"
$healthy = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 2
        if ($response.StatusCode -eq 200) { $healthy = $true; break }
    }
    catch {}
}

if (-not $healthy) {
    if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    throw "CAM path prototype failed to become healthy. Check logs\cam-path-prototype.err.log."
}

$listenerPid = Get-LoopbackListenerPid -LocalPort $Port
if ($null -eq $listenerPid) {
    if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    throw "Prototype became healthy but its loopback listener PID could not be resolved."
}
$listenerProc = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerPid" -ErrorAction SilentlyContinue
if ($null -eq $listenerProc -or -not $listenerProc.CommandLine -or $listenerProc.CommandLine -notmatch "china_apps_mcp\.path_prototype") {
    if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    throw "Port $Port listener is not a verified CAM path-prototype process."
}

Set-Content -LiteralPath $pidFile -Value ([string]$listenerPid) -Encoding ASCII
Write-Output "CAM path prototype started. PID=$listenerPid"
Write-Output "Local health: $healthUrl"
Write-Output "Public base:  $normalizedBase"
Write-Output "Public MCP:   $normalizedBase/mcp"
Write-Output "Approval secret stays local at: $secretFile"
Write-Output "Browser bridge is disabled in this prototype; production CAM on 8765 is untouched."
