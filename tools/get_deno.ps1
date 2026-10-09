# Downloads Deno (a small JavaScript runtime) into app\bin. yt-dlp needs a JS runtime to solve
# YouTube's challenges; without one, downloads can fail with "HTTP Error 403: Forbidden".
param([switch]$ForBuild)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$bin = Join-Path $root 'app\bin'
New-Item -ItemType Directory -Force $bin | Out-Null

if (Test-Path (Join-Path $bin 'deno.exe')) { return }
if (-not $ForBuild -and ((Get-Command deno -ErrorAction SilentlyContinue) -or (Get-Command node -ErrorAction SilentlyContinue))) { return }

Write-Host 'No JavaScript runtime found - downloading Deno into app\bin (about 40 MB, one time)...' -ForegroundColor Yellow
$tmp = Join-Path $env:TEMP ('mf_deno_' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force $tmp | Out-Null
$zip = Join-Path $tmp 'deno.zip'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
try {
  $ProgressPreference = 'SilentlyContinue'
  Invoke-WebRequest -Uri 'https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip' -OutFile $zip -UseBasicParsing
  Expand-Archive -Path $zip -DestinationPath $tmp -Force
  $f = Get-ChildItem $tmp -Recurse -Filter 'deno.exe' | Select-Object -First 1
  if ($f) { Copy-Item $f.FullName (Join-Path $bin 'deno.exe') -Force; Write-Host 'Deno installed to app\bin.' -ForegroundColor Green }
} catch {
  Write-Host "Could not download Deno automatically ($($_.Exception.Message)). Install it with: winget install DenoLand.Deno  (or install Node.js)." -ForegroundColor Red
}
Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
