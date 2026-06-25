# chatxz runner for Windows — same as ./run.sh on Linux/macOS.
param(
    [Parameter(Position = 0)]
    [string]$Command = '',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'

$RepoRoot = $PSScriptRoot
Set-Location $RepoRoot
$env:CHATXZ_ROOT = $RepoRoot
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$RepoRoot;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $RepoRoot
}

. (Join-Path $RepoRoot 'scripts\ensure-windows-deps.ps1')

function Resolve-Python {
    $venvPy = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (Test-Path $venvPy) { return $venvPy }
    return $null
}

function Invoke-Python {
    param([string[]]$PyArgs)
    $py = Resolve-Python
    if (-not $py) {
        $py = Ensure-WindowsDeps -Quiet
    }
    & $py @PyArgs
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Install-Deps {
    $null = Ensure-WindowsDeps
}

switch ($Command) {
    'install' {
        $null = Ensure-WindowsDeps
        Write-Host 'Done. Run:  run.cmd web --share'
    }
    { $_ -in 'web', 'server' } {
        Install-Deps
        $py = Resolve-Python
        if (-not $py) { $py = Ensure-WindowsDeps -Quiet }
        $env:PYTHONUNBUFFERED = '1'
        Write-Host 'chatxz web server — logs below. Press Ctrl+C to stop.'
        Write-Host ''
        & $py -u -m chatxz.web.server @Rest
        exit $LASTEXITCODE
    }
    'cli' {
        Install-Deps
        Invoke-Python (@('-m', 'chatxz.app') + $Rest)
    }
    default {
        Write-Host 'chatxz - Reticulum Chat'
        Write-Host ''
        Write-Host 'Usage: run.cmd <command> [args]   (from cmd — do NOT use .\run.ps1 in cmd)'
        Write-Host '       .\run.ps1 <command> [args]  (from PowerShell only)'
        Write-Host ''
        Write-Host 'Commands:'
        Write-Host '  web [--share] [--verbose] [--debug] [--force]  Start web server'
        Write-Host '  cli [options]    Start CLI mode'
        Write-Host '  install          Re-run dependency setup (.venv)'
        Write-Host ''
        Write-Host 'Examples:'
        Write-Host '  run.cmd web --share'
        Write-Host '  run.cmd web --share --force'
        Write-Host ''
        Write-Host 'Git Bash (same as Linux):  ./run.sh web --share'
        Write-Host 'Requires Python 3.10+ on PATH (auto-setup on first run).'
    }
}