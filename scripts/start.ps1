[CmdletBinding()]
param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

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

Write-Output "Starting China Apps MCP on http://127.0.0.1:8765"
Write-Output "Health: http://127.0.0.1:8765/health"
Write-Output "MCP:    http://127.0.0.1:8765/mcp"
& $venvPython -m china_apps_mcp
