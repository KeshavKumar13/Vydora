$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$app = Join-Path $root 'app'
$py = Join-Path $app '.venv\Scripts\python.exe'
$req = Join-Path $app 'requirements.txt'
if (-not (Test-Path $py)) { & (Join-Path $PSScriptRoot 'setup_windows.ps1') }

# Keep dependencies in sync with requirements.txt (fast no-op when already satisfied).
& $py -m pip install --quiet --upgrade -r $req
if ($LASTEXITCODE -ne 0) { Write-Host 'pip install reported a problem; trying to start anyway.' -ForegroundColor Yellow }

# FFmpeg is needed for merging video+audio and MP3 conversion.
try { & (Join-Path $PSScriptRoot 'get_ffmpeg.ps1') } catch { Write-Host "FFmpeg setup skipped: $($_.Exception.Message)" -ForegroundColor Yellow }

try { & (Join-Path $PSScriptRoot 'get_deno.ps1') } catch { Write-Host "JS runtime setup skipped: $($_.Exception.Message)" -ForegroundColor Yellow }

$ErrorActionPreference = 'Continue'
& $py (Join-Path $app 'main.py')
if ($LASTEXITCODE -ne 0) { Write-Host "Vydora exited with code $LASTEXITCODE" -ForegroundColor Red }
