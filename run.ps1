# chatxz runner for Windows — same commands as ./run.sh on Linux/macOS.
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

function Resolve-Python {
    $venvPy = Join-Path $RepoRoot '.venv\Scripts\python.exe'
    if (Test-Path $venvPy) { return $venvPy }
    $localPy312 = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    $localPy313 = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
    if (Test-Path $localPy312) { return $localPy312 }
    if (Test-Path $localPy313) { return $localPy313 }
    if (Get-Command py -ErrorAction SilentlyContinue) { return 'py' }
    if (Get-Command python3 -ErrorAction SilentlyContinue) { return (Get-Command python3).Source }
    if (Get-Command python -ErrorAction SilentlyContinue) { return (Get-Command python).Source }
    return $null
}

function Invoke-Python {
    param([string[]]$CmdArgs)
    $py = Resolve-Python
    if (-not $py) {
        Write-Host 'Python not found. Run: powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1'
        exit 1
    }
    if ($py -eq 'py') {
        & py -3 @CmdArgs
    } else {
        & $py @CmdArgs
    }
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Install-Deps {
    if (Test-Path (Join-Path $RepoRoot '.venv\Scripts\python.exe')) {
        return
    }
    $py = Resolve-Python
    if (-not $py) {
        & "$RepoRoot\scripts\install-windows.ps1"
        return
    }
    Write-Host 'Installing dependencies...'
    if ($py -eq 'py') {
        & py -3 -m pip install "rns>=1.3.0" "aiohttp>=3.9.0"
    } else {
        & $py -m pip install "rns>=1.3.0" "aiohttp>=3.9.0"
    }
}

switch ($Command) {
    'install' {
        & "$RepoRoot\scripts\install-windows.ps1" @Rest
    }
    { $_ -in 'web', 'server' } {
        Install-Deps
        & "$RepoRoot\scripts\launch-server.ps1" @Rest
    }
    'cli' {
        Install-Deps
        if ((Resolve-Python) -eq 'py') {
            Invoke-Python -CmdArgs (@('-m', 'chatxz.app') + $Rest)
        } else {
            Invoke-Python -CmdArgs (@('-m', 'chatxz.app') + $Rest)
        }
    }
    default {
        Write-Host 'chatxz - Reticulum Chat'
        Write-Host ''
        Write-Host 'Usage: .\run.ps1 <command> [args]'
        Write-Host ''
        Write-Host 'Commands:'
        Write-Host '  install          Install dependencies into .venv (first-time setup)'
        Write-Host '  web [--share] [--verbose] [--debug] [--force]  Start web server'
        Write-Host '  cli [options]    Start CLI mode'
        Write-Host ''
        Write-Host 'Examples:'
        Write-Host '  .\run.ps1 web'
        Write-Host '  .\run.ps1 web --share    # LAN access from other devices'
        Write-Host '  .\run.ps1 web --share --force'
        Write-Host ''
        Write-Host 'First-time setup:'
        Write-Host '  powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1'
        Write-Host '  .\run.ps1 web --share'
    }
}