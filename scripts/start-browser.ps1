[CmdletBinding()]
param(
    [int]$Port = 9222,
    [string]$ProfileDir = "",
    [string]$ChromePath = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot ".env"

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }

    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        if ($line -match ('^\s*' + [Regex]::Escape($Name) + '\s*=\s*(.*?)\s*(?:#.*)?$')) {
            return $matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return ""
}

if (-not $PSBoundParameters.ContainsKey("Port")) {
    $cdpRaw = Get-DotEnvValue -Name "BROWSER_CDP_URL" -Path $envPath
    if (-not [string]::IsNullOrWhiteSpace($cdpRaw)) {
        try {
            $cdpUri = [Uri]$cdpRaw
        }
        catch {
            throw "BROWSER_CDP_URL in .env is not a valid URL: $cdpRaw"
        }
        if ($cdpUri.Scheme -ne "http" -or $cdpUri.Host -notin @("127.0.0.1", "localhost", "::1")) {
            throw "BROWSER_CDP_URL must be a loopback http:// URL."
        }
        $Port = $cdpUri.Port
    }
}

if ($Port -lt 1 -or $Port -gt 65535) {
    throw "Port must be between 1 and 65535."
}

if ([string]::IsNullOrWhiteSpace($ProfileDir)) {
    $ProfileDir = Get-DotEnvValue -Name "BROWSER_PROFILE_DIR" -Path $envPath
}
if ([string]::IsNullOrWhiteSpace($ProfileDir)) {
    $ProfileDir = "profiles\chrome"
}
if (-not [IO.Path]::IsPathRooted($ProfileDir)) {
    $ProfileDir = Join-Path $repoRoot $ProfileDir
}

if ([string]::IsNullOrWhiteSpace($ChromePath)) {
    $ChromePath = Get-DotEnvValue -Name "BROWSER_CHROME_PATH" -Path $envPath
}

function Get-ChromePath {
    param([string]$Override)

    if (-not [string]::IsNullOrWhiteSpace($Override)) {
        if (Test-Path -LiteralPath $Override -PathType Leaf) {
            return (Resolve-Path -LiteralPath $Override).Path
        }
        throw "Chrome executable not found at: $Override"
    }

    foreach ($root in @($env:PROGRAMFILES, ${env:PROGRAMFILES(X86)}, $env:LOCALAPPDATA)) {
        if ([string]::IsNullOrWhiteSpace($root)) { continue }
        $candidate = Join-Path $root "Google\Chrome\Application\chrome.exe"
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }

    throw "Google Chrome was not found. Set BROWSER_CHROME_PATH in .env or pass -ChromePath explicitly."
}

New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null
$ProfileDir = (Resolve-Path -LiteralPath $ProfileDir).Path
$ChromePath = Get-ChromePath -Override $ChromePath

$cdpOrigin = "http://127.0.0.1:$Port"
$endpoint = "$cdpOrigin/json/version"
try {
    $existing = Invoke-WebRequest -UseBasicParsing -Uri $endpoint -TimeoutSec 1
    if ($existing.StatusCode -eq 200) {
        Write-Output "A debuggable Chrome is already available at $cdpOrigin"
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
    throw "Chrome started, but DevTools did not become ready at $cdpOrigin"
}

Write-Output "Chrome ready: $cdpOrigin"
Write-Output "Profile: $ProfileDir"
Write-Output "Log in manually, then call browser_start from China Apps MCP to attach."
