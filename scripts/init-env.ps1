[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $repoRoot ".env"

if ((Test-Path -LiteralPath $envPath) -and -not $Force) {
    throw ".env already exists. Use -Force only if you intentionally want to replace it."
}

function New-RandomSecret {
    param([int]$ByteCount = 32)

    $bytes = New-Object byte[] $ByteCount
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $rng.GetBytes($bytes)
    }
    finally {
        $rng.Dispose()
    }

    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

$token = New-RandomSecret -ByteCount 32
$oauthSecret = New-RandomSecret -ByteCount 32

$content = @"
MCP_HOST=127.0.0.1
MCP_PORT=8765
MCP_AUTH_MODE=none
MCP_ACCESS_TOKEN=$token
MCP_PUBLIC_BASE_URL=
MCP_OAUTH_APPROVAL_SECRET=$oauthSecret
MCP_OAUTH_STATE_FILE=.state/oauth-state.json
MCP_ALLOWED_HOSTS=
MCP_ALLOWED_ORIGINS=
BILIBILI_COOKIE=
"@

[IO.File]::WriteAllText($envPath, $content, [Text.UTF8Encoding]::new($false))
Write-Output "Created .env with fresh random MCP_ACCESS_TOKEN and MCP_OAUTH_APPROVAL_SECRET values."
Write-Output "Both secrets are stored only in .env; do not commit them or paste them into chat."
