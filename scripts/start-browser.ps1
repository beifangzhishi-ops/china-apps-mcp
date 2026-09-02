[CmdletBinding()]
param(
    [int]$Port = 9222,
    [string]$ProfileDir = "",
    [string]$ChromePath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

if ($Port -lt 1 -or $Port -gt 65535) {
    throw "Port must be between 1 and 65535."
}

function Get-ChromePath {
    param([string]$Override)

    if (-not [string]::IsNullOrWhiteSpace($Override)) {
        if (Test-Path -LiteralPath $Override -PathType Leaf) {
            return (Resolve-Path -LiteralPath $Override).Path
        }
        throw "Chrome executable not found at: $Override"
    }

    foreach ($candidate in @(
        (Join-Path $env:PROGRAMFILES "Google\Chrome\Application\chrome.exe"),
        (Join-Path ${env:PROGRAMFILES(X86)} "Google\Chrome\Application\chrome.exe"),
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe")
    )) {
        if (-not [string]::IsNullOrWhiteSpace($candidate) -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return $candidate
        }
    }

    throw "Google Chrome was not found. Pass -ChromePath explicitly."
}

if ([string]::IsNullOrWhiteSpace($ProfileDir)) {
    $ProfileDir = Join-Path $repoRoot "profiles\chrome"
}
elseif (-not [IO.Path]::IsPathRooted($ProfileDir)) {
    $ProfileDir = Join-Path $repoRoot $ProfileDir
}

New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null
$ProfileDir = (Resolve-Path -LiteralPath $ProfileDir).Path
$ChromePath = Get-ChromePath -Override $ChromePath

$endpoint = "http://127.0.0.1:$Port/json/version"
try {
    $existing = Invoke-WebRequest -UseBasicParsing -Uri $endpoint -TimeoutSec 1
    if ($existing.StatusCode -eq 200) {
        Write-Output "A debuggable Chrome is already available at http://127.0.0.1:$Port"
        exit 0
    }
}
catch {
    # Expected when Chrome has not been started yet.
}

$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalAddress -in @("127.0.0.1", "::1", "0.0.0.0", "::") } |
    Select-Object -First 1
if ($null -ne $listener) {
    throw "Port $Port is already in use by PID=$($listener.OwningProcess), but it is not a Chrome DevTools endpoint."
}

$args = @(
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=$Port",
    "--user-data-dir=`"$ProfileDir`""
)

Start-Process -FilePath $ChromePath -ArgumentList $args | Out-Null

$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 300
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $endpoint -TimeoutSec 1
        if ($response.StatusCode -eq 200) {
            $ready = $true
            break
        }
    }
    catch {
    }
}

if (-not $ready) {
    throw "Chrome started, but DevTools did not become ready at http://127.0.0.1:$Port"
}

Write-Output "Chrome ready: http://127.0.0.1:$Port"
Write-Output "Profile: $ProfileDir"
Write-Output "Log in manually, then call browser_start from China Apps MCP to attach."
