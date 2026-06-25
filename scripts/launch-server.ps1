# Launch chatxz web server on Windows (source install).
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ServerArgs
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$env:CHATXZ_ROOT = $RepoRoot
$env:PYTHONUNBUFFERED = '1'
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$RepoRoot;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $RepoRoot
}

$venvPy = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$localPy312 = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
$localPy313 = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
if (Test-Path $venvPy) {
    $python = $venvPy
} elseif (Test-Path $localPy312) {
    $python = $localPy312
} elseif (Test-Path $localPy313) {
    $python = $localPy313
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $python = 'py'
    $ServerArgs = @('-3', '-u', '-m', 'chatxz.web.server') + $ServerArgs
    & $python @ServerArgs
    exit $LASTEXITCODE
} elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
    $python = (Get-Command python3).Source
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $python = (Get-Command python).Source
} else {
    Write-Host 'Python not found. Run:  run.cmd web --share'
    exit 1
}

& $python -u -m chatxz.web.server @ServerArgs
exit $LASTEXITCODE