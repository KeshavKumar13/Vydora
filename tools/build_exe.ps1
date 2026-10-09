# Builds a single, self-contained Vydora.exe (Python, yt-dlp, FFmpeg and Deno all bundled inside).
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$app  = Join-Path $root 'app'
$py   = Join-Path $app '.venv\Scripts\python.exe'
$bin  = Join-Path $app 'bin'

if (-not (Test-Path $py)) { & (Join-Path $PSScriptRoot 'setup_windows.ps1') }

Write-Host 'Installing build tools...' -ForegroundColor Cyan
& $py -m pip install --quiet --upgrade -r (Join-Path $app 'requirements.txt') pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'pip install failed' }

Write-Host 'Fetching FFmpeg and Deno to bundle...' -ForegroundColor Cyan
& (Join-Path $PSScriptRoot 'get_ffmpeg.ps1') -ForBuild
& (Join-Path $PSScriptRoot 'get_deno.ps1') -ForBuild
foreach ($f in 'ffmpeg.exe','ffprobe.exe','deno.exe') {
  if (-not (Test-Path (Join-Path $bin $f))) { throw "Missing $f in app\bin - cannot build." }
}

Set-Location $app
& $py -c "import main; main.ensure_icon()"
$ico = Join-Path $app 'assets\app.ico'

Write-Host 'Building Vydora.exe (this takes a few minutes)...' -ForegroundColor Cyan
& $py -m PyInstaller --noconfirm --clean --onefile --windowed --name Vydora `
  --icon $ico `
  --add-binary "$bin\ffmpeg.exe;bin" --add-binary "$bin\ffprobe.exe;bin" --add-binary "$bin\deno.exe;bin" `
  --add-data "$app\assets\sponsored_offer.png;assets" `
  --collect-all yt_dlp --collect-all yt_dlp_ejs `
  --distpath (Join-Path $root 'dist') --workpath (Join-Path $app 'build') --specpath (Join-Path $app 'build') `
  (Join-Path $app 'main.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }

Copy-Item (Join-Path $root 'dist\Vydora.exe') (Join-Path $root 'Vydora.exe') -Force
Write-Host ''
Write-Host "Done: $(Join-Path $root 'Vydora.exe')" -ForegroundColor Green
Write-Host 'This single file can be copied anywhere and run on its own.'
$hash = (Get-FileHash (Join-Path $root 'Vydora.exe') -Algorithm SHA256).Hash.ToLower()
Write-Host "SHA-256: $hash"
Write-Host ''
Write-Host 'To publish an update: raise APP_VERSION in app\main.py, rebuild, then create a GitHub release tagged v<version>' -ForegroundColor Cyan
Write-Host 'and attach Vydora.exe to it. Installed copies will pick it up automatically.' -ForegroundColor Cyan
