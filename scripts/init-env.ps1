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

$bytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $rng.GetBytes($bytes)
}
finally {
    $rng.Dispose()
}

$token = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')

$content = @"
MCP_HOST=127.0.0.1
MCP_PORT=8765
MCP_ACCESS_TOKEN=$token
BILIBILI_COOKIE=
"@

[IO.File]::WriteAllText($envPath, $content, [Text.UTF8Encoding]::new($false))
Write-Output "Created .env with a fresh random MCP_ACCESS_TOKEN."
Write-Output "The token is stored only in .env; do not commit or paste it into chat."
