param([Parameter(Mandatory=$true)][string]$OutputDirectory, [string]$Python = ".venv/Scripts/python.exe", [switch]$Resume)
$ErrorActionPreference = 'Stop'
if (-not $Resume) {
    if (Test-Path -LiteralPath $OutputDirectory) { throw "Choose a new output directory" }
    New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
}
$root = (Resolve-Path -LiteralPath $OutputDirectory).Path
$repo = Join-Path $root 'repo'
if (-not $Resume) {
    New-Item -ItemType Directory -Path $repo | Out-Null
    Copy-Item -Path "$PSScriptRoot/fixture/*" -Destination $repo
    git -C $repo init -q
    git -C $repo config core.autocrlf false
    git -C $repo add -- .
    git -C $repo -c user.name='EpochGate Fixture' -c user.email='fixture@localhost' commit -qm 'Seed coding fixture'
} elseif (-not (Test-Path -LiteralPath (Join-Path $root "trace.json"))) { throw "No persisted trace to resume" }
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$trace = Join-Path $root 'trace.json'
for ($step = 0; $step -lt 55; $step++) {
    & $pythonPath "$PSScriptRoot/agent.py" $root
    if ($LASTEXITCODE -ne 0) { throw 'Agent step failed' }
    $state = Get-Content -LiteralPath $trace -Raw -Encoding utf8 | ConvertFrom-Json
    if ($state.status -ne 'running') { break }
    if ($null -ne $state.pending_test) {
        $started = [Diagnostics.Stopwatch]::StartNew()
        try {
            $ErrorActionPreference = 'Continue'
            $raw = & docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --pids-limit 32 --memory 256m --user 65534:65534 --mount "type=bind,source=$repo,target=/src,readonly" --workdir /src python:3.10-slim timeout 30s python -B -m unittest -v 2>&1
            $exitCode = $LASTEXITCODE
        } finally { $ErrorActionPreference = 'Stop' }
        $started.Stop()
        $testLog = $raw | ForEach-Object { $_.ToString() }
        if ($exitCode -ge 125) { throw "Docker runner error $exitCode" }
        $bytes = [IO.File]::ReadAllBytes((Join-Path $repo 'shipping.py'))
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $hex = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant() }
        finally { $sha.Dispose() }
        @{ticket=$state.pending_test; exit_code=$exitCode; source_sha256=$hex; seconds=$started.Elapsed.TotalSeconds; output=($testLog -join [Environment]::NewLine); isolation='docker-linux-readonly-no-network'} |
            ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $root 'test-result.json') -Encoding utf8
    }
}
git -C $repo diff -- shipping.py | Set-Content -Encoding utf8 -LiteralPath (Join-Path $root 'agent.patch')
git -C $repo diff -- rules.py | Set-Content -Encoding utf8 -LiteralPath (Join-Path $root 'world-change.patch')
if ($state.status -ne 'completed') { throw "Coding job stopped with $($state.status)" }
"Completed coding task with $($state.events.Count) recorded events"
