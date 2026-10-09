# Vydora

Vydora is a desktop media downloader (Windows) built on yt-dlp, with a companion browser extension.
Use it only for media you are authorised to download; it does not bypass DRM, paywalls or access controls.

## Run from source
    Set-ExecutionPolicy -Scope Process Bypass -Force
    .\tools\run_windows.ps1

## Build a single Vydora.exe
    .\tools\build_exe.ps1

The exe bundles Python, yt-dlp, FFmpeg and Deno. Settings, history and logs live in `%APPDATA%\Vydora`
(settings from an older "MediaFetch" install are copied over on first start).

## Publishing updates (automatic updater)
1. Raise `APP_VERSION` in `app\main.py` (for example `1.4.1`).
2. Run `.\tools\build_exe.ps1`.
3. On GitHub, open **Releases -> Draft a new release**, tag it `v1.4.1` (same number), attach `Vydora.exe`, publish.
4. Installed copies check on every start, download the new exe in the background, and install it when the app
   is closed (or on the next start, or immediately via "Restart & update").

The repository used for updates is `UPDATE_REPO` in `app\main.py`; the file must be named `Vydora.exe`.
Only HTTPS downloads from GitHub are accepted, and the SHA-256 that GitHub records for the asset is verified.

## Browser extension
Load the `extension` folder as an unpacked extension (Chrome/Edge), then paste the API token from
Vydora -> Settings into the extension options. The desktop API listens on `127.0.0.1:17653` only.

## Sponsored strip
`AD_URL` in `app\main.py` controls the optional "Sponsored" strip. Leave it empty to hide it.
