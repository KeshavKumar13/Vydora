# Downloads FFmpeg (essentials build) into app\bin if it is not already available.
param([switch]$ForBuild)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$bin = Join-Path $root 'app\bin'
New-Item -ItemType Directory -Force $bin | Out-Null

if ((Test-Path (Join-Path $bin 'ffmpeg.exe')) -and (Test-Path (Join-Path $bin 'ffprobe.exe'))) { return }
if (-not $ForBuild -and (Get-Command ffmpeg -ErrorAction SilentlyContinue) -and (Get-Command ffprobe -ErrorAction SilentlyContinue)) { return }

Write-Host 'FFmpeg not found - downloading it into app\bin (about 100 MB, one time)...' -ForegroundColor Yellow
$tmp = Join-Path $env:TEMP ('mf_ffmpeg_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force $tmp | Out-Null
$zip = Join-Path $tmp 'ffmpeg.zip'
$urls = @(
  'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip',
  'https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip'
)
$ok = $false
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
foreach ($u in $urls) {
  try {
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $u -OutFile $zip -UseBasicParsing
    $ok = $true; break
  } catch { Write-Host "Download failed from $u : $($_.Exception.Message)" -ForegroundColor DarkYellow }
}
if (-not $ok) {
  Write-Host 'Could not download FFmpeg automatically. Install it manually (winget install Gyan.FFmpeg) or copy ffmpeg.exe and ffprobe.exe into app\bin.' -ForegroundColor Red
  return
}
Expand-Archive -Path $zip -DestinationPath $tmp -Force
foreach ($name in 'ffmpeg.exe','ffprobe.exe') {
  $f = Get-ChildItem $tmp -Recurse -Filter $name | Select-Object -First 1
  if ($f) { Copy-Item $f.FullName (Join-Path $bin $name) -Force }
}
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
Write-Host 'FFmpeg installed to app\bin.' -ForegroundColor Green
