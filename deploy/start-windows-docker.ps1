[CmdletBinding()]
param(
    [string]$Config = 'epochgate.yaml',
    [string]$DataDir,
    [ValidateRange(1, 65535)][int]$Port,
    [switch]$CheckOnly,
    [switch]$RefreshRuntime
)

$ErrorActionPreference = 'Stop'
if ($env:OS -ne 'Windows_NT' -or -not $env:LOCALAPPDATA) {
    throw 'This launcher requires Windows and LOCALAPPDATA.'
}

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$runtimeDir = Join-Path $env:LOCALAPPDATA 'EpochGate/docker-runtime'
$python = Join-Path $runtimeDir 'Scripts/python.exe'
$previousProjectEnvironment = $env:UV_PROJECT_ENVIRONMENT
Push-Location -LiteralPath $root
try {
    if ($RefreshRuntime -or -not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $env:UV_PROJECT_ENVIRONMENT = $runtimeDir
        & uv sync --locked --no-dev --no-editable --package epochgate-runtime
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    $probe = 'from epochgate_runtime.sandbox import docker_available; import sys; sys.exit(0 if docker_available() else 1)'
    & $python -I -c $probe
    if ($LASTEXITCODE -ne 0) {
        throw "Python in $runtimeDir cannot access Docker Linux. The control plane was not started."
    }
    Write-Host "Docker Linux engine is accessible from $python."
    if ($CheckOnly) { exit 0 }

    $serve = @('serve', '--config', $Config)
    if ($DataDir) { $serve += @('--data-dir', $DataDir) }
    if ($PSBoundParameters.ContainsKey('Port')) { $serve += @('--port', "$Port") }
    $entry = "from pathlib import Path; from epochgate_runtime.cli import load_project_env; from epochgate_runtime.egctl import app; load_project_env(Path.cwd() / '.env'); app()"
    & $python -I -c $entry @serve
    exit $LASTEXITCODE
} finally {
    $env:UV_PROJECT_ENVIRONMENT = $previousProjectEnvironment
    Pop-Location
}
