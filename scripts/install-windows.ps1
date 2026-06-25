# chatxz Windows installer — same workflow as Linux/macOS: run.ps1 web --share
param(
    [switch]$Voice,
    [switch]$InstallPython,
    [switch]$NonInteractive
)

$ErrorActionPreference = 'Stop'

Write-Host 'chatxz - Windows Installer'
Write-Host '=========================='
Write-Host ''

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Test-Python310 {
    param([string]$Exe)
    if (-not $Exe) { return $false }
    & $Exe -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>$null | Out-Null
    return $LASTEXITCODE -eq 0
}

function Find-SystemPython {
    $localAppData = [Environment]::GetFolderPath('LocalApplicationData')
    $candidates = @(
        (Join-Path $localAppData 'Programs\Python\Python313\python.exe'),
        (Join-Path $localAppData 'Programs\Python\Python312\python.exe'),
        (Join-Path $localAppData 'Programs\Python\Python311\python.exe'),
        'C:\Program Files\Python313\python.exe',
        'C:\Program Files\Python312\python.exe',
        'C:\Program Files\Python311\python.exe'
    )
    foreach ($path in $candidates) {
        if ((Test-Path $path) -and (Test-Python310 $path)) { return $path }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { return 'PYLAUNCHER' }
    }
    foreach ($name in @('python3', 'python')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notmatch 'WindowsApps' -and (Test-Python310 $cmd.Source)) {
            return $cmd.Source
        }
    }
    return $null
}

function Invoke-Python {
    param([string[]]$PyArgs)
    if ($script:UsePyLauncher) {
        & py -3 @PyArgs
    } else {
        & $script:SystemPython @PyArgs
    }
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
        throw "Python command failed (exit $LASTEXITCODE): $($PyArgs -join ' ')"
    }
}

function Ensure-Python {
    $found = Find-SystemPython
    if ($found) {
        if ($found -eq 'PYLAUNCHER') {
            $script:UsePyLauncher = $true
            $script:SystemPython = $null
            $ver = (& py -3 -c 'import sys; print(sys.version.split()[0])').Trim()
        } else {
            $script:UsePyLauncher = $false
            $script:SystemPython = $found
            $ver = (& $found -c 'import sys; print(sys.version.split()[0])').Trim()
        }
        Write-Host "Using Python $ver"
        return
    }

    Write-Host 'Python 3.10+ not found.'
    if ($InstallPython -or (-not $NonInteractive -and (Read-Host 'Install Python 3.12 via winget? [Y/n]') -notmatch '^[Nn]')) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Write-Host 'winget is not available. Install Python 3.10+ from https://www.python.org/downloads/windows/'
            Write-Host 'During setup, check "Add python.exe to PATH". Then re-run this script.'
            exit 1
        }
        Write-Host 'Installing Python.Python.3.12 via winget...'
        $null = winget install --id Python.Python.3.12 -e --accept-package-agreements --accept-source-agreements 2>&1
        $env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
            [System.Environment]::GetEnvironmentVariable('Path', 'User')
        Ensure-Python
        return
    }

    Write-Host 'Install Python 3.10+ from https://www.python.org/downloads/windows/ (check Add to PATH).'
    exit 1
}

Ensure-Python

$venvDir = Join-Path $RepoRoot '.venv'
$venvPy = Join-Path $venvDir 'Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Write-Host 'Creating virtual environment (.venv)...'
    Invoke-Python @('-m', 'venv', $venvDir)
}
$script:UsePyLauncher = $false
$script:SystemPython = $venvPy

Write-Host 'Upgrading pip...'
Invoke-Python @('-m', 'pip', 'install', '--upgrade', 'pip')

Write-Host 'Installing dependencies (rns, aiohttp)...'
Invoke-Python @('-m', 'pip', 'install', 'rns>=1.3.0', 'aiohttp>=3.9.0')

if ($Voice) {
    Write-Host 'Installing voice support (pyaudio)...'
    Invoke-Python @('-m', 'pip', 'install', 'pyaudio')
} elseif (-not $NonInteractive) {
    $voiceOpt = Read-Host 'Install voice support (pyaudio)? [y/N]'
    if ($voiceOpt -match '^[Yy]') {
        Invoke-Python @('-m', 'pip', 'install', 'pyaudio')
    }
}

Write-Host 'Installing chatxz (editable)...'
Invoke-Python @('-m', 'pip', 'install', '-e', '.')

Write-Host ''
Write-Host 'chatxz installed!'
Write-Host ''
Write-Host 'Start the web UI (LAN accessible):'
Write-Host "  cd $RepoRoot"
Write-Host '  .\run.ps1 web --share'
Write-Host ''
Write-Host 'Open http://localhost:8742 in your browser.'
Write-Host 'Config and data: %USERPROFILE%\.config\chatxz\'
Write-Host 'Optional repo-local data: set CHATXZ_PORTABLE to this folder before starting.'