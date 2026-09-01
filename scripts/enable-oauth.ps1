[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$PublicBaseUrl,
    [switch]$RotateSecret
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot ".env"

if (-not (Test-Path -LiteralPath $envPath)) {
    & (Join-Path $PSScriptRoot "init-env.ps1")
}

try {
    $uri = [Uri]$PublicBaseUrl
}
catch {
    throw "PublicBaseUrl must be a valid URL, for example https://node.tailnet.ts.net"
}

if ($uri.Scheme -ne "https") {
    throw "PublicBaseUrl must use https://"
}
if ([string]::IsNullOrWhiteSpace($uri.Host)) {
    throw "PublicBaseUrl must include a hostname."
}
if (($uri.AbsolutePath -ne "/") -or (-not [string]::IsNullOrWhiteSpace($uri.Query)) -or (-not [string]::IsNullOrWhiteSpace($uri.Fragment))) {
    throw "PublicBaseUrl must be only the HTTPS origin, with no path, query, or fragment."
}

$normalizedBase = "https://" + $uri.Host
if (-not $uri.IsDefaultPort) {
    $normalizedBase += ":" + $uri.Port
}

$lines = [System.Collections.Generic.List[string]]::new()
foreach ($line in [IO.File]::ReadAllLines($envPath)) {
    $lines.Add($line)
}

function Get-EnvValue {
    param([string]$Name)
    foreach ($line in $lines) {
        if ($line.StartsWith($Name + "=")) {
            return $line.Substring($Name.Length + 1)
        }
    }
    return ""
}

function Set-EnvValue {
    param([string]$Name, [string]$Value)
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i].StartsWith($Name + "=")) {
            $lines[$i] = $Name + "=" + $Value
            return
        }
    }
    $lines.Add($Name + "=" + $Value)
}

function New-RandomSecret {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

$approvalSecret = Get-EnvValue "MCP_OAUTH_APPROVAL_SECRET"
if ($RotateSecret -or [string]::IsNullOrWhiteSpace($approvalSecret) -or $approvalSecret.Length -lt 16) {
    $approvalSecret = New-RandomSecret
    Set-EnvValue "MCP_OAUTH_APPROVAL_SECRET" $approvalSecret
}

$existingAllowedHosts = Get-EnvValue "MCP_ALLOWED_HOSTS"
$allowedHosts = @($existingAllowedHosts -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($allowedHosts -notcontains $uri.Host) {
    $allowedHosts += $uri.Host
}

Set-EnvValue "MCP_AUTH_MODE" "oauth"
Set-EnvValue "MCP_PUBLIC_BASE_URL" $normalizedBase
Set-EnvValue "MCP_ALLOWED_HOSTS" ($allowedHosts -join ',')
if ([string]::IsNullOrWhiteSpace((Get-EnvValue "MCP_OAUTH_STATE_FILE"))) {
    Set-EnvValue "MCP_OAUTH_STATE_FILE" ".state/oauth-state.json"
}

$content = ($lines -join [Environment]::NewLine) + [Environment]::NewLine
[IO.File]::WriteAllText($envPath, $content, [Text.UTF8Encoding]::new($false))

Write-Output "OAuth mode enabled in .env."
Write-Output "Public base: $normalizedBase"
Write-Output "MCP URL:     $normalizedBase/mcp"
Write-Output "The approval secret remains local in .env. Do not paste it into chat."
Write-Output "Restart the gateway, then run scripts/test-oauth-discovery.py."
