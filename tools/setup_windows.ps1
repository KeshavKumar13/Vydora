$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$app = Join-Path $root 'app'
$venv = Join-Path $app '.venv'

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw 'Python 3.11+ is required. Install Python from python.org and enable Add Python to PATH.'
}

if (-not (Test-Path $venv)) {
  python -m venv $venv
}

$py = Join-Path $venv 'Scripts\python.exe'
& $py -m pip install --upgrade pip
& $py -m pip install -r (Join-Path $app 'requirements.txt')

New-Item -ItemType Directory -Force (Join-Path $app 'bin') | Out-Null
Write-Host ''
Write-Host 'Vydora environment is ready.' -ForegroundColor Green
& (Join-Path $PSScriptRoot 'get_ffmpeg.ps1')
& (Join-Path $PSScriptRoot 'get_deno.ps1')
