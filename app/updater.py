"""Self-updater for the packaged exe.

Flow
  1. check()    - ask GitHub Releases for the latest release of `repo`.
  2. download() - if it is newer, download the exe in the background, verify it, and record it in
                  updates/pending.json.  This happens whether or not the user reacts to any prompt.
  3. apply()    - hand the new exe to a tiny detached helper script that waits for this app to exit,
                  swaps the file, and (optionally) starts the new version.
                  Called when the user clicks "Restart & update", when the app closes, or on the next
                  start-up if an update is still waiting.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CREATE_NEW_PROCESS_GROUP = 0x00000200 if os.name == "nt" else 0
MAX_APPLY_ATTEMPTS = 2          # protects against an endless "update -> restart -> update" loop
MIN_EXE_BYTES = 5 * 1024 * 1024


def parse_version(text):
    """'v1.4.2' -> (1, 4, 2).  Anything non-numeric after the first dash is ignored."""
    head = re.split(r"[-+ ]", str(text).strip().lstrip("vV"), maxsplit=1)[0]
    nums = [int(n) for n in re.findall(r"\d+", head)]
    return tuple((nums + [0, 0, 0])[:max(3, len(nums))])


def is_newer(candidate, current):
    return parse_version(candidate) > parse_version(current)


class Updater:
    def __init__(self, repo, asset_name, current_version, data_dir, user_agent="updater",
                 api_base="https://api.github.com", allowed_hosts=None, enabled=True):
        self.repo = repo
        self.asset_name = asset_name
        self.current = current_version
        self.dir = Path(data_dir) / "updates"
        self.agent = user_agent
        self.api_base = api_base.rstrip("/")
        # Only talk to GitHub (and the CDN GitHub redirects release downloads to).
        self.allowed_hosts = allowed_hosts or ("api.github.com", "github.com", ".githubusercontent.com")
        self.enabled = enabled
        self.state = "idle"          # idle | checking | uptodate | downloading | ready | error
        self.version = ""            # newest version found
        self.notes = ""
        self.progress = 0.0          # 0..100 while downloading
        self.message = ""            # last human-readable status
        self.on_change = None        # callback(updater), may be called from a worker thread
        self._lock = threading.Lock()
        self._busy = False

    # ---------------------------------------------------------------- helpers
    def _emit(self):
        cb = self.on_change
        if cb:
            try:
                cb(self)
            except Exception:
                pass

    def _set(self, state, message=""):
        self.state = state
        if message:
            self.message = message
        self._emit()

    def host_ok(self, url):
        parts = urllib.parse.urlparse(url)
        host = (parts.hostname or "").lower()
        local_ok = self.api_base.startswith("http://127.0.0.1") and host == "127.0.0.1"   # unit tests only
        if parts.scheme != "https" and not local_ok:
            return False
        for allowed in self.allowed_hosts:
            if allowed.startswith("."):
                if host.endswith(allowed):
                    return True
            elif host == allowed:
                return True
        return local_ok

    def _open(self, url, accept=None, timeout=30):
        if not self.host_ok(url):
            raise RuntimeError(f"Blocked update URL: {url}")
        headers = {"User-Agent": self.agent}
        if accept:
            headers["Accept"] = accept
        resp = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)
        if not self.host_ok(resp.geturl()):                     # after redirects
            resp.close()
            raise RuntimeError("Update download was redirected to an untrusted host")
        return resp

    def pending_file(self):
        return self.dir / "pending.json"

    # ---------------------------------------------------------------- check
    def fetch_latest(self):
        url = f"{self.api_base}/repos/{self.repo}/releases/latest"
        with self._open(url, accept="application/vnd.github+json") as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        tag = data.get("tag_name") or data.get("name") or ""
        assets = data.get("assets") or []
        asset = next((a for a in assets if a.get("name", "").lower() == self.asset_name.lower()), None)
        if asset is None:
            asset = next((a for a in assets if a.get("name", "").lower().endswith(".exe")), None)
        sha = ""
        if asset:
            digest = str(asset.get("digest") or "")
            if digest.lower().startswith("sha256:"):
                sha = digest.split(":", 1)[1].strip().lower()
            if not sha:
                side = next((a for a in assets if a.get("name", "").lower() == asset["name"].lower() + ".sha256"), None)
                if side:
                    try:
                        with self._open(side["browser_download_url"]) as r:
                            sha = r.read().decode("utf-8", "replace").split()[0].strip().lower()
                    except Exception:
                        sha = ""
        return {"version": tag.lstrip("vV"), "notes": data.get("body") or "", "asset": asset, "sha256": sha}

    def check_and_download(self, manual=False):
        """Blocking: check for a newer release and download it.  Safe to call from a thread."""
        if not self.enabled:
            self._set("idle", "Updates apply to the packaged app only.")
            return
        with self._lock:
            if self._busy:
                return
            self._busy = True
        try:
            if self.pending() and is_newer(self.pending()["version"], self.current):
                self.version = self.pending()["version"]
                self._set("ready", f"Version {self.version} is ready to install.")
                return
            self._set("checking", "Checking for updates...")
            info = self.fetch_latest()
            self.notes = info["notes"]
            if not info["version"] or not is_newer(info["version"], self.current):
                self._set("uptodate", "You're up to date.")
                return
            if not info["asset"]:
                self._set("error", f"Version {info['version']} has no {self.asset_name} attached.")
                return
            self.version = info["version"]
            self._download(info)
        except Exception as e:
            self._set("error", f"Update check failed: {str(e)[:140]}" if manual else "")
            self.state = "error"
        finally:
            self._busy = False

    def start_background_check(self, manual=False):
        threading.Thread(target=self.check_and_download, args=(manual,), daemon=True).start()

    # ---------------------------------------------------------------- download
    def _download(self, info):
        self.dir.mkdir(parents=True, exist_ok=True)
        asset = info["asset"]
        final = self.dir / f"{Path(self.asset_name).stem}-{info['version']}.exe"
        part = final.with_suffix(".exe.part")
        self.progress = 0.0
        self._set("downloading", f"Downloading version {info['version']}...")
        sha = hashlib.sha256()
        size = 0
        with self._open(asset["browser_download_url"], timeout=60) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or asset.get("size") or 0)
            first = True
            while True:
                chunk = r.read(1024 * 256)
                if not chunk:
                    break
                if first:
                    first = False
                    if chunk[:2] != b"MZ":
                        raise RuntimeError("Downloaded file is not a Windows program")
                f.write(chunk)
                sha.update(chunk)
                size += len(chunk)
                if total:
                    new = min(99.0, size * 100.0 / total)
                    if new - self.progress >= 1:
                        self.progress = new
                        self._emit()
        digest = sha.hexdigest()
        if size < MIN_EXE_BYTES and not self.api_base.startswith("http://127.0.0.1"):
            part.unlink(missing_ok=True)
            raise RuntimeError("Downloaded file is too small")
        if info["sha256"] and digest != info["sha256"]:
            part.unlink(missing_ok=True)
            raise RuntimeError("Checksum mismatch - the download was discarded")
        os.replace(part, final)
        self.pending_file().write_text(json.dumps({
            "version": info["version"], "path": str(final), "sha256": digest, "attempts": 0,
        }), encoding="utf-8")
        self.progress = 100.0
        self._set("ready", f"Version {info['version']} is ready to install.")

    # ---------------------------------------------------------------- pending / apply
    def pending(self):
        """The downloaded-but-not-installed update, or None (also cleans up stale files)."""
        try:
            data = json.loads(self.pending_file().read_text(encoding="utf-8"))
            path = Path(data["path"])
            if not path.is_file() or not is_newer(data["version"], self.current):
                self.discard_pending()
                return None
            return data
        except Exception:
            return None

    def discard_pending(self):
        try:
            data = json.loads(self.pending_file().read_text(encoding="utf-8"))
            Path(data.get("path", "")).unlink(missing_ok=True)
        except Exception:
            pass
        self.pending_file().unlink(missing_ok=True)

    def apply(self, target_exe=None, relaunch=False, launch=True):
        """Install the pending update after this process exits.  Returns True if the helper was started."""
        data = self.pending()
        if not data:
            return False
        if int(data.get("attempts", 0)) >= MAX_APPLY_ATTEMPTS:
            self.discard_pending()
            return False
        target = Path(target_exe or sys.executable)
        new = Path(data["path"])
        try:
            # Re-verify the file we are about to install.
            h = hashlib.sha256()
            with open(new, "rb") as f:
                for block in iter(lambda: f.read(1024 * 1024), b""):
                    h.update(block)
            if data.get("sha256") and h.hexdigest() != data["sha256"]:
                self.discard_pending()
                return False
            staged = target.with_name(target.name + ".new")
            shutil.copyfile(new, staged)             # same folder as the target => the final swap is a fast rename
            data["attempts"] = int(data.get("attempts", 0)) + 1
            self.pending_file().write_text(json.dumps(data), encoding="utf-8")
            script = self.dir / "apply_update.cmd"
            script.write_text(self.script_text(target, staged, new, relaunch), encoding="utf-8")
            if launch and os.name == "nt":
                env = os.environ.copy()
                env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"      # the relaunched exe must unpack on its own
                env.pop("_MEIPASS2", None)
                subprocess.Popen(["cmd.exe", "/c", str(script)], env=env, close_fds=True,
                                 creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
            return True
        except Exception:
            return False

    @staticmethod
    def script_text(target, staged, downloaded, relaunch):
        return "\r\n".join([
            "@echo off",
            f'set "TARGET={target}"',
            f'set "STAGED={staged}"',
            f'set "DOWNLOADED={downloaded}"',
            f'set "RELAUNCH={1 if relaunch else 0}"',
            "set /a tries=0",
            ":retry",
            "ping -n 2 127.0.0.1 >nul",
            'move /y "%STAGED%" "%TARGET%" >nul 2>&1',
            "if not errorlevel 1 goto swapped",
            "set /a tries+=1",
            "if %tries% lss 90 goto retry",
            'del "%STAGED%" >nul 2>&1',
            "goto finish",
            ":swapped",
            'del "%DOWNLOADED%" >nul 2>&1',
            f'del "{Path(downloaded).parent / "pending.json"}" >nul 2>&1',
            'if "%RELAUNCH%"=="1" start "" "%TARGET%"',
            ":finish",
            '(goto) 2>nul & del "%~f0"',
            "",
        ])
