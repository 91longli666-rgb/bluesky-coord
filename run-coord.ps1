param(
    [ValidateSet('demo', 'test', 'validation', 'stress')]
    [string]$Action = 'demo',
    [string]$Python = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$localInterpreter = Join-Path $projectRoot '.venv\Scripts\python.exe'
$workspaceInterpreter = Join-Path $projectRoot '..\..\work\bluesky-venv\Scripts\python.exe'

if (-not $Python) {
    if (Test-Path -LiteralPath $localInterpreter) {
        $Python = $localInterpreter
    } elseif (Test-Path -LiteralPath $workspaceInterpreter) {
        $Python = (Resolve-Path -LiteralPath $workspaceInterpreter).Path
    } else {
        $command = Get-Command python -ErrorAction SilentlyContinue
        if (-not $command) {
            throw 'Python 3.12 is required. Pass its executable with -Python.'
        }
        & $command.Source -m venv (Join-Path $projectRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the local Python environment.' }
        $Python = $localInterpreter
        & $Python -m pip install -r (Join-Path $projectRoot 'requirements-coord.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    }
}
if (-not (Test-Path -LiteralPath $Python)) { throw "Python executable not found: $Python" }
New-Item -ItemType Directory -Path (Join-Path $projectRoot 'cache') -Force | Out-Null
$env:MPLCONFIGDIR = Join-Path $projectRoot 'cache\matplotlib'
Push-Location -LiteralPath $projectRoot
try {
    switch ($Action) {
        'test' {
            & $Python -m pytest tests/coord -q -p no:cacheprovider --basetemp=cache/coord-tests
        }
        'demo' {
            & $Python research/coord/benchmark.py --suite pilot --envelope-baseline
            if ($LASTEXITCODE -ne 0) { throw 'Demo simulation failed.' }
            & $Python research/coord/plot_demo.py
        }
        'validation' {
            & $Python research/coord/benchmark.py --suite validation --envelope-baseline
        }
        'stress' {
            & $Python research/coord/benchmark.py --suite stress --envelope-baseline
        }
    }
    if ($LASTEXITCODE -ne 0) { throw "COORD action failed: $Action" }
} finally {
    Pop-Location
}
