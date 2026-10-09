import json
import os
import queue
import secrets
import shutil
import subprocess
import sys
import re
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from updater import Updater
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

APP_NAME = "Vydora"
APP_VERSION = "1.4.2"
USER_WATERMARK = "KeshavKumar13"
GITHUB_URL = "https://github.com/KeshavKumar13/"
# Self-update source: GitHub Releases of this repository. The release must contain an asset named ASSET_NAME.
UPDATE_REPO = "KeshavKumar13/Vydora"
ASSET_NAME = f"{APP_NAME}.exe"

# ---- Sponsored strip (leave AD_URL empty to hide it) ----
AD_URL = "https://asiafilm.org/4/d06566d143eeefdd7f3dda37da5a120c"  # Adsterra Smartlink (opened only when the user clicks)
AD_IMAGE_URL = ""      # optional remote override; local banner is used by default
AD_TEXT = "Sponsored offer"
# Top-quality downloads open the sponsor page, but never more often than this:
SPONSOR_COOLDOWN_MIN = 30
AD_IMAGE_PATH = Path(getattr(sys, "_MEIPASS", ROOT if "ROOT" in globals() else Path(__file__).resolve().parent)) / "assets" / "sponsored_offer.png"
LINK_BLUE = "#3ea6ff"
QUALITY_BEST = "Best available"
API_HOST = "127.0.0.1"
API_PORT = 17653
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    # Packaged exe: keep settings/history/logs in %APPDATA%\Vydora; bundled tools live in the unpack dir.
    _appdata = Path(os.environ.get("APPDATA") or Path.home())
    ROOT = _appdata / APP_NAME
    _legacy = _appdata / "MediaFetch"          # settings/history from the pre-rename app
    if not ROOT.exists() and _legacy.exists():
        try:
            shutil.copytree(_legacy, ROOT, ignore=shutil.ignore_patterns("logs", "thumbnail_cache", "updates", "yt-dlp*.exe"))
        except Exception:
            pass
    BIN = Path(getattr(sys, "_MEIPASS", ROOT)) / "bin"
else:
    ROOT = Path(__file__).resolve().parent
    BIN = ROOT / "bin"
ROOT.mkdir(parents=True, exist_ok=True)
ASSETS = ROOT / "assets"
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
CONFIG = ROOT / "mediafetch.json"
HISTORY = ROOT / "history.json"
THUMB_CACHE = ROOT / "thumbnail_cache"
THUMB_CACHE.mkdir(exist_ok=True)


def load_config():
    defaults = {
        "download_dir": str(Path.home() / "Downloads" / APP_NAME),
        "browser_cookies": "none",
        "cookies_file": "",
        "show_dislikes": True,
        "auto_update": True,
        "auto_browser_retry": False,
        "api_token": secrets.token_urlsafe(32),
        "max_concurrent": 2,
        "last_sponsor_open": 0,
    }
    try:
        if CONFIG.exists():
            data = json.loads(CONFIG.read_text(encoding="utf-8"))
            defaults.update(data)
    except Exception:
        pass
    Path(defaults["download_dir"]).mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(defaults, indent=2), encoding="utf-8")
    return defaults


def load_history():
    try:
        if HISTORY.exists():
            data = json.loads(HISTORY.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def save_history(items):
    try:
        HISTORY.write_text(json.dumps(items[-500:], indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def executable(name):
    local = BIN / (name + (".exe" if os.name == "nt" else ""))
    if local.exists():
        return str(local)
    return shutil.which(name)


def js_runtime():
    """('deno'|'node', path) for the JavaScript runtime yt-dlp can use, or None."""
    for name in ("deno", "node"):
        path = executable(name)
        if path:
            return name, path
    return None


def yt_dlp_cmd():
    updated = ROOT / "yt-dlp.exe"
    if FROZEN and updated.is_file():
        cmd = [str(updated)]          # newer copy downloaded from Settings -> Update yt-dlp
    elif FROZEN:
        cmd = [sys.executable, "--run-yt-dlp"]
    else:
        cmd = [sys.executable, "-m", "yt_dlp"]
    # Recent YouTube extraction needs a JavaScript runtime to solve its challenges.
    rt = js_runtime()
    if rt:
        cmd += ["--js-runtimes", f"{rt[0]}:{rt[1]}"]
    return cmd


def tool_env():
    """Environment for child processes: UTF-8 output and FFmpeg/Deno on PATH."""
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PATH"] = str(BIN) + os.pathsep + env.get("PATH", "")
    return env


def make_updater():
    return Updater(UPDATE_REPO, ASSET_NAME, APP_VERSION, ROOT, user_agent=f"{APP_NAME}/{APP_VERSION}", enabled=FROZEN)


def run_yt_dlp_cli(args):
    """Entry point used by the packaged exe: behave exactly like the yt-dlp command line."""
    for name, fd in (("stdout", 1), ("stderr", 2)):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(fd, "w", encoding="utf-8", errors="replace", closefd=False))
            except OSError:
                setattr(sys, name, open(os.devnull, "w"))
    import yt_dlp
    yt_dlp.main(args)


def clean_error(text):
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    if not lines:
        return "Unknown error"
    for line in reversed(lines):
        if not line.startswith("WARNING:"):
            return line
    return lines[-1]


def format_size(value):
    if value is None:
        return "Unknown"
    try:
        n = float(value)
    except Exception:
        return "Unknown"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def format_duration(seconds):
    if seconds is None:
        return ""
    try:
        seconds = int(seconds)
    except Exception:
        return ""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


COOKIES_FILE = ""


def cookie_args(browser="none"):
    """A cookies.txt file wins (it avoids Chrome's DPAPI/app-bound encryption problems)."""
    if COOKIES_FILE and Path(COOKIES_FILE).is_file():
        return ["--cookies", COOKIES_FILE]
    if browser and browser != "none":
        return ["--cookies-from-browser", browser]
    return []


def analyze_url(url, cookies="none"):
    cmd = yt_dlp_cmd() + ["--dump-single-json", "--no-warnings", "--skip-download"]
    cmd += cookie_args(cookies)
    cmd += [url]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=120, env=tool_env(), creationflags=NO_WINDOW)
    if proc.returncode != 0:
        raise RuntimeError(clean_error(proc.stderr or proc.stdout))
    return json.loads(proc.stdout)


def build_formats(info):
    rows = []
    for f in info.get("formats", []):
        v = f.get("vcodec") not in (None, "none")
        a = f.get("acodec") not in (None, "none")
        if not v and not a:
            continue
        rows.append({
            "id": str(f.get("format_id") or ""),
            "ext": f.get("ext", ""),
            "height": f.get("height"),
            "width": f.get("width"),
            "fps": f.get("fps"),
            "vcodec": f.get("vcodec") or "none",
            "acodec": f.get("acodec") or "none",
            "size": f.get("filesize") or f.get("filesize_approx"),
            "tbr": f.get("tbr"),
            "has_video": v,
            "has_audio": a,
            "protocol": f.get("protocol", ""),
            "note": f.get("format_note") or "",
        })
    rows.sort(key=lambda x: (
        1 if x["has_video"] else 0,
        x.get("height") or 0,
        1 if x["has_audio"] else 0,
        x.get("tbr") or 0,
    ), reverse=True)
    return rows


def short_vcodec(c):
    c = (c or "").lower()
    if c.startswith(("avc", "h264")): return "H.264"
    if c.startswith(("av01", "av1")): return "AV1"
    if c.startswith(("vp09", "vp9")): return "VP9"
    if c.startswith(("hev", "hvc", "h265")): return "HEVC"
    if c.startswith("vp8"): return "VP8"
    return "" if c in ("", "none") else c.split(".")[0].upper()


def short_acodec(c):
    c = (c or "").lower()
    if c.startswith("mp4a"): return "AAC"
    if c.startswith("opus"): return "Opus"
    if c.startswith("vorbis"): return "Vorbis"
    if c.startswith(("ec-3", "ac-3")): return "Dolby"
    if c.startswith("mp3"): return "MP3"
    if c.startswith("flac"): return "FLAC"
    return "" if c in ("", "none") else c.split(".")[0].upper()


def format_bitrate(tbr):
    if not tbr:
        return ""
    return f"{tbr / 1000:.1f} Mbps" if tbr >= 1000 else f"{tbr:.0f} kbps"


def quality_label(h):
    extra = {4320: " (8K)", 2160: " (4K)", 1440: " (2K)"}.get(h, "")
    return f"{h}p{extra}"


def format_label(f):
    if not f:
        return "Automatic"
    ext = (f.get("ext") or "").upper()
    if f["has_video"]:
        res = f"{f['height']}p" if f.get("height") else "Video"
        parts = [res, f"{ext} {short_vcodec(f.get('vcodec'))}".strip()]
        if f.get("fps"):
            parts.append(f"{f['fps']:.0f} fps")
        if not f["has_audio"]:
            parts.append("video only")
    else:
        parts = ["Audio", f"{ext} {short_acodec(f.get('acodec'))}".strip()]
    parts.append(format_size(f["size"]) if f.get("size") else format_bitrate(f.get("tbr")))
    parts.append(f"[{f['id']}]")
    return "  •  ".join(p for p in parts if p)


@dataclass
class Job:
    id: int
    url: str
    mode: str
    quality: str
    title: str = ""
    format_selector: str = ""
    format_label: str = "Automatic"
    thumbnail: str = ""
    status: str = "Queued"
    progress: float = 0.0
    speed: str = ""
    eta: str = ""
    error: str = ""
    file_path: str = ""
    created_at: str = ""
    completed_at: str = ""
    est_size: float = 0.0
    actual_size: float = 0.0
    log_path: str = ""
    process: object = None


class DownloadManager:
    def __init__(self, app):
        self.app = app
        self.jobs = []
        self.counter = max([int(x.get("id", 0)) for x in app.history if str(x.get("id", "")).isdigit()] + [0])
        self.q = queue.Queue()
        self.lock = threading.Lock()
        for _ in range(max(1, int(app.config.get("max_concurrent", 2)))):
            threading.Thread(target=self.worker, daemon=True).start()

    def add(self, url, mode, quality, title="", format_selector="", format_label="Automatic", thumbnail="", est_size=0.0):
        with self.lock:
            self.counter += 1
            job = Job(
                self.counter, url, mode, quality, title=title,
                format_selector=format_selector, format_label=format_label,
                thumbnail=thumbnail, created_at=datetime.now().isoformat(timespec="seconds"),
                est_size=float(est_size or 0)
            )
            self.jobs.append(job)
        self.app.refresh_jobs()
        self.q.put(job)
        return job

    def cancel(self, job):
        if job.process and job.process.poll() is None:
            try:
                job.process.terminate()
            except Exception:
                pass
        job.status = "Cancelled"
        job.completed_at = datetime.now().isoformat(timespec="seconds")
        self.app.record_history(job)
        self.app.refresh_jobs()

    def worker(self):
        while True:
            job = self.q.get()
            try:
                self.run(job)
            except Exception as e:
                job.status = "Error"
                job.error = str(e)
                job.completed_at = datetime.now().isoformat(timespec="seconds")
                self.app.record_history(job)
                try:
                    self.app.root.after(0, lambda m=job.error: self.app.status_var.set("Download failed: " + m[:180]))
                except Exception:
                    pass
            finally:
                self.app.refresh_jobs()
                self.q.task_done()

    # When YouTube answers 403, retry with other player clients (some need no PO token) before giving up.
    ATTEMPTS = (
        ("default clients", [], True),
        ("TV / embedded clients", ["--extractor-args", "youtube:player_client=tv,web_embedded", "--http-chunk-size", "10M"], True),
        ("TV / embedded clients, signed out", ["--extractor-args", "youtube:player_client=tv,web_embedded", "--http-chunk-size", "10M"], False),
    )

    def attempt_order(self):
        """Start with the profile that worked last time, then try the others."""
        n = len(self.ATTEMPTS)
        try:
            pref = int(self.app.config.get("ytdlp_profile", 0))
        except (TypeError, ValueError):
            pref = 0
        pref = pref if 0 <= pref < n else 0
        return [pref] + [i for i in range(n) if i != pref]

    def remember_profile(self, index):
        if self.app.config.get("ytdlp_profile") != index:
            self.app.config["ytdlp_profile"] = index
            try:
                CONFIG.write_text(json.dumps(self.app.config, indent=2), encoding="utf-8")
            except Exception:
                pass

    def build_cmd(self, job, outdir, extra, use_cookies):
        cmd = yt_dlp_cmd() + [
            "--newline", "--progress", "--continue", "--no-overwrites", "--no-simulate",
            "--print", "after_move:__MEDIAFETCH_PATH__:%(filepath)s"
        ] + extra
        if use_cookies:
            cmd += cookie_args(self.app.config["browser_cookies"])
        if job.mode == "audio":
            cmd += ["-f", job.format_selector or "bestaudio/best", "-x", "--audio-format", "mp3", "--audio-quality", "0"]
        elif job.format_selector and job.format_selector != "auto":
            cmd += ["-f", f"{job.format_selector}+bestaudio/{job.format_selector}/best", "--merge-output-format", "mp4"]
        elif not re.match(r"\s*\d+", job.quality or ""):
            cmd += ["-f", "bv*+ba/b", "--merge-output-format", "mp4"]
        else:
            h = int(re.match(r"\s*(\d+)", job.quality).group(1))
            cmd += ["-f", f"bv*[height<={h}]+ba/b[height<={h}]/b[height<={h}]", "--merge-output-format", "mp4"]
        # The resolution suffix keeps e.g. the 1440p and 2160p downloads of one video from overwriting each other.
        cmd += ["-o", str(outdir / "%(title).180B [%(id)s]%(height& [{}p]|)s.%(ext)s"), job.url]
        return cmd

    def run_once(self, job, cmd, full):
        """Run one yt-dlp attempt. Returns (returncode, tail_lines)."""
        try:
            job.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=tool_env(), encoding="utf-8", errors="replace", creationflags=NO_WINDOW
            )
        except Exception as e:
            raise RuntimeError(f"Could not start yt-dlp: {e}")
        tail = []
        for line in job.process.stdout:
            if job.status == "Cancelled":
                break
            line = line.strip()
            if not line:
                continue
            tail.append(line)
            tail = tail[-30:]
            if len(full) < 800 and not (line.startswith("[download]") and "%" in line):
                full.append(line)
            if line.startswith("__MEDIAFETCH_PATH__:"):
                job.file_path = line.split(":", 1)[1]
                continue
            if "[download]" in line:
                m = re.search(r"(\d+(?:\.\d+)?)%.*?at\s+([^\s]+).*?ETA\s+([^\s]+)", line)
                if m:
                    job.progress = float(m.group(1))
                    job.speed = m.group(2)
                    job.eta = m.group(3)
                else:
                    m = re.search(r"(\d+(?:\.\d+)?)%", line)
                    if m:
                        job.progress = float(m.group(1))
            elif "Destination:" in line or "Merging" in line or "Extracting" in line:
                job.status = line.split("]", 1)[-1].strip()
            self.app.refresh_jobs()
        return job.process.wait(), tail

    def run(self, job):
        if job.status == "Cancelled":
            return
        job.status = "Starting"
        self.app.refresh_jobs()
        outdir = Path(self.app.config["download_dir"])
        outdir.mkdir(parents=True, exist_ok=True)
        started = time.time()
        full, tail, rc = [], [], 1
        order = self.attempt_order()
        used = order[0]
        for n, idx in enumerate(order):
            label, extra, use_cookies = self.ATTEMPTS[idx]
            used = idx
            job.status = "Downloading" if n == 0 else f"Retrying ({label})"
            job.progress, job.speed, job.eta = 0.0, "", ""
            self.app.refresh_jobs()
            cmd = self.build_cmd(job, outdir, extra, use_cookies)
            full.append(f"=== attempt {n + 1}: {label} ===")
            full.append(" ".join(cmd[:6]) + " ...")
            rc, tail = self.run_once(job, cmd, full)
            if job.status == "Cancelled":
                return
            blob = " ".join(tail).lower()
            if rc == 0 or not ("403" in blob or "forbidden" in blob):
                break
        if rc == 0:
            self.remember_profile(used)
        if rc != 0:
            for stale in outdir.glob("*.temp.*"):      # leftover half-merged files from this run
                try:
                    if stale.stat().st_mtime >= started - 2:
                        stale.unlink()
                except OSError:
                    pass
            errs = [l for l in tail if l.startswith("ERROR")]
            detail = clean_error("\n".join(errs or tail))
            low = detail.lower()
            if "ffmpeg" in low or "ffprobe" in low:
                detail += "  (FFmpeg problem - see Settings)"
            if "403" in low or "forbidden" in low:
                if not js_runtime():
                    detail += "  (No JavaScript runtime found)"
                detail += "  (YouTube refused every attempt. Update yt-dlp in Settings, refresh your cookies, or try again later)"
            try:
                logdir = ROOT / "logs"
                logdir.mkdir(exist_ok=True)
                logfile = logdir / f"download-{job.id}.log"
                logfile.write_text("\n".join(full), encoding="utf-8")
                job.log_path = str(logfile)
            except Exception:
                pass
            raise RuntimeError(detail)
        found = job.file_path if job.file_path and Path(job.file_path).is_file() else ""
        if not found:
            # Fallback: newest media file created in the download folder during this job.
            recent = [p for p in outdir.glob("*") if p.is_file() and p.stat().st_mtime >= started - 2
                      and p.suffix.lower() not in (".part", ".ytdl", ".temp")]
            if recent:
                found = str(max(recent, key=lambda p: p.stat().st_mtime))
        if not found:
            raise RuntimeError("yt-dlp finished but no file was saved. " + clean_error("\n".join(tail)))
        job.file_path = str(Path(found))
        try:
            job.actual_size = float(Path(found).stat().st_size)
        except OSError:
            pass
        job.progress = 100
        job.status = "Completed"
        job.speed = ""
        job.eta = ""
        job.completed_at = datetime.now().isoformat(timespec="seconds")
        self.app.record_history(job)
        try:
            self.app.root.after(0, lambda p=job.file_path: self.app.status_var.set(f"Saved: {p}"))
        except Exception:
            pass


class ApiHandler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{APP_VERSION}"

    def _headers(self, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-MediaFetch-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def _json(self, obj, status=200):
        self._headers(status)
        self.wfile.write(json.dumps(obj).encode())

    def _authorized(self):
        return self.headers.get("X-MediaFetch-Token", "") == self.server.app.config["api_token"]

    def do_OPTIONS(self):
        self._headers(204)

    def do_GET(self):
        if self.path == "/health":
            self._json({"ok": True, "app": APP_NAME, "version": APP_VERSION})
            return
        if not self._authorized():
            self._json({"error": "Unauthorized"}, 401)
            return
        if self.path == "/config":
            c = dict(self.server.app.config)
            self._json({"download_dir": c["download_dir"], "browser_cookies": c["browser_cookies"]})
            return
        self._json({"error": "Not found"}, 404)

    def do_POST(self):
        if not self._authorized():
            self._json({"error": "Unauthorized"}, 401)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._json({"error": "Invalid JSON"}, 400)
            return

        if self.path == "/analyze":
            url = str(data.get("url", "")).strip()
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in ("http", "https"):
                self._json({"error": "Only HTTP/HTTPS URLs are allowed"}, 400)
                return
            try:
                info = analyze_url(url, self.server.app.config["browser_cookies"])
                self._json({
                    "title": info.get("title", ""),
                    "webpage_url": info.get("webpage_url", url),
                    "duration": info.get("duration"),
                    "uploader": info.get("uploader"),
                    "thumbnail": info.get("thumbnail"),
                    "formats": build_formats(info),
                })
            except Exception as e:
                self._json({"error": str(e)}, 422)
            return

        if self.path == "/download":
            url = str(data.get("url", "")).strip()
            if urllib.parse.urlparse(url).scheme not in ("http", "https"):
                self._json({"error": "Only HTTP/HTTPS URLs are allowed"}, 400)
                return
            mode = data.get("mode", "video")
            quality = str(data.get("quality", "best"))
            title = str(data.get("title", "")).strip()
            fmt = str(data.get("format_selector", ""))
            fmt_label = str(data.get("format_label", "Automatic"))
            thumb = str(data.get("thumbnail", ""))
            job = self.server.app.manager.add(url, mode, quality, title, fmt, fmt_label, thumb)
            self._json({"id": job.id, "status": job.status})
            return

        self._json({"error": "Not found"}, 404)

    def log_message(self, fmt, *args):
        pass


class ApiServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, app):
        super().__init__((API_HOST, API_PORT), ApiHandler)
        self.app = app



def monitor_workarea(widget):
    """(left, top, right, bottom) of the monitor that contains the widget; falls back to the primary screen."""
    try:
        widget.update_idletasks()
        x = widget.winfo_rootx() + max(1, widget.winfo_width()) // 2
        y = widget.winfo_rooty() + max(1, widget.winfo_height()) // 2
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

            user32 = ctypes.windll.user32
            user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
            user32.MonitorFromPoint.restype = wintypes.HANDLE
            user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
            hmon = user32.MonitorFromPoint(wintypes.POINT(x, y), 2)  # MONITOR_DEFAULTTONEAREST
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(MONITORINFO)
            if hmon and user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                r = info.rcWork
                return r.left, r.top, r.right, r.bottom
    except Exception:
        pass
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


def compact_count(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            v = n / div
            return (f"{v:.0f}" if v >= 100 else f"{v:.1f}".rstrip("0").rstrip(".")) + suffix
    return str(int(n))


def quality_rank(text):
    t = (text or "").lower()
    m = re.match(r"\s*(\d+)", t)
    if m:
        return int(m.group(1))
    return 100000 if "best" in t else 0


def parse_rate(text):
    m = re.match(r"\s*([\d.]+)\s*([KMGT]?)i?B/s", text or "", re.I)
    if not m:
        return -1.0
    return float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12}[m.group(2).upper()]


def parse_eta(text):
    parts = re.findall(r"\d+", text or "")
    if not parts:
        return -1
    total = 0
    for p in parts:
        total = total * 60 + int(p)
    return total


def make_logo(size=256):
    """App mark: red rounded square, white play-triangle arrow dropping into a tray."""
    if Image is None:
        return None
    from PIL import ImageDraw
    k = 4
    S = size * k
    top, bot = (255, 72, 58), (196, 0, 12)
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / (S - 1)
        gd.line([(0, y), (S, y)], fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([S * 0.03, S * 0.03, S * 0.97, S * 0.97], radius=S * 0.24, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    cx = S / 2
    sw = S * 0.125
    d.rounded_rectangle([cx - sw / 2, S * 0.17, cx + sw / 2, S * 0.50], radius=sw * 0.45, fill=white)
    tri = [(cx - S * 0.205, S * 0.435), (cx + S * 0.205, S * 0.435), (cx, S * 0.665)]
    d.polygon(tri, fill=white)
    d.line(tri + [tri[0]], fill=white, width=int(S * 0.055), joint="curve")
    d.rounded_rectangle([S * 0.23, S * 0.755, S * 0.77, S * 0.825], radius=S * 0.035, fill=white)
    return img.resize((size, size), Image.LANCZOS)


def ensure_icon():
    """Write assets/app.ico (multi-size) and return its path, or None."""
    try:
        ASSETS.mkdir(exist_ok=True)
        ico = ASSETS / "app.ico"
        if not ico.exists() and Image is not None:
            make_logo(256).save(ico, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
        return ico if ico.exists() else None
    except Exception:
        return None


def style_title_bar(window, bg="#0f0f0f", fg="#f1f1f1", border="#0f0f0f"):
    """Windows 10/11: dark title bar tinted to the app background (Windows 11 also gets matching colors)."""
    if os.name != "nt":
        return
    try:
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        dwm = ctypes.windll.dwmapi

        def colorref(h):
            return int(h[1:3], 16) | (int(h[3:5], 16) << 8) | (int(h[5:7], 16) << 16)

        def put(attr, value):
            v = ctypes.c_int(value)
            return dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

        if put(20, 1) != 0:
            put(19, 1)
        put(35, colorref(bg))      # caption color (Windows 11)
        put(36, colorref(fg))      # caption text color
        put(34, colorref(border))  # window border color
    except Exception:
        pass


class Tooltip:
    """Small hover tooltip; text may be a string or a callable returning one (empty = no tooltip)."""

    def __init__(self, widget, text, delay=450):
        self.widget, self.text, self.delay = widget, text, delay
        self.tip = None
        self._job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._hide()
        self._job = self.widget.after(self.delay, self._show)

    def _show(self):
        self._job = None
        text = self.text() if callable(self.text) else self.text
        if not text or self.tip is not None:
            return
        try:
            tip = tk.Toplevel(self.widget)
            tip.overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except tk.TclError:
                pass
            tk.Label(tip, text=text, bg="#2b2b2b", fg="#f1f1f1", font=("Segoe UI", 9), padx=9, pady=4,
                     highlightthickness=1, highlightbackground="#4a4a4a").pack()
            tip.geometry(f"+{self.widget.winfo_pointerx() + 14}+{self.widget.winfo_pointery() + 20}")
            self.tip = tip
        except tk.TclError:
            self.tip = None

    def _hide(self, _e=None):
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        if self.tip is not None:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None


def enable_drag_select(tree, on_change=None):
    """Extended selection for a Treeview: Ctrl/Shift-click, Ctrl+A and click-drag to sweep rows."""
    state = {"anchor": None}

    def press(e):
        if tree.identify_region(e.x, e.y) == "cell" and not (e.state & 0x0005):
            state["anchor"] = tree.identify_row(e.y) or None
        else:
            state["anchor"] = None

    def drag(e):
        anchor = state["anchor"]
        if not anchor:
            return
        rows = tree.get_children()
        if e.y < 0:
            tree.yview_scroll(-1, "units")
        elif e.y > tree.winfo_height():
            tree.yview_scroll(1, "units")
        cur = tree.identify_row(max(0, min(e.y, tree.winfo_height() - 1)))
        if not cur or anchor not in rows or cur not in rows:
            return
        a, b = sorted((rows.index(anchor), rows.index(cur)))
        tree.selection_set(rows[a:b + 1])
        return "break"

    def select_all(_e=None):
        tree.selection_set(tree.get_children())
        return "break"

    tree.bind("<ButtonPress-1>", press, add="+")
    tree.bind("<B1-Motion>", drag, add="+")
    tree.bind("<Control-a>", select_all)
    tree.bind("<Control-A>", select_all)
    if on_change:
        tree.bind("<<TreeviewSelect>>", lambda e: on_change(), add="+")


class RoundedButton(tk.Canvas):
    """Small pill-shaped button that behaves enough like tk.Button for this app."""
    def __init__(self, parent, text, command, bg, fg, hover_bg, font, accent=False, **kwargs):
        self._text = text
        self._command = command
        self._bg = bg
        self._fg = fg
        self._hover_bg = hover_bg
        self._base = (bg, fg, hover_bg)
        self._disabled = False
        self._font = font
        self._accent = accent
        self._pad_x = kwargs.pop("padx", 14)
        self._pad_y = kwargs.pop("pady", 7)
        try:
            import tkinter.font as tkfont
            f = tkfont.Font(font=self._font)
            auto_w = f.measure(self._text) + (self._pad_x * 2)
            auto_h = f.metrics("linespace") + (self._pad_y * 2)
        except Exception:
            auto_w, auto_h = 90, 32
        kwargs.setdefault("width", max(44, auto_w))
        kwargs.setdefault("height", max(30, auto_h))
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0, bd=0, relief="flat", cursor="hand2", **kwargs)
        self.bind("<Configure>", self._draw)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<Button-1>", self._click)
        self._draw()

    def _draw(self, *_):
        self.delete("all")
        w = max(2, self.winfo_width())
        h = max(2, self.winfo_height())
        r = max(1, min(14, max(8, h // 2 - 1), w // 2 - 1, h // 2 - 1))
        if self._disabled:
            fill = "#232323"
        elif getattr(self, "_hovering", False):
            fill = self._hover_bg
        else:
            fill = self._bg
        img = self._pill(w, h, r, fill)
        if img is not None:
            self.create_image(0, 0, image=img, anchor="nw")
        else:
            self.create_rounded_rect(1, 1, w - 1, h - 1, r, fill=fill, outline="")
        fg = "#5c5c5c" if self._disabled else self._fg
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)

    def _pill(self, w, h, r, fill):
        """Smooth (anti-aliased) rounded rectangle rendered at 4x and scaled down; None if Pillow is missing."""
        if Image is None or ImageTk is None:
            return None
        cache = self.__dict__.setdefault("_pill_cache", {})
        key = (w, h, r, fill)
        if key not in cache:
            try:
                from PIL import ImageDraw
                k = 4
                mask = Image.new("L", (w * k, h * k), 0)
                ImageDraw.Draw(mask).rounded_rectangle([k, k, w * k - k - 1, h * k - k - 1], radius=r * k, fill=255)
                mask = mask.resize((w, h), Image.LANCZOS)
                base = Image.new("RGB", (w, h), self.cget("bg"))
                shape = Image.new("RGB", (w, h), fill)
                if len(cache) > 24:
                    cache.clear()
                cache[key] = ImageTk.PhotoImage(Image.composite(shape, base, mask))
            except Exception:
                return None
        self._pill_img = cache[key]
        return cache[key]

    def create_rounded_rect(self, x1, y1, x2, y2, r, **kw):
        # Arcs need start=/extent= keywords; positional values are treated as extra coordinates.
        d = 2 * r
        self.create_arc(x1, y1, x1 + d, y1 + d, start=90, extent=90, style="pieslice", **kw)
        self.create_arc(x2 - d, y1, x2, y1 + d, start=0, extent=90, style="pieslice", **kw)
        self.create_arc(x1, y2 - d, x1 + d, y2, start=180, extent=90, style="pieslice", **kw)
        self.create_arc(x2 - d, y2 - d, x2, y2, start=270, extent=90, style="pieslice", **kw)
        self.create_rectangle(x1 + r, y1, x2 - r, y2, **kw)
        self.create_rectangle(x1, y1 + r, x2, y2 - r, **kw)

    def _enter(self, *_):
        self._hovering = True
        if not self._disabled:
            self._draw()

    def _leave(self, *_):
        self._hovering = False
        if not self._disabled:
            self._draw()

    def _click(self, *_):
        if not self._disabled and self._command:
            self._command()

    def set_active(self, active):
        """Highlight as the selected nav item (light pill, dark text)."""
        if active:
            self._bg, self._fg, self._hover_bg = "#f1f1f1", "#0f0f0f", "#ffffff"
        else:
            self._bg, self._fg, self._hover_bg = self._base
        self._draw()

    def configure(self, cnf=None, **kw):
        state = kw.pop("state", None)
        if state is not None:
            self._disabled = state == "disabled"
            tk.Canvas.configure(self, cursor="arrow" if self._disabled else "hand2")
            self._draw()
        if "text" in kw:
            self._text = kw.pop("text")
            self._draw()
        if kw or cnf:
            return super().configure(cnf, **kw)
        return None

    config = configure

class Dropdown(tk.Frame):
    """Dark dropdown with a scrollable popup list and optional non-selectable section headers.
    Values are strings, or ("header", text) tuples for section titles."""

    def __init__(self, parent, colors, font, variable=None, values=None, label_width=14,
                 popup_width=None, max_rows=14, on_select=None):
        super().__init__(parent, bg=colors["field"], highlightthickness=1, bd=0,
                         highlightbackground=colors["border"], highlightcolor=colors["accent"])
        self._dd_colors = colors
        self._dd_font = font
        self._dd_var = variable if variable is not None else tk.StringVar()
        self._dd_items = []
        self._dd_popup = None
        self._dd_list = None
        self._dd_disabled = False
        self._dd_popup_width = popup_width
        self._dd_max_rows = max_rows
        self._dd_on_select = on_select
        self._dd_bind_ids = []
        self._dd_label = tk.Label(self, textvariable=self._dd_var, bg=colors["field"], fg=colors["text"],
                                  font=font, anchor="w", padx=10, pady=6, width=label_width, cursor="hand2")
        self._dd_label.pack(side="left", fill="both", expand=True)
        self._dd_arrow = tk.Label(self, text="\u25BE", bg=colors["field"], fg=colors["muted"],
                                  font=(font[0], 11), padx=8, cursor="hand2")
        self._dd_arrow.pack(side="right", fill="y")
        for w in (self, self._dd_label, self._dd_arrow):
            w.bind("<Button-1>", self._toggle)
            w.bind("<Enter>", lambda e: self._paint(self._dd_colors["field_hover"]))
            w.bind("<Leave>", lambda e: self._paint(self._dd_colors["field"]))
        self.set_values(values or [])

    # ---- public API (a small subset of ttk.Combobox) ----
    def set_values(self, values):
        items = []
        for v in values:
            if isinstance(v, tuple):
                if v[0] == "header":
                    items.append((v[1], True, ""))
                else:                                   # ("item", text, hint)
                    items.append((v[1], False, v[2] if len(v) > 2 else ""))
            else:
                items.append((str(v), False, ""))
        self._dd_items = items

    def get(self):
        return self._dd_var.get()

    def set(self, value):
        self._dd_var.set(value)

    def current(self, index):
        if 0 <= index < len(self._dd_items) and not self._dd_items[index][1]:
            self._dd_var.set(self._dd_items[index][0])

    def configure(self, cnf=None, **kw):
        if "values" in kw:
            self.set_values(kw.pop("values"))
        if "state" in kw:
            self._dd_disabled = kw.pop("state") == "disabled"
            fg = self._dd_colors["muted"] if self._dd_disabled else self._dd_colors["text"]
            self._dd_label.configure(fg=fg, cursor="arrow" if self._dd_disabled else "hand2")
            self._dd_arrow.configure(cursor="arrow" if self._dd_disabled else "hand2")
            self._close()
        if kw or cnf:
            return super().configure(cnf, **kw)
        return None

    config = configure

    # ---- internals ----
    def _paint(self, bg):
        if self._dd_disabled:
            bg = self._dd_colors["field"]
        for w in (self, self._dd_label, self._dd_arrow):
            tk.Frame.configure(w, bg=bg) if w is self else w.configure(bg=bg)

    def _toggle(self, _event=None):
        if self._dd_disabled:
            return "break"
        if self._dd_popup is not None:
            self._close()
        else:
            self._open()
        return "break"

    def _open(self):
        if not self._dd_items:
            return
        on_open = getattr(self, "_dd_on_open", None)
        if on_open:
            on_open()
        c = self._dd_colors
        top = tk.Toplevel(self)
        top.withdraw()
        top.overrideredirect(True)
        top.configure(bg=c["border"])
        try:
            top.attributes("-topmost", True)
        except tk.TclError:
            pass
        body = tk.Frame(top, bg=c["card2"])
        body.pack(fill="both", expand=True, padx=1, pady=1)
        rows = min(len(self._dd_items), self._dd_max_rows)
        lb = tk.Listbox(body, bg=c["card2"], fg=c["text"], font=self._dd_font, bd=0, highlightthickness=0,
                        relief="flat", activestyle="none", exportselection=False, height=rows, width=1,
                        selectbackground=c["accent"], selectforeground=c["white"])
        if len(self._dd_items) > rows:
            sb = ttk.Scrollbar(body, orient="vertical", command=lb.yview)
            lb.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
        lb.pack(side="left", fill="both", expand=True)
        current = self._dd_var.get()
        cur_idx = None
        for i, (text, header, hint) in enumerate(self._dd_items):
            lb.insert("end", text if header else "   " + text + (("     " + hint) if hint else ""))
            if header:
                lb.itemconfigure(i, background=c["card"], foreground=c["muted"],
                                 selectbackground=c["card"], selectforeground=c["muted"])
            elif text == current:
                cur_idx = i
        self._dd_popup, self._dd_list = top, lb
        if cur_idx is not None:
            lb.selection_set(cur_idx)
            lb.activate(cur_idx)
            lb.see(cur_idx)
        lb.bind("<Motion>", self._on_motion)
        lb.bind("<ButtonRelease-1>", self._on_release)
        lb.bind("<Return>", self._on_return)
        lb.bind("<Escape>", lambda e: self._close())
        lb.bind("<Up>", lambda e: self._move(-1))
        lb.bind("<Down>", lambda e: self._move(1))
        lb.bind("<FocusOut>", lambda e: self.after(80, self._check_focus))
        root = self.winfo_toplevel()
        self._dd_bind_ids = [
            ("<Button-1>", root.bind("<Button-1>", self._on_root_click, add="+")),
            ("<Configure>", root.bind("<Configure>", self._on_root_configure, add="+")),
        ]
        self.update_idletasks()
        top.update_idletasks()
        w = max(self.winfo_width(), self._dd_popup_width or 0)
        h = top.winfo_reqheight()
        x, y = self.winfo_rootx(), self.winfo_rooty() + self.winfo_height() - 1
        ml, mt, mr, mb = monitor_workarea(self)   # stay on the monitor the app is on
        if y + h > mb - 8:
            y = max(mt, self.winfo_rooty() - h + 1)
        if x + w > mr:
            x = mr - w - 4
        x = max(ml, x)
        top.geometry(f"{w}x{h}+{x}+{y}")
        top.deiconify()
        top.lift()
        lb.focus_force()

    def _close(self):
        if self._dd_popup is None:
            return
        root = self.winfo_toplevel()
        for seq, fid in self._dd_bind_ids:
            try:
                root.unbind(seq, fid)
            except tk.TclError:
                pass
        self._dd_bind_ids = []
        try:
            self._dd_popup.destroy()
        except tk.TclError:
            pass
        self._dd_popup = None
        self._dd_list = None

    @staticmethod
    def _inside(widget_path, root_path):
        return widget_path == root_path or widget_path.startswith(root_path + ".")

    def _on_root_click(self, event):
        if self._dd_popup is None:
            return
        w = str(event.widget)
        if self._inside(w, str(self._dd_popup)) or self._inside(w, str(self)):
            return
        self._close()

    def _on_root_configure(self, event):
        if self._dd_popup is not None and event.widget == self.winfo_toplevel():
            self._close()

    def _check_focus(self):
        if self._dd_popup is None:
            return
        try:
            f = self.focus_get()
        except Exception:
            f = None
        if f is None or not self._inside(str(f), str(self._dd_popup)):
            self._close()

    def _set_row(self, idx):
        lb = self._dd_list
        lb.selection_clear(0, "end")
        lb.selection_set(idx)
        lb.activate(idx)

    def _on_motion(self, event):
        lb = self._dd_list
        if lb is None:
            return
        idx = lb.nearest(event.y)
        if 0 <= idx < len(self._dd_items) and not self._dd_items[idx][1]:
            self._set_row(idx)

    def _move(self, delta):
        lb = self._dd_list
        if lb is None:
            return "break"
        n = len(self._dd_items)
        sel = lb.curselection()
        idx = sel[0] if sel else (-1 if delta > 0 else n)
        idx += delta
        while 0 <= idx < n and self._dd_items[idx][1]:
            idx += delta
        if 0 <= idx < n:
            self._set_row(idx)
            lb.see(idx)
        return "break"

    def _on_release(self, event):
        lb = self._dd_list
        if lb is None:
            return
        self._choose(lb.nearest(event.y))

    def _on_return(self, _event):
        lb = self._dd_list
        sel = lb.curselection() if lb is not None else ()
        if sel:
            self._choose(sel[0])
        return "break"

    def _choose(self, idx):
        if not (0 <= idx < len(self._dd_items)):
            return
        text, header, _hint = self._dd_items[idx]
        if header:
            return
        self._dd_var.set(text)
        self._close()
        if self._dd_on_select:
            self._dd_on_select(text)


class App:
    def __init__(self):
        self.config = load_config()
        self.history = load_history()
        global COOKIES_FILE
        COOKIES_FILE = self.config.get("cookies_file", "") or ""
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"KeshavKumar13.{APP_NAME}")
            except Exception:
                pass
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.media_url = ""
        self.logo_photos = []
        self.apply_window_icon(self.root)
        self.root.geometry("1240x760")
        self.root.minsize(980, 650)
        self.last_info = None
        self.last_formats = []
        self.last_thumbnail = None
        self.server = None
        self.status_var = tk.StringVar(value="Ready")
        self.sort_state = {"queue": (None, False), "history": (None, False)}
        self.page_var = tk.StringVar(value="Vydora Downloader")
        self.build_styles()
        self.manager = DownloadManager(self)
        self.updater = make_updater()
        self.updater.on_change = lambda u: self.after_main(self.refresh_update_ui)
        self._update_applied = False
        self._update_dismissed = ""
        self._settings_update_label = None
        self.build_ui()
        self.root.after(60, lambda: style_title_bar(self.root, self.colors["bg"], self.colors["text"], self.colors["bg"]))
        self.root.after(600, lambda: style_title_bar(self.root, self.colors["bg"], self.colors["text"], self.colors["bg"]))
        self.start_server()
        if self.updater.enabled and self.config.get("auto_update", True):
            self.root.after(2500, self.updater.start_background_check)   # check on every start, download quietly
        self.root.after(500, self.refresh_jobs)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    # ---------- updates ----------
    def after_main(self, fn):
        try:
            self.root.after(0, fn)
        except Exception:
            pass

    def build_update_bar(self, main):
        c = self.colors
        self.update_bar = tk.Frame(main, bg=c["card"], highlightthickness=1, highlightbackground=c["border"])
        tk.Label(self.update_bar, text="\u25CF", bg=c["card"], fg=c["accent"], font=(self.font_family, 10)).pack(side="left", padx=(12, 6), pady=9)
        self.update_text = tk.Label(self.update_bar, text="", bg=c["card"], fg=c["text"], font=(self.font_family, 9), anchor="w")
        self.update_text.pack(side="left", fill="x", expand=True)
        self.update_dismiss_btn = self.text_button(self.update_bar, "Dismiss", self.dismiss_update)
        self.update_dismiss_btn.pack(side="right", padx=(4, 8), pady=5)
        self.update_now_btn = self.text_button(self.update_bar, "Restart & update", self.restart_and_update, accent=True)
        self.update_now_btn.pack(side="right", padx=4, pady=5)

    def refresh_update_ui(self):
        u = self.updater
        try:
            if self._settings_update_label is not None and self._settings_update_label.winfo_exists():
                self._settings_update_label.config(text=self.update_status_text())
        except tk.TclError:
            self._settings_update_label = None
        try:
            show = u.state in ("downloading", "ready") and u.version != self._update_dismissed
            if not show:
                self.update_bar.pack_forget()
                return
            if u.state == "downloading":
                self.update_text.config(text=f"Version {u.version} is available - downloading in the background ({u.progress:.0f}%). You can keep working.")
                self.update_now_btn.pack_forget()
            else:
                self.update_text.config(text=f"Version {u.version} is ready. It installs automatically when you close {APP_NAME}.")
                self.update_now_btn.pack(side="right", padx=4, pady=5)
            if not self.update_bar.winfo_ismapped():
                self.update_bar.pack(fill="x", pady=(0, 8), before=self.pages)
        except tk.TclError:
            pass

    def update_status_text(self):
        u = self.updater
        if not u.enabled:
            return f"Version {APP_VERSION}  \u2022  automatic updates apply to the packaged app"
        detail = {"checking": "checking...", "downloading": f"downloading {u.version} ({u.progress:.0f}%)",
                  "ready": f"{u.version} ready - installs when you close the app", "uptodate": "you're up to date"}.get(u.state, u.message)
        return f"Version {APP_VERSION}  \u2022  {detail}" if detail else f"Version {APP_VERSION}"

    def dismiss_update(self):
        self._update_dismissed = self.updater.version
        self.update_bar.pack_forget()

    def restart_and_update(self):
        active = [j for j in self.manager.jobs if j.status not in self.FINISHED]
        if active and not messagebox.askyesno(APP_NAME, "Downloads are still running and will be cancelled. Restart and update now?"):
            return
        if self.updater.apply(relaunch=True):
            self._update_applied = True
            self.close()
        else:
            messagebox.showerror(APP_NAME, "Could not start the update. It will be retried the next time the app opens.")

    def check_updates_now(self):
        if not self.updater.enabled:
            self.refresh_update_ui()
            return
        self._update_dismissed = ""
        self.updater.start_background_check(manual=True)

    # ---------- sponsored ad ----------
    def build_ad_strip(self, parent):
        if not AD_URL:
            return
        c = self.colors

        # Keep the Smartlink present but visually secondary to the application UI.
        outer = tk.Frame(parent, bg=c["card"], highlightthickness=1,
                         highlightbackground="#303030", cursor="hand2")
        outer.pack(fill="x", pady=(0, 8))

        content = tk.Frame(outer, bg=c["card"], cursor="hand2")
        content.pack(fill="x", padx=10, pady=7)
        content.grid_columnconfigure(1, weight=1)

        # Neutral media/link icon instead of the bright Vydora-style logo block.
        icon = tk.Canvas(content, width=56, height=56, bg=c["card2"],
                         highlightthickness=1, highlightbackground="#353535",
                         bd=0, cursor="hand2")
        icon.grid(row=0, column=0, padx=(0, 12), sticky="w")
        icon.create_oval(16, 16, 40, 40, outline="#777777", width=1)
        icon.create_polygon(25, 22, 25, 34, 35, 28, fill="#888888", outline="")

        text_wrap = tk.Frame(content, bg=c["card"], cursor="hand2")
        text_wrap.grid(row=0, column=1, sticky="ew")
        tk.Label(text_wrap, text="Sponsored link", bg=c["card"], fg="#dddddd",
                 font=(self.font_family, 10, "bold"), cursor="hand2").pack(anchor="w")
        tk.Label(text_wrap, text="Partner link", bg=c["card"], fg="#888888",
                 font=(self.font_family, 8), cursor="hand2").pack(anchor="w", pady=(2, 0))

        action = tk.Label(content, text="Open link", bg=c["field_hover"], fg="#bdbdbd",
                          font=(self.font_family, 8, "bold"), cursor="hand2", padx=16, pady=8,
                          highlightthickness=1, highlightbackground="#3a3a3a")
        action.grid(row=0, column=2, padx=(12, 0), sticky="e")

        def open_ad(_e=None):
            webbrowser.open(AD_URL)

        widgets = [outer, content, icon, text_wrap, action]
        for w in widgets:
            w.bind("<Button-1>", open_ad)
        for child in text_wrap.winfo_children():
            child.bind("<Button-1>", open_ad)

        tk.Label(outer, text="ADVERTISEMENT", bg=c["card"], fg="#666666",
                 font=(self.font_family, 7, "bold")).pack(pady=(0, 6))

    def apply_window_icon(self, window):
        try:
            ico = ensure_icon()
            if ico and os.name == "nt":
                window.iconbitmap(default=str(ico))
                return
            logo = make_logo(64)
            if logo is not None and ImageTk is not None:
                photo = ImageTk.PhotoImage(logo)
                self.logo_photos.append(photo)
                window.iconphoto(True, photo)
        except Exception:
            pass

    def make_link(self, widget, get_url, tooltip, hover_fg=LINK_BLUE):
        """Turn a Label into a hyperlink: hand cursor, underline + color on hover, tooltip."""
        normal_fg = widget.cget("fg")
        base = tkfont.Font(font=widget.cget("font"))
        under = tkfont.Font(font=widget.cget("font"))
        under.configure(underline=True)
        widget._link_fonts = (base, under)
        Tooltip(widget, lambda: tooltip if get_url() else "")

        def enter(_e=None):
            if get_url():
                widget.configure(fg=hover_fg, font=under, cursor="hand2")

        def leave(_e=None):
            widget.configure(fg=normal_fg, font=base, cursor="arrow")

        def click(_e=None):
            url = get_url()
            if url:
                webbrowser.open(url)

        widget.bind("<Enter>", enter, add="+")
        widget.bind("<Leave>", leave, add="+")
        widget.bind("<Button-1>", click, add="+")

    def build_styles(self):
        # YouTube-inspired dark desktop theme: strong contrast, Roboto-like typography,
        # red accent, compact spacing, and simple text-first controls.
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        self.font_family = "Roboto"
        try:
            tkfont = __import__("tkinter.font", fromlist=["Font"])
            available = tkfont.families(self.root)
            if self.font_family not in available:
                self.font_family = "Segoe UI"
        except Exception:
            self.font_family = "Segoe UI"
        self.colors = {
            "bg": "#0f0f0f",
            "card": "#212121",
            "card2": "#181818",
            "text": "#f1f1f1",
            "muted": "#aaaaaa",
            "accent": "#ff0000",
            "accent_hover": "#cc0000",
            "border": "#3f3f3f",
            "field": "#121212",
            "field_hover": "#272727",
            "success": "#2ba640",
            "danger": "#ff4e45",
            "white": "#ffffff",
        }
        self.root.configure(bg=self.colors["bg"])
        base = (self.font_family, 10)
        style.configure(".", font=base, background=self.colors["bg"], foreground=self.colors["text"])
        style.configure("App.TFrame", background=self.colors["bg"])
        style.configure("Card.TFrame", background=self.colors["card"])
        style.configure("Header.TFrame", background=self.colors["bg"])
        style.configure("Title.TLabel", background=self.colors["bg"], foreground=self.colors["text"], font=(self.font_family, 21, "bold"))
        style.configure("Subtitle.TLabel", background=self.colors["bg"], foreground=self.colors["muted"], font=(self.font_family, 9))
        style.configure("CardTitle.TLabel", background=self.colors["card"], foreground=self.colors["text"], font=(self.font_family, 11, "bold"))
        style.configure("CardText.TLabel", background=self.colors["card"], foreground=self.colors["muted"], font=(self.font_family, 9))
        style.configure("Danger.TLabel", background=self.colors["card"], foreground=self.colors["danger"], font=(self.font_family, 9, "bold"))
        style.configure("Status.TLabel", background=self.colors["bg"], foreground=self.colors["muted"], font=(self.font_family, 8))
        style.configure("Watermark.TLabel", background=self.colors["bg"], foreground="#666666", font=(self.font_family, 8))
        style.configure("TEntry", fieldbackground=self.colors["field"], foreground=self.colors["text"], insertcolor=self.colors["text"], borderwidth=0, padding=8)
        style.configure("TCombobox", fieldbackground=self.colors["field"], background=self.colors["field"], foreground=self.colors["text"], arrowcolor=self.colors["text"], borderwidth=0, padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", self.colors["field"])], foreground=[("readonly", self.colors["text"])])
        style.configure("Treeview", rowheight=32, font=(self.font_family, 9), background=self.colors["card2"], fieldbackground=self.colors["card2"], foreground=self.colors["text"], borderwidth=0,
                        bordercolor=self.colors["card"], lightcolor=self.colors["card"], darkcolor=self.colors["card"])
        style.configure("Treeview.Heading", font=(self.font_family, 9, "bold"), background=self.colors["card"], foreground=self.colors["muted"], relief="flat", padding=(7, 7))
        style.configure("Treeview.Heading", bordercolor=self.colors["card"], lightcolor=self.colors["card"], darkcolor=self.colors["card"])
        style.map("Treeview.Heading",
                  background=[("pressed", "#2f2f2f"), ("active", "#292929")],
                  foreground=[("pressed", self.colors["text"]), ("active", self.colors["text"])],
                  relief=[("pressed", "flat"), ("active", "flat")])
        style.map("Treeview", background=[("selected", "#3a3a3a")], foreground=[("selected", self.colors["text"])])
        self.root.option_add("*TCombobox*Listbox.background", self.colors["card2"])
        self.root.option_add("*TCombobox*Listbox.foreground", self.colors["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", self.colors["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", self.colors["white"])
        self.root.option_add("*TCombobox*Listbox.font", (self.font_family, 9))
        for orient in ("Vertical", "Horizontal"):
            style.configure(f"{orient}.TScrollbar", background="#3d3d3d", troughcolor="#121212",
                            bordercolor="#121212", lightcolor="#3d3d3d", darkcolor="#3d3d3d",
                            arrowcolor="#aaaaaa", gripcount=0, relief="flat", arrowsize=14)
            style.map(f"{orient}.TScrollbar", background=[("active", "#5a5a5a"), ("pressed", "#707070")])
        style.configure("TScrollbar", background=self.colors["card"], troughcolor=self.colors["bg"], bordercolor=self.colors["bg"], arrowcolor=self.colors["muted"])

    def text_button(self, parent, text, command, accent=False, width=None, padx=14, pady=7, size=9):
        bg = self.colors["accent"] if accent else self.colors["card2"]
        fg = self.colors["white"] if accent else self.colors["text"]
        active = self.colors["accent_hover"] if accent else self.colors["field_hover"]
        btn = RoundedButton(parent, text, command, bg, fg, active,
                            font=(self.font_family, size, "bold" if accent else "normal"),
                            accent=accent, padx=padx, pady=pady)
        if width:
            btn.configure(width=width)
        return btn

    def card(self, parent, padding=12):
        return tk.Frame(parent, bg=self.colors["card"], highlightbackground=self.colors["card"], highlightthickness=0)
    def build_ui(self):
        main = tk.Frame(self.root, bg=self.colors["bg"])
        main.pack(fill="both", expand=True, padx=18, pady=(10, 5))

        header = tk.Frame(main, bg=self.colors["bg"], height=52)
        header.pack(fill="x")
        header.pack_propagate(False)
        brand = tk.Frame(header, bg=self.colors["bg"])
        brand.pack(side="left", fill="y")
        # The title bar already shows the logo and app name, so the header just shows the current page.
        tk.Label(brand, textvariable=self.page_var, bg=self.colors["bg"], fg=self.colors["text"],
                 font=(self.font_family, 18, "bold")).pack(side="left", pady=6, padx=(4, 0))

        nav = tk.Frame(header, bg=self.colors["bg"])
        nav.pack(side="right", pady=7)
        self.nav_downloader = self.text_button(nav, "Downloader", lambda: self.show_page("Downloader"))
        self.nav_downloader.pack(side="left", padx=3)
        self.nav_history = self.text_button(nav, "History", lambda: self.show_page("History"))
        self.nav_history.pack(side="left", padx=3)
        self.text_button(nav, "Settings", self.settings).pack(side="left", padx=(3, 0))

        self.pages = tk.Frame(main, bg=self.colors["bg"])
        self.pages.pack(fill="both", expand=True)
        self.build_update_bar(main)
        self.build_downloader_page()
        self.build_history_page()
        self.show_page("Downloader")

        footer = tk.Frame(main, bg=self.colors["bg"], height=24)
        footer.pack(fill="x", pady=(4, 0))
        footer.pack_propagate(False)
        tk.Label(footer, textvariable=self.status_var, bg=self.colors["bg"], fg=self.colors["muted"], font=(self.font_family, 8)).pack(side="left")
        tk.Label(footer, text=f"Local API • {API_HOST}:{API_PORT}", bg=self.colors["bg"], fg="#555555", font=(self.font_family, 8)).pack(side="left", padx=15)
        wm = tk.Label(footer, text="●  " + USER_WATERMARK, bg=self.colors["bg"], fg="#b3b3b3", font=(self.font_family, 9, "bold"))
        wm.pack(side="right", padx=(0, 6))
        self.make_link(wm, lambda: GITHUB_URL, "Open GitHub profile")

    def build_downloader_page(self):
        self.downloader_page = tk.Frame(self.pages, bg=self.colors["bg"])

        # Compact URL/search bar, intentionally much shorter than v1.2.x.
        url_card = self.card(self.downloader_page)
        url_card.pack(fill="x", pady=(0, 8))
        tk.Label(url_card, text="Video URL", bg=self.colors["card"], fg=self.colors["muted"], font=(self.font_family, 10, "bold")).pack(side="left", padx=(16, 10), pady=16)
        self.url = tk.StringVar()
        self.url_entry = tk.Entry(url_card, textvariable=self.url, bg=self.colors["field"], fg=self.colors["text"], insertbackground=self.colors["text"], relief="flat", bd=0, font=(self.font_family, 12), highlightthickness=1, highlightbackground=self.colors["border"], highlightcolor=self.colors["accent"])
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(0, 10), pady=12, ipady=10)
        self.url_entry.bind("<Return>", lambda e: self.analyze())
        self.enable_clipboard(self.url_entry)
        self.analyze_btn = self.text_button(url_card, "Analyze", self.analyze, accent=True, padx=26, pady=11, size=10)
        self.analyze_btn.pack(side="left", padx=(0, 12), pady=10)

        media_card = self.card(self.downloader_page)
        media_card.pack(fill="x", pady=(0, 8))
        self.thumb_frame = tk.Frame(media_card, bg=self.colors["card"], width=200, height=112)
        self.thumb_frame.pack(side="left", padx=(10, 12), pady=10)
        self.thumb_frame.pack_propagate(False)
        self.thumb_label = tk.Label(self.thumb_frame, text="Thumbnail", bg="#181818", fg="#777777", font=(self.font_family, 8), anchor="center")
        self.thumb_label.pack(fill="both", expand=True)
        media_body = tk.Frame(media_card, bg=self.colors["card"])
        media_body.pack(side="left", fill="both", expand=True, pady=9)
        top = tk.Frame(media_body, bg=self.colors["card"])
        top.pack(fill="x")
        tk.Label(top, text="MEDIA", bg=self.colors["card"], fg=self.colors["muted"], font=(self.font_family, 8, "bold")).pack(side="left")
        self.media_badge = tk.Label(top, text="Not analyzed", bg=self.colors["card"], fg=self.colors["muted"], font=(self.font_family, 8, "bold"))
        self.media_badge.pack(side="right", padx=12)
        self.info_title = tk.Label(media_body, text="Paste a URL above and click Analyze", bg=self.colors["card"], fg=self.colors["text"], font=(self.font_family, 12, "bold"), anchor="w", justify="left", wraplength=900)
        self.info_title.pack(fill="x", pady=(5, 2))
        self.make_link(self.info_title, lambda: self.media_url, "Open this video in your browser")
        self.make_link(self.thumb_label, lambda: self.media_url, "Open this video in your browser", hover_fg="#ffffff")
        self.info_meta = tk.Label(media_body, text="", bg=self.colors["card"], fg=self.colors["muted"], font=(self.font_family, 9), anchor="w")
        self.info_meta.pack(fill="x")
        self.info_error = tk.Label(media_body, text="", bg=self.colors["card"], fg=self.colors["danger"], font=(self.font_family, 9, "bold"), anchor="w", justify="left", wraplength=820)
        self.info_error.pack(fill="x", pady=(4, 0))
        self.retry_browser_btn = self.text_button(media_body, "Retry with browser session", self.retry_with_browser, accent=False)

        download_card = self.card(self.downloader_page)
        download_card.pack(fill="x", pady=(0, 8))
        row = tk.Frame(download_card, bg=self.colors["card"])
        row.pack(fill="x", padx=10, pady=8)
        tk.Label(row, text="DOWNLOAD", bg=self.colors["card"], fg=self.colors["muted"], font=(self.font_family, 8, "bold")).pack(side="left", padx=(0, 10))
        self.mode = tk.StringVar(value="video")
        tk.Radiobutton(row, text="Video", variable=self.mode, value="video", command=self.mode_changed, bg=self.colors["card"], fg=self.colors["text"], selectcolor=self.colors["field"], activebackground=self.colors["card"], activeforeground=self.colors["text"], font=(self.font_family, 9)).pack(side="left")
        tk.Radiobutton(row, text="MP3", variable=self.mode, value="audio", command=self.mode_changed, bg=self.colors["card"], fg=self.colors["text"], selectcolor=self.colors["field"], activebackground=self.colors["card"], activeforeground=self.colors["text"], font=(self.font_family, 9)).pack(side="left", padx=(4, 12))
        tk.Label(row, text="Quality", bg=self.colors["card"], fg=self.colors["muted"], font=(self.font_family, 9)).pack(side="left")
        self.quality = tk.StringVar(value=QUALITY_BEST)
        self.quality_box = Dropdown(row, self.colors, (self.font_family, 9), variable=self.quality,
                                    values=[QUALITY_BEST], label_width=18, popup_width=250,
                                    on_select=lambda v: self.quality_changed())
        self.quality_box.pack(side="left", padx=(5, 10))
        quality_tips = [Tooltip(w, self.sponsor_tooltip) for w in (self.quality_box, self.quality_box._dd_label, self.quality_box._dd_arrow)]
        self.quality_box._dd_on_open = lambda: [t._hide() for t in quality_tips]   # no tooltip over the open list
        tk.Label(row, text="Estimated size", bg=self.colors["card"], fg=self.colors["muted"], font=(self.font_family, 9)).pack(side="left", padx=(6, 6))
        self.size_label = tk.Label(row, text="—", bg=self.colors["card"], fg=self.colors["text"], font=(self.font_family, 10, "bold"))
        self.size_label.pack(side="left")
        Tooltip(self.size_label, "Approximate size of the final file. The real size can differ slightly.")
        self.sponsor_hint = tk.Label(row, text="", bg=self.colors["card"], fg="#d9a441", font=(self.font_family, 8))
        self.sponsor_hint.pack(side="left", padx=(14, 0))
        Tooltip(self.sponsor_hint, self.sponsor_tooltip)
        self.root.after(30000, self._sponsor_tick)
        self.text_button(row, "Open Folder", self.open_download_folder).pack(side="right", padx=(5, 0))
        self.add_btn = self.text_button(row, "Download", self.add_download, accent=True)
        self.add_btn.pack(side="right", padx=(5, 0))


        queue_card = self.card(self.downloader_page)
        queue_card.pack(fill="both", expand=True)
        toolbar = tk.Frame(queue_card, bg=self.colors["card"], height=38)
        toolbar.pack(fill="x", padx=10, pady=(7, 3))
        toolbar.pack_propagate(False)
        tk.Label(toolbar, text="DOWNLOAD QUEUE", bg=self.colors["card"], fg=self.colors["text"], font=(self.font_family, 10, "bold")).pack(side="left")
        self.queue_count = tk.Label(toolbar, text="0 items", bg=self.colors["card"], fg=self.colors["muted"], font=(self.font_family, 8))
        self.queue_count.pack(side="left", padx=(8, 0))
        self.btn_clear_done = self.text_button(toolbar, "Clear Completed", self.clear_completed)
        self.btn_clear_done.pack(side="right", padx=(5, 0))
        self.btn_remove = self.text_button(toolbar, "Remove", self.remove_selected)
        self.btn_remove.pack(side="right", padx=(5, 0))
        self.btn_cancel = self.text_button(toolbar, "Cancel", self.cancel_selected)
        self.btn_cancel.pack(side="right", padx=(5, 0))
        self.btn_show = self.text_button(toolbar, "Show File", self.open_selected_job)
        self.btn_show.pack(side="right", padx=(5, 0))
        table_wrap = tk.Frame(queue_card, bg=self.colors["card"])
        table_wrap.pack(fill="both", expand=True, padx=9, pady=(0, 9))
        cols = ("id", "title", "format", "size", "status", "progress", "speed", "eta")
        self.tree = ttk.Treeview(table_wrap, columns=cols, show="headings", selectmode="extended")
        headings = {"id":"#", "title":"Title", "format":"Quality", "size":"Size", "status":"Status", "progress":"Progress", "speed":"Speed", "eta":"ETA"}
        for c in cols:
            self.tree.heading(c, text=headings[c], anchor="w")
            self.tree.column(c, anchor="w", stretch=False)
        for c,w,mn in (("id",40,35),("title",300,180),("format",130,110),("size",95,80),("status",160,120),("progress",85,75),("speed",100,75),("eta",70,65)):
            self.tree.column(c, width=w, minwidth=mn)
        self.tree.column("title", stretch=True)
        table_wrap.grid_rowconfigure(0, weight=1)
        table_wrap.grid_columnconfigure(0, weight=1)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        sb.grid(row=0, column=1, sticky="ns")
        hsb = ttk.Scrollbar(table_wrap, orient="horizontal", command=self.tree.xview)
        hsb.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=sb.set, xscrollcommand=hsb.set)
        self.tree.bind("<Double-1>", self.queue_double_click)
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.tree.bind("<Button-3>", self.queue_menu)
        self.queue_headings = dict(headings)
        self.bind_heading_sort(self.tree, lambda col: self.sort_queue(col))
        enable_drag_select(self.tree, self.update_queue_buttons)
        self.tree.tag_configure("completed", foreground=self.colors["success"])
        self.tree.tag_configure("error", foreground=self.colors["danger"])
        self.tree.tag_configure("cancelled", foreground="#b084ff")
        self.tree.tag_configure("active", foreground=self.colors["text"])
        self.update_queue_buttons()

    def build_history_page(self):
        self.history_page = tk.Frame(self.pages, bg=self.colors["bg"])
        card = self.card(self.history_page)
        card.pack(fill="both", expand=True)
        toolbar = tk.Frame(card, bg=self.colors["card"], height=44)
        toolbar.pack(fill="x", padx=10, pady=8)
        toolbar.pack_propagate(False)
        tk.Label(toolbar, text="DOWNLOAD HISTORY", bg=self.colors["card"], fg=self.colors["text"], font=(self.font_family, 11, "bold")).pack(side="left")
        self.history_count = tk.Label(toolbar, text="0 downloads", bg=self.colors["card"], fg=self.colors["muted"], font=(self.font_family, 8))
        self.history_count.pack(side="left", padx=(10, 0))
        self.hbtn_clear_all = self.text_button(toolbar, "Clear All", self.clear_history)
        self.hbtn_clear_all.pack(side="right", padx=(5, 0))
        self.hbtn_remove = self.text_button(toolbar, "Remove", self.remove_history_selected)
        self.hbtn_remove.pack(side="right", padx=(5, 0))
        self.hbtn_folder = self.text_button(toolbar, "Show File", self.show_history_file)
        self.hbtn_folder.pack(side="right", padx=(5, 0))
        self.hbtn_open = self.text_button(toolbar, "Open File", self.open_history_file)
        self.hbtn_open.pack(side="right", padx=(5, 0))
        wrap = tk.Frame(card, bg=self.colors["card"])
        wrap.pack(fill="both", expand=True, padx=9, pady=(0,9))
        cols = ("num", "date", "title", "status", "format", "size", "path")
        self.history_tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="extended")
        heads = {"num":"#", "date":"Date", "title":"Title", "status":"Status", "format":"Quality", "size":"Size", "path":"File"}
        for c in cols:
            self.history_tree.heading(c, text=heads[c], anchor="w")
            self.history_tree.column(c, anchor="w", stretch=False)
        for c,w,mn in (("num",45,40),("date",135,125),("title",300,180),("status",100,90),("format",130,110),("size",90,80),("path",350,180)):
            self.history_tree.column(c, width=w, minwidth=mn)
        self.history_tree.column("title", stretch=True)
        self.history_tree.column("path", stretch=True)
        wrap.grid_rowconfigure(0, weight=1)
        wrap.grid_columnconfigure(0, weight=1)
        self.history_tree.grid(row=0, column=0, sticky="nsew")
        hs = ttk.Scrollbar(wrap, orient="vertical", command=self.history_tree.yview)
        hs.grid(row=0, column=1, sticky="ns")
        hsx = ttk.Scrollbar(wrap, orient="horizontal", command=self.history_tree.xview)
        hsx.grid(row=1, column=0, sticky="ew")
        self.history_tree.configure(yscrollcommand=hs.set, xscrollcommand=hsx.set)
        self.history_tree.tag_configure("Completed", foreground=self.colors["success"])
        self.history_tree.tag_configure("Error", foreground=self.colors["danger"])
        self.history_tree.tag_configure("Cancelled", foreground="#b084ff")
        self.history_headings = dict(heads)
        self.bind_heading_sort(self.history_tree, lambda col: self.sort_history(col))
        self.history_tree.bind("<Button-3>", self.history_menu)
        self.history_tree.bind("<Delete>", lambda e: self.remove_history_selected())
        enable_drag_select(self.history_tree, self.update_history_buttons)
        self.refresh_history()

        # Keep the History ad at the bottom of the page, below the history table.
        self.build_ad_strip(self.history_page)

    def show_page(self, name):
        for page in (self.downloader_page, self.history_page):
            page.pack_forget()
        page = self.downloader_page if name == "Downloader" else self.history_page
        page.pack(fill="both", expand=True)
        self.page_var.set(f"{APP_NAME} {name}")
        self.nav_downloader.set_active(name == "Downloader")
        self.nav_history.set_active(name == "History")
        if name == "History":
            self.refresh_history()

    def retry_with_browser(self):
        url = self.url.get().strip()
        if not url:
            return
        # Respect the user's explicit browser choice; this button only changes the
        # session source for this analysis attempt and does not persist cookies.
        preferred = self.config.get("browser_cookies", "none")
        if getattr(self.retry_browser_btn, "_text", "") == "Retry with Firefox" and self.browser_exists("firefox"):
            preferred = "firefox"
        elif preferred == "none":
            preferred = self.detect_browser()
        if preferred == "none" and not (COOKIES_FILE and Path(COOKIES_FILE).is_file()):
            messagebox.showinfo(APP_NAME, "Select a browser under Settings → Browser session, then retry. This is only for content you are authorized to access.")
            return
        self.analyze_btn.configure(state="disabled")
        self.info_title.config(text="Retrying with browser session…")
        self.info_error.config(text="")
        self.retry_browser_btn.pack_forget()
        self.media_badge.config(text="Analyzing…")
        threading.Thread(target=self._analyze_with_cookies, args=(url, preferred), daemon=True).start()

    def browser_exists(self, browser):
        local = os.environ.get("LOCALAPPDATA", "")
        appdata = os.environ.get("APPDATA", "")
        paths = {
            "chrome": [Path(local) / "Google" / "Chrome" / "User Data"],
            "edge": [Path(local) / "Microsoft" / "Edge" / "User Data"],
            "brave": [Path(local) / "BraveSoftware" / "Brave-Browser" / "User Data"],
            "firefox": [Path(appdata) / "Mozilla" / "Firefox" / "Profiles"],
        }
        return any(p.exists() for p in paths.get(browser, []))

    def detect_browser(self):
        candidates = []
        local = os.environ.get("LOCALAPPDATA", "")
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidates += [("firefox", Path(appdata) / "Mozilla" / "Firefox" / "Profiles")]
        if local:
            candidates += [("chrome", Path(local) / "Google" / "Chrome" / "User Data"), ("edge", Path(local) / "Microsoft" / "Edge" / "User Data"), ("brave", Path(local) / "BraveSoftware" / "Brave-Browser" / "User Data")]
        if appdata:
            candidates += [("firefox", Path(appdata) / "Mozilla" / "Firefox" / "Profiles")]
        for name, path in candidates:
            if path.exists():
                return name
        return "none"

    def _analyze_with_cookies(self, url, browser):
        try:
            info = analyze_url(url, browser)
            self.last_info = info
            self.last_formats = build_formats(info)
            title = info.get("title", "Untitled")
            duration = format_duration(info.get("duration"))
            uploader = info.get("uploader") or "Unknown creator"
            thumb_url = info.get("thumbnail") or ""
            thumb_path = self.fetch_thumbnail(thumb_url, info.get("id", "thumb")) if thumb_url else ""
            self.media_url = info.get("webpage_url") or url
            self.root.after(0, self.populate_format_controls)
            self.root.after(0, lambda: self.info_title.config(text=title))
            self.root.after(0, lambda: self.info_meta.config(text=self.meta_text(info, browser)))
            self.root.after(0, lambda: self.media_badge.config(text="Ready"))
            self.root.after(0, lambda: self.info_error.config(text=""))
            self.root.after(0, lambda: self.retry_browser_btn.pack_forget())
            self.root.after(0, lambda: self.analyze_btn.configure(state="normal"))
            self.root.after(0, lambda: self.status_var.set("Media analyzed successfully"))
            self.start_dislike_fetch(info, browser)
            if thumb_path:
                self.root.after(0, lambda p=thumb_path: self.show_thumbnail(p))
        except Exception as e:
            msg = str(e)
            self.media_url = ""
            self.root.after(0, lambda: self.thumb_label.config(image="", text="Thumbnail"))
            self.root.after(0, lambda: self.info_title.config(text="Unable to analyze this URL"))
            self.root.after(0, lambda: self.info_error.config(text=msg))
            self.root.after(0, lambda: self.media_badge.config(text="Error"))
            self.root.after(0, lambda: self.analyze_btn.configure(state="normal"))
            low = msg.lower()
            auth_error = any(x in low for x in ("sign in", "not a bot", "cookies", "login required", "captcha"))
            dpapi_error = "dpapi" in low or "failed to decrypt" in low
            def show_retry():
                if dpapi_error and self.browser_exists("firefox"):
                    self.retry_browser_btn._text = "Retry with Firefox"
                else:
                    self.retry_browser_btn._text = "Retry with browser session"
                self.retry_browser_btn.pack(anchor="w", pady=(6,0)) if (auth_error or dpapi_error) else None
            self.root.after(0, show_retry)
            if dpapi_error:
                self.root.after(0, lambda: self.info_error.config(text="Chrome/Chromium cookie decryption failed on Windows (DPAPI). This is a browser encryption limitation. Firefox is the recommended session source for yt-dlp; sign in to YouTube in Firefox, select Firefox in Settings, then retry."))
            self.root.after(0, lambda: self.status_var.set("Analysis failed"))

    def meta_text(self, info, browser=None, ryd=None):
        parts = [info.get("uploader") or "Unknown creator", format_duration(info.get("duration")) or "Duration unknown"]
        likes = info.get("like_count")
        if likes is None and ryd:
            likes = ryd.get("likes")
        if likes is not None:
            parts.append(f"Likes {compact_count(likes)}")
        dislikes = info.get("dislike_count")
        estimated = False
        if dislikes is None and ryd and ryd.get("dislikes") is not None:
            dislikes, estimated = ryd["dislikes"], True
        if dislikes is not None:
            parts.append(f"Dislikes {compact_count(dislikes)}" + (" (est.)" if estimated else ""))
        if browser:
            parts.append(f"{browser} session")
        return "   •   ".join(parts)

    def start_dislike_fetch(self, info, browser=None):
        """YouTube hides dislike counts; optionally fetch the community estimate (Return YouTube Dislike)."""
        if not self.config.get("show_dislikes", True) or info.get("dislike_count") is not None:
            return
        vid = info.get("id")
        if str(info.get("extractor_key", "")).lower() != "youtube" or not vid:
            return

        def work():
            try:
                req = urllib.request.Request(
                    "https://returnyoutubedislikeapi.com/votes?videoId=" + urllib.parse.quote(str(vid)),
                    headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
                with urllib.request.urlopen(req, timeout=6) as r:
                    data = json.loads(r.read().decode("utf-8", "replace"))
                if data.get("dislikes") is None:
                    return
            except Exception:
                return

            def apply():
                if self.last_info is info:
                    self.info_meta.config(text=self.meta_text(info, browser, data))
            try:
                self.root.after(0, apply)
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def enable_clipboard(self, entry):
        """Ctrl+V / Shift+Insert / Ctrl+A and a right-click Cut/Copy/Paste menu for a text entry."""
        c = self.colors
        menu = tk.Menu(entry, tearoff=0, bg=c["card2"], fg=c["text"], activebackground=c["accent"],
                       activeforeground=c["white"], bd=0, relief="flat")

        def paste(_e=None):
            try:
                text = entry.clipboard_get()
            except tk.TclError:
                return "break"
            text = " ".join(text.split())
            try:
                if entry.selection_present():
                    entry.delete("sel.first", "sel.last")
            except tk.TclError:
                pass
            entry.insert("insert", text)
            return "break"

        def copy(_e=None):
            try:
                text = entry.selection_get()
            except tk.TclError:
                return "break"
            entry.clipboard_clear()
            entry.clipboard_append(text)
            return "break"

        def cut(_e=None):
            copy()
            try:
                entry.delete("sel.first", "sel.last")
            except tk.TclError:
                pass
            return "break"

        def select_all(_e=None):
            entry.select_range(0, "end")
            entry.icursor("end")
            return "break"

        def clear():
            entry.delete(0, "end")

        menu.add_command(label="Cut", command=cut)
        menu.add_command(label="Copy", command=copy)
        menu.add_command(label="Paste", command=paste)
        menu.add_separator()
        menu.add_command(label="Select all", command=select_all)
        menu.add_command(label="Clear", command=clear)

        def popup(event):
            entry.focus_set()
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
            return "break"

        for seq in ("<Control-v>", "<Control-V>", "<Shift-Insert>"):
            entry.bind(seq, paste)
        for seq in ("<Control-a>", "<Control-A>"):
            entry.bind(seq, select_all)
        entry.bind("<Button-3>", popup)

    def paste_url(self):
        try:
            value = self.root.clipboard_get().strip()
            if value:
                self.url.set(value)
                self.url_entry.focus_set()
        except tk.TclError:
            pass

    def mode_changed(self):
        self.quality_box.configure(state="disabled" if self.mode.get() == "audio" else "normal")
        self.populate_format_controls()

    def quality_changed(self, _value=None):
        self.update_estimate()
        self.refresh_sponsor_ui()

    # ---------- sponsored top quality ----------
    def top_height(self):
        heights = [f["height"] for f in self.last_formats if f["has_video"] and f.get("height")]
        return max(heights) if heights else 0

    def is_top_quality(self):
        """True when the selected video quality is the best this video offers."""
        if self.mode.get() != "video":
            return False
        m = re.match(r"\s*(\d+)", self.quality.get() or "")
        if not m:
            return True                      # "Best available"
        top = self.top_height()
        return bool(top) and int(m.group(1)) >= top

    def sponsor_due(self):
        if not AD_URL:
            return False
        last = float(self.config.get("last_sponsor_open", 0) or 0)
        now = time.time()
        return last > now or now - last >= SPONSOR_COOLDOWN_MIN * 60

    def sponsor_tooltip(self):
        if not AD_URL or self.mode.get() != "video":
            return ""
        mins = SPONSOR_COOLDOWN_MIN
        if self.is_top_quality():
            if self.sponsor_due():
                return ("Top quality is supported by a sponsor.\n"
                        "When you click Download, a sponsored page opens in your browser\n"
                        "and your download starts as normal.\n"
                        f"It opens at most once every {mins} minutes. Choose a lower quality to skip it.")
            return ("Top quality is supported by a sponsor, but a sponsored page was opened\n"
                    f"recently, so none will open now (at most once every {mins} minutes).")
        return ("Lower qualities never open a sponsored page.\n"
                f"Top quality (marked 'sponsored') opens one at most once every {mins} minutes.")

    def refresh_sponsor_ui(self):
        try:
            if not AD_URL or not self.is_top_quality():
                text = ""
            elif self.sponsor_due():
                text = "Sponsored \u00b7 opens an ad page"
            else:
                text = "Top quality \u00b7 ad page shown recently"
            self.sponsor_hint.config(text=text)
        except (tk.TclError, AttributeError):
            pass

    def _sponsor_tick(self):
        self.refresh_sponsor_ui()
        try:
            self.root.after(30000, self._sponsor_tick)
        except tk.TclError:
            pass

    def open_sponsor_page(self):
        self.config["last_sponsor_open"] = time.time()
        try:
            CONFIG.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
        except Exception:
            pass
        try:
            webbrowser.open(AD_URL, new=2)
            self.status_var.set(f"Sponsored page opened - thanks for supporting {APP_NAME}")
        except Exception:
            pass
        self.refresh_sponsor_ui()

    @staticmethod
    def _codec_rank(f):
        return {"AV1": 5, "VP9": 4, "HEVC": 3, "H.264": 2, "VP8": 1}.get(short_vcodec(f.get("vcodec")), 0)

    def estimate_size(self):
        """Approximate bytes for the current mode/quality, or None when it cannot be known."""
        formats = self.last_formats
        if not formats:
            return None
        dur = (self.last_info or {}).get("duration") or 0

        def fsize(f):
            if f.get("size"):
                return float(f["size"])
            if f.get("tbr") and dur:
                return f["tbr"] * 1000 / 8 * dur
            return None

        if self.mode.get() == "audio":
            if dur:
                return dur * 245 * 1000 / 8      # MP3 VBR quality 0 averages roughly 245 kbps
            audio = [f for f in formats if f["has_audio"]]
            return fsize(max(audio, key=lambda f: f.get("tbr") or 0)) if audio else None
        audio = [f for f in formats if f["has_audio"] and not f["has_video"]]
        best_audio = max(audio, key=lambda f: (f.get("tbr") or 0), default=None)
        a_size = (fsize(best_audio) if best_audio else 0) or 0
        videos = [f for f in formats if f["has_video"] and "premium" not in str(f.get("note", "")).lower()]
        m = re.match(r"\s*(\d+)", self.quality.get())
        if m:
            h = int(m.group(1))
            videos = [f for f in videos if (f.get("height") or 0) <= h]
        if not videos:
            return None
        best = max(videos, key=lambda f: (f.get("height") or 0, f.get("fps") or 0, self._codec_rank(f), fsize(f) or 0))
        size = fsize(best)
        if size is None:
            return None
        return size + (0 if best["has_audio"] else a_size)

    def update_estimate(self):
        size = self.estimate_size()
        self.size_label.config(text=f"~ {format_size(size)}" if size else "—")

    def populate_format_controls(self):
        mode = self.mode.get()
        if mode == "video":
            heights = sorted({f["height"] for f in self.last_formats if f["has_video"] and f.get("height")}, reverse=True)
            hint = "\u00b7 sponsored" if AD_URL else ""
            qvals = [("item", QUALITY_BEST, hint)] + [("item", quality_label(h), hint if i == 0 else "") for i, h in enumerate(heights)]
            self.quality_box.configure(values=qvals)
            if self.quality.get() not in [v[1] for v in qvals]:
                self.quality.set(QUALITY_BEST)
        else:
            self.quality_box.configure(values=["MP3 / Best audio"])
            self.quality.set("MP3 / Best audio")
        self.update_estimate()
        self.refresh_sponsor_ui()

    def open_download_folder(self):
        folder = str(Path(self.config["download_dir"]))
        Path(folder).mkdir(parents=True, exist_ok=True)
        os.startfile(folder) if os.name == "nt" else webbrowser.open(folder)

    def start_server(self):
        try:
            self.server = ApiServer(self)
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Could not start local API on port {API_PORT}.\n\n{e}")

    def analyze(self):
        url = self.url.get().strip()
        if not url:
            messagebox.showwarning(APP_NAME, "Paste a video or page URL first.")
            self.url_entry.focus_set()
            return
        if urllib.parse.urlparse(url).scheme not in ("http", "https"):
            messagebox.showwarning(APP_NAME, "Only HTTP/HTTPS URLs are supported.")
            return
        self.analyze_btn.configure(state="disabled")
        self.info_title.config(text="Analyzing media…")
        self.info_meta.config(text="Fetching thumbnail, title, duration and available formats")
        self.info_error.config(text="")
        self.retry_browser_btn.pack_forget()
        self.media_badge.config(text="Analyzing…")
        self.status_var.set("Analyzing URL…")
        self.thumb_label.config(image="", text="Loading…")
        self.media_url = ""
        self.size_label.config(text="—")
        threading.Thread(target=self._analyze, args=(url,), daemon=True).start()

    def _analyze(self, url):
        try:
            info = analyze_url(url, self.config["browser_cookies"])
            self.last_info = info
            self.last_formats = build_formats(info)
            title = info.get("title", "Untitled")
            duration = format_duration(info.get("duration"))
            uploader = info.get("uploader") or "Unknown creator"
            thumb_url = info.get("thumbnail") or ""
            thumb_path = self.fetch_thumbnail(thumb_url, info.get("id", "thumb")) if thumb_url else ""
            self.media_url = info.get("webpage_url") or url
            self.root.after(0, self.populate_format_controls)
            self.root.after(0, lambda: self.info_title.config(text=title))
            self.root.after(0, lambda: self.info_meta.config(text=self.meta_text(info)))
            self.root.after(0, lambda: self.media_badge.config(text="Ready"))
            self.root.after(0, lambda: self.info_error.config(text=""))
            self.root.after(0, lambda: self.analyze_btn.configure(state="normal"))
            self.root.after(0, lambda: self.status_var.set("Media analyzed successfully"))
            self.start_dislike_fetch(info)
            if thumb_path:
                self.root.after(0, lambda p=thumb_path: self.show_thumbnail(p))
        except Exception as e:
            msg = str(e)
            self.media_url = ""
            self.root.after(0, lambda: self.thumb_label.config(image="", text="Thumbnail"))
            self.root.after(0, lambda: self.info_title.config(text="Unable to analyze this URL"))
            self.root.after(0, lambda: self.info_meta.config(text=""))
            self.root.after(0, lambda: self.info_error.config(text=msg))
            self.root.after(0, lambda: self.media_badge.config(text="Error"))
            self.root.after(0, lambda: self.analyze_btn.configure(state="normal"))
            low = msg.lower()
            auth_error = any(x in low for x in ("sign in", "not a bot", "cookies", "login required", "captcha"))
            dpapi_error = "dpapi" in low or "failed to decrypt" in low
            def show_retry():
                if dpapi_error and self.browser_exists("firefox"):
                    self.retry_browser_btn._text = "Retry with Firefox"
                else:
                    self.retry_browser_btn._text = "Retry with browser session"
                self.retry_browser_btn.pack(anchor="w", pady=(6,0)) if (auth_error or dpapi_error) else None
            self.root.after(0, show_retry)
            if dpapi_error:
                self.root.after(0, lambda: self.info_error.config(text="Chrome/Chromium cookie decryption failed on Windows (DPAPI). This is a browser encryption limitation. Firefox is the recommended session source for yt-dlp; sign in to YouTube in Firefox, select Firefox in Settings, then retry."))
            self.root.after(0, lambda: self.status_var.set("Analysis failed"))

    def fetch_thumbnail(self, url, video_id):
        if not Image or not ImageTk:
            return ""
        safe = "".join(c for c in str(video_id) if c.isalnum() or c in "-_ .")[:80] or "thumb"
        path = THUMB_CACHE / f"{safe}.jpg"
        if not path.exists():
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=15) as r:
                    path.write_bytes(r.read())
            except Exception:
                return ""
        return str(path)

    def show_thumbnail(self, path):
        try:
            img = Image.open(path).convert("RGB")
            img.thumbnail((200, 112), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.last_thumbnail = photo
            self.thumb_label.configure(image=photo, text="")
        except Exception:
            self.thumb_label.configure(image="", text="Thumbnail unavailable")

    def add_download(self):
        url = self.url.get().strip()
        if not url:
            messagebox.showwarning(APP_NAME, "Enter a URL first.")
            return
        if urllib.parse.urlparse(url).scheme not in ("http", "https"):
            messagebox.showwarning(APP_NAME, "Only HTTP/HTTPS URLs are supported.")
            return

        title = (self.last_info or {}).get("title", "")
        label = "MP3" if self.mode.get() == "audio" else self.quality.get()
        fmt_label, selector = label, ""
        thumb = (self.last_info or {}).get("thumbnail", "")
        job = self.manager.add(url, self.mode.get(), self.quality.get(), title, selector, fmt_label, thumb,
                               est_size=self.estimate_size() or 0.0)
        self.status_var.set(f"Added to download queue ({len(self.manager.jobs)} item{'s' if len(self.manager.jobs) != 1 else ''})")
        self.refresh_jobs()
        # Top quality is the sponsored option: open the sponsor page (rate-limited), the download runs regardless.
        if self.mode.get() == "video" and self.is_top_quality() and self.sponsor_due():
            self.open_sponsor_page()

    def refresh_jobs(self):
        # Called from worker threads too, so just schedule one throttled UI refresh.
        if getattr(self, "_refresh_pending", False):
            return
        self._refresh_pending = True
        try:
            self.root.after(150, self._do_refresh_jobs)
        except (RuntimeError, tk.TclError):
            self._refresh_pending = False

    def _do_refresh_jobs(self):
        self._refresh_pending = False
        try:
            wanted = {str(j.id) for j in self.manager.jobs}
            for iid in self.tree.get_children():
                if iid not in wanted:
                    self.tree.delete(iid)
            existing = set(self.tree.get_children())
            for pos, j in enumerate(self.manager.jobs, start=1):
                status = j.status
                vals = (
                    pos, j.title or j.url[:70], j.format_label or QUALITY_BEST, self.job_size_text(j), status,
                    f"{j.progress:.1f}%", j.speed or "—", j.eta or "—"
                )
                iid = str(j.id)
                tag = "active"
                if j.status == "Completed": tag = "completed"
                elif j.status == "Error": tag = "error"
                elif j.status == "Cancelled": tag = "cancelled"
                if iid in existing:
                    self.tree.item(iid, values=vals, tags=(tag,))
                    if self.tree.index(iid) != pos - 1:
                        self.tree.move(iid, "", pos - 1)
                else:
                    self.tree.insert("", "end", iid=iid, values=vals, tags=(tag,))
            self.queue_count.config(text=f"{len(self.manager.jobs)} item{'s' if len(self.manager.jobs) != 1 else ''}")
            self.autosize_tree_columns()
            self.update_queue_buttons()
        except tk.TclError:
            pass

    FINISHED = ("Completed", "Cancelled", "Error")

    @staticmethod
    def job_size_value(j):
        return j.actual_size or j.est_size or 0.0

    def job_size_text(self, j):
        if j.actual_size:
            return format_size(j.actual_size)
        if j.est_size:
            return f"~ {format_size(j.est_size)}"
        return "—"

    def selected_jobs(self):
        sel = set(self.tree.selection())
        return [j for j in self.manager.jobs if str(j.id) in sel]

    def update_queue_buttons(self):
        try:
            jobs = self.selected_jobs()
            any_finished = any(j.status in self.FINISHED for j in self.manager.jobs)
            self.btn_show.configure(state="normal" if any(j.file_path and Path(j.file_path).exists() for j in jobs) else "disabled")
            self.btn_cancel.configure(state="normal" if any(j.status not in self.FINISHED for j in jobs) else "disabled")
            self.btn_remove.configure(state="normal" if any(j.status in self.FINISHED for j in jobs) else "disabled")
            self.btn_clear_done.configure(state="normal" if any_finished else "disabled")
        except (tk.TclError, AttributeError):
            pass

    def autosize_tree_columns(self):
        # Text width is measured and clamped so columns remain readable without exploding.
        import tkinter.font as tkfont
        font = tkfont.Font(font=(self.font_family, 9))
        heading_font = tkfont.Font(font=(self.font_family, 9, "bold"))
        limits = {
            "id": (35, 55), "title": (180, 500), "format": (110, 220), "size": (80, 120),
            "status": (120, 300), "progress": (75, 110), "speed": (75, 140), "eta": (65, 100)
        }
        headings = {"id": "#", "title": "Title", "format": "Quality", "size": "Size", "status": "Status", "progress": "Progress", "speed": "Speed", "eta": "ETA"}
        for col, (mn, mx) in limits.items():
            width = heading_font.measure(headings[col]) + 36
            for iid in self.tree.get_children():
                width = max(width, font.measure(str(self.tree.set(iid, col))) + 22)
            self.tree.column(col, width=max(mn, min(mx, width)))

    # ---------- sorting (double-click a column heading) ----------
    def bind_heading_sort(self, tree, callback):
        """Clicking any column heading sorts by it; clicking again reverses the order."""
        for col in tree["columns"]:
            tree.heading(col, command=lambda c=col: callback(c))

    def update_sort_arrows(self, tree, headings, col, reverse):
        for c, text in headings.items():
            arrow = (" \u25BC" if reverse else " \u25B2") if c == col else ""
            tree.heading(c, text=text + arrow)

    def sort_queue(self, col):
        cur, rev = self.sort_state["queue"]
        rev = (not rev) if cur == col else False
        self.sort_state["queue"] = (col, rev)
        keys = {
            "id": lambda j: j.id,
            "title": lambda j: (j.title or j.url).casefold(),
            "format": lambda j: quality_rank(j.format_label),
            "size": self.job_size_value,
            "status": lambda j: j.status.casefold(),
            "progress": lambda j: j.progress,
            "speed": lambda j: parse_rate(j.speed),
            "eta": lambda j: parse_eta(j.eta),
        }
        with self.manager.lock:
            self.manager.jobs = sorted(self.manager.jobs, key=keys[col], reverse=rev)
        self.update_sort_arrows(self.tree, self.queue_headings, col, rev)
        self._do_refresh_jobs()

    def sort_history(self, col):
        cur, rev = self.sort_state["history"]
        rev = (not rev) if cur == col else False
        self.sort_state["history"] = (col, rev)
        self.refresh_history()

    # ---------- right-click menus ----------
    def popup_menu(self, event, entries):
        """entries: (label, command, enabled) tuples, or None for a separator."""
        c = self.colors
        menu = tk.Menu(self.root, tearoff=0, bg=c["card2"], fg=c["text"], activebackground=c["accent"],
                       activeforeground=c["white"], disabledforeground="#6a6a6a", bd=0, relief="flat",
                       font=(self.font_family, 9))
        for entry in entries:
            if entry is None:
                menu.add_separator()
            else:
                label, command, enabled = entry
                menu.add_command(label=label, command=command, state="normal" if enabled else "disabled")
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def copy_text(self, text, what="Copied"):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set(f"{what} to clipboard")

    @staticmethod
    def _plural(n, word, plural=None):
        return f"{n} {word if n == 1 else (plural or word + 's')}"

    def queue_double_click(self, event):
        if self.tree.identify_region(event.x, event.y) == "cell":
            self.open_selected_job()

    def queue_menu(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        if row not in self.tree.selection():
            self.tree.selection_set(row)
        jobs = self.selected_jobs()
        if not jobs:
            return
        first = jobs[0]
        has_file = any(j.file_path and Path(j.file_path).exists() for j in jobs)
        active = [j for j in jobs if j.status not in self.FINISHED]
        done = [j for j in jobs if j.status in self.FINISHED]
        return self.popup_menu(event, [
            ("Show File in Folder", self.open_selected_job, has_file),
            ("Open Video Page", lambda: webbrowser.open(first.url), len(jobs) == 1),
            ("Copy Video Link", lambda: self.copy_text("\n".join(j.url for j in jobs), "Link copied"), True),
            ("Copy Title", lambda: self.copy_text("\n".join(j.title or j.url for j in jobs), "Title copied"), True),
            None,
            ("Copy Error Details", lambda: self.copy_text("\n\n".join(j.error for j in jobs if j.error), "Error details copied"), any(j.error for j in jobs)),
            ("Open Log File", lambda: self.open_path(first.log_path), len(jobs) == 1 and bool(first.log_path) and Path(first.log_path).exists()),
            None,
            (f"Cancel {self._plural(len(active), 'download')}" if active else "Cancel", self.cancel_selected, bool(active)),
            (f"Remove {self._plural(len(done), 'item')}" if done else "Remove", self.remove_selected, bool(done)),
        ])

    def history_menu(self, event):
        row = self.history_tree.identify_row(event.y)
        if not row:
            return
        if row not in self.history_tree.selection():
            self.history_tree.selection_set(row)
        sel = self.selected_history_items()
        if not sel:
            return
        items = [i for _, i in sel]
        first = items[0]
        has_file = any(i.get("file_path") and Path(i["file_path"]).exists() for i in items)
        has_path = any(i.get("file_path") for i in items)
        n = len(items)
        return self.popup_menu(event, [
            ("Open File", self.open_history_file, has_file),
            ("Show in Folder", self.show_history_file, has_file),
            ("Open Video Page", lambda: webbrowser.open(first["url"]), n == 1 and bool(first.get("url"))),
            None,
            ("Copy Video Link", lambda: self.copy_text("\n".join(i.get("url", "") for i in items), "Link copied"), True),
            ("Copy Title", lambda: self.copy_text("\n".join(i.get("title", "") for i in items), "Title copied"), True),
            ("Copy File Path", lambda: self.copy_text("\n".join(i.get("file_path", "") for i in items if i.get("file_path")), "Path copied"), has_path),
            None,
            (f"Download Again ({n})" if n > 1 else "Download Again", self.redownload_selected, all(i.get("url") for i in items)),
            (f"Remove {self._plural(n, 'entry', 'entries')} from History", self.remove_history_selected, True),
        ])

    def redownload_selected(self):
        count = 0
        for _, item in self.selected_history_items():
            url = item.get("url")
            if not url:
                continue
            label = item.get("format") or QUALITY_BEST
            audio = label.upper().startswith("MP3")
            self.manager.add(url, "audio" if audio else "video", "MP3 / Best audio" if audio else label,
                             item.get("title", ""), "", label, "")
            count += 1
        if count:
            self.show_page("Downloader")
            self.status_var.set(f"Added {self._plural(count, 'item')} to the download queue")

    def open_path(self, path):
        try:
            if os.name == "nt":
                os.startfile(path)
            else:
                webbrowser.open(Path(path).as_uri())
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Could not open the file: {e}")

    def reveal_file(self, path):
        try:
            if os.name == "nt":
                subprocess.Popen(["explorer", f"/select,{Path(path)}"])
            else:
                webbrowser.open(Path(path).parent.as_uri())
        except Exception:
            self.open_download_folder()

    def open_selected_job(self, _event=None):
        files = [j.file_path for j in self.selected_jobs() if j.file_path and Path(j.file_path).exists()]
        if files:
            self.reveal_file(files[0])
        elif not self.selected_jobs():
            self.open_download_folder()

    def cancel_selected(self, _event=None):
        jobs = [j for j in self.selected_jobs() if j.status not in self.FINISHED]
        for j in jobs:
            self.manager.cancel(j)
        if jobs:
            self.status_var.set(f"Cancelled {len(jobs)} download{'s' if len(jobs) != 1 else ''}")

    def _drop_jobs(self, jobs):
        ids = {j.id for j in jobs}
        self.manager.jobs = [j for j in self.manager.jobs if j.id not in ids]
        self._do_refresh_jobs()

    def remove_selected(self):
        jobs = [j for j in self.selected_jobs() if j.status in self.FINISHED]
        if not jobs:
            return
        self._drop_jobs(jobs)
        self.status_var.set(f"Removed {len(jobs)} item{'s' if len(jobs) != 1 else ''} from the queue")

    def clear_completed(self):
        jobs = [j for j in self.manager.jobs if j.status in self.FINISHED]
        if not jobs:
            self.status_var.set("Nothing to clear")
            return
        self._drop_jobs(jobs)
        self.status_var.set("Finished items cleared from queue")

    def record_history(self, job):
        record = {
            "id": job.id,
            "url": job.url,
            "title": job.title or "Untitled",
            "status": job.status,
            "format": job.format_label or "Automatic",
            "file_path": job.file_path,
            "size": Path(job.file_path).stat().st_size if job.file_path and Path(job.file_path).exists() else None,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
            "error": job.error,
        }
        self.history.append(record)
        save_history(self.history)
        self.root.after(0, self.refresh_history)

    def refresh_history(self):
        try:
            for iid in self.history_tree.get_children():
                self.history_tree.delete(iid)
            rows = list(enumerate(self.history))[::-1]          # newest first by default
            col, rev = self.sort_state["history"]
            if col:
                def key(row):
                    idx, item = row
                    if col == "num":
                        return idx
                    if col == "date":
                        return item.get("completed_at") or item.get("created_at") or ""
                    if col == "title":
                        return (item.get("title") or "").casefold()
                    if col == "status":
                        return (item.get("status") or "").casefold()
                    if col == "format":
                        return quality_rank(item.get("format"))
                    if col == "size":
                        return item.get("size") or 0
                    return (item.get("file_path") or item.get("error") or "").casefold()
                rows.sort(key=key, reverse=rev)
            for pos, (idx, item) in enumerate(rows, start=1):    # "#" counts 1, 2, 3 ... top to bottom
                dt = item.get("completed_at") or item.get("created_at") or ""
                vals = (
                    pos, dt.replace("T", " "), item.get("title", "Untitled"), item.get("status", ""),
                    item.get("format", "Automatic"), format_size(item.get("size")) if item.get("size") else "—", item.get("file_path", "") or item.get("error", "")
                )
                self.history_tree.insert("", "end", iid=str(idx), values=vals, tags=(item.get("status", ""),))
            self.history_count.config(text=f"{len(self.history)} download{'s' if len(self.history) != 1 else ''}")
            self.update_sort_arrows(self.history_tree, self.history_headings, col, rev)
            self.autosize_history_columns()
            self.update_history_buttons()
        except (tk.TclError, AttributeError):
            pass

    def autosize_history_columns(self):
        font = tkfont.Font(font=(self.font_family, 9))
        hf = tkfont.Font(font=(self.font_family, 9, "bold"))
        heads = {"num": "#", "date": "Date", "title": "Title", "status": "Status", "format": "Quality", "size": "Size", "path": "File"}
        limits = {"num": (40, 60), "date": (125, 180), "title": (180, 500), "status": (90, 130), "format": (110, 220), "size": (80, 120), "path": (180, 600)}
        for col, (mn, mx) in limits.items():
            w = hf.measure(heads[col]) + 36
            for iid in self.history_tree.get_children():
                w = max(w, font.measure(str(self.history_tree.set(iid, col))) + 22)
            self.history_tree.column(col, width=max(mn, min(mx, w)))

    def selected_history_items(self):
        out = []
        for iid in self.history_tree.selection():
            try:
                idx = int(iid)
            except ValueError:
                continue
            if 0 <= idx < len(self.history):
                out.append((idx, self.history[idx]))
        return out

    def update_history_buttons(self):
        try:
            sel = self.selected_history_items()
            has_file = any(i.get("file_path") and Path(i["file_path"]).exists() for _, i in sel)
            self.hbtn_open.configure(state="normal" if has_file else "disabled")
            self.hbtn_folder.configure(state="normal" if has_file else "disabled")
            self.hbtn_remove.configure(state="normal" if sel else "disabled")
            self.hbtn_clear_all.configure(state="normal" if self.history else "disabled")
        except (tk.TclError, AttributeError):
            pass

    def open_history_file(self):
        sel = self.selected_history_items()
        if not sel:
            return
        existing = [i["file_path"] for _, i in sel if i.get("file_path") and Path(i["file_path"]).exists()]
        if existing:
            for path in existing[:5]:
                if os.name == "nt":
                    os.startfile(path)
                else:
                    webbrowser.open(Path(path).parent.as_uri())
        else:
            item = sel[0][1]
            if item.get("file_path"):
                messagebox.showinfo(APP_NAME, "The file is no longer present at its recorded location.")
            else:
                messagebox.showinfo(APP_NAME, item.get("error", "No file was created for this entry."))

    def show_history_file(self):
        for _, item in self.selected_history_items():
            path = item.get("file_path")
            if path and Path(path).exists():
                self.reveal_file(path)
                return

    def remove_history_selected(self):
        sel = self.selected_history_items()
        if not sel:
            return
        drop = {idx for idx, _ in sel}
        self.history = [h for i, h in enumerate(self.history) if i not in drop]
        save_history(self.history)
        self.refresh_history()
        self.status_var.set(f"Removed {len(drop)} entr{'ies' if len(drop) != 1 else 'y'} from history (files were not deleted)")

    def clear_history(self):
        if not self.history:
            return
        if not messagebox.askyesno(APP_NAME, "Clear all download history? The downloaded files will not be deleted."):
            return
        self.history = []
        save_history(self.history)
        self.refresh_history()
        self.status_var.set("Download history cleared")

    def update_ytdlp(self, label):
        """Source install: pip upgrade. Packaged exe: download the official standalone yt-dlp.exe."""
        label.config(text="Updating... please wait", fg=self.colors["muted"])

        def done(text, ok):
            try:
                label.config(text=text, fg=self.colors["success"] if ok else self.colors["danger"])
            except tk.TclError:
                pass

        def work():
            try:
                if FROZEN:
                    dest = ROOT / "yt-dlp.exe"
                    tmp = ROOT / "yt-dlp-new.exe"
                    req = urllib.request.Request("https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe",
                                                 headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
                    with urllib.request.urlopen(req, timeout=90) as r, open(tmp, "wb") as f:
                        shutil.copyfileobj(r, f)
                    check = subprocess.run([str(tmp), "--version"], capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW)
                    if check.returncode != 0:
                        raise RuntimeError("downloaded file did not run")
                    os.replace(tmp, dest)
                    msg = f"Updated to {check.stdout.strip()}"
                else:
                    p = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp", "yt-dlp-ejs"],
                                       capture_output=True, text=True, timeout=300, creationflags=NO_WINDOW)
                    if p.returncode != 0:
                        raise RuntimeError((p.stderr or p.stdout).strip().splitlines()[-1][:160])
                    msg = f"Updated - restart {APP_NAME} to use it"
                self.root.after(0, lambda: done(msg, True))
            except Exception as e:
                self.root.after(0, lambda m=str(e)[:160]: done(f"Update failed: {m}", False))
        threading.Thread(target=work, daemon=True).start()

    def place_over_root(self, win, w, h):
        """Center a window over the app, on whichever monitor the app is currently on."""
        try:
            self.root.update_idletasks()
            ml, mt, mr, mb = monitor_workarea(self.root)
            x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 2
            x = max(ml, min(x, mr - w))
            y = max(mt, min(y, mb - h))
            win.geometry(f"{w}x{h}+{x}+{y}")
        except tk.TclError:
            win.geometry(f"{w}x{h}")

    def settings(self):
        win = tk.Toplevel(self.root)
        win.title(f"{APP_NAME} Settings")
        self.place_over_root(win, 720, 720)
        win.minsize(650, 700)
        win.transient(self.root)
        win.configure(bg=self.colors["bg"])
        self.apply_window_icon(win)
        win.after(60, lambda: style_title_bar(win, self.colors["bg"], self.colors["text"], self.colors["bg"]))
        frm = tk.Frame(win, bg=self.colors["bg"])
        frm.pack(fill="both", expand=True, padx=24, pady=20)
        tk.Label(frm, text="Settings", bg=self.colors["bg"], fg=self.colors["text"], font=(self.font_family, 20, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 18))
        label_kw = dict(bg=self.colors["bg"], fg=self.colors["text"], font=(self.font_family, 9, "bold"))
        muted_kw = dict(bg=self.colors["bg"], fg=self.colors["muted"], font=(self.font_family, 8))
        tk.Label(frm, text="Download folder", **label_kw).grid(row=1, column=0, sticky="w", pady=9)
        d = tk.StringVar(value=self.config["download_dir"])
        tk.Entry(frm, textvariable=d, bg=self.colors["field"], fg=self.colors["text"], insertbackground=self.colors["text"], relief="flat", highlightthickness=1, highlightbackground=self.colors["border"], highlightcolor=self.colors["accent"]).grid(row=1, column=1, sticky="ew", padx=12)
        self.text_button(frm, "Browse", lambda: d.set(filedialog.askdirectory(parent=win) or d.get())).grid(row=1, column=2)
        tk.Label(frm, text="Browser session", **label_kw).grid(row=2, column=0, sticky="w", pady=9)
        c = tk.StringVar(value=self.config["browser_cookies"])
        ttk.Combobox(frm, textvariable=c, values=["none", "chrome", "edge", "firefox", "brave", "chromium", "opera", "vivaldi", "whale"], state="readonly", width=18).grid(row=2, column=1, sticky="w", padx=12)
        tk.Label(frm, text="Used only when you explicitly choose a browser session for authorized content.", **muted_kw).grid(row=3, column=1, sticky="w", padx=12)
        tk.Label(frm, text="If YouTube asks you to sign in or confirm you're not a bot, select your signed-in browser here and use Retry with browser session.", **muted_kw, wraplength=520, justify="left").grid(row=4, column=1, columnspan=2, sticky="w", padx=12, pady=(0,8))
        tk.Label(frm, text="cookies.txt (optional)", **label_kw).grid(row=9, column=0, sticky="w", pady=9)
        cf = tk.StringVar(value=self.config.get("cookies_file", ""))
        tk.Entry(frm, textvariable=cf, bg=self.colors["field"], fg=self.colors["text"], insertbackground=self.colors["text"], relief="flat", highlightthickness=1, highlightbackground=self.colors["border"], highlightcolor=self.colors["accent"]).grid(row=9, column=1, sticky="ew", padx=12)
        self.text_button(frm, "Browse", lambda: cf.set(filedialog.askopenfilename(parent=win, filetypes=[("Cookies", "*.txt"), ("All files", "*.*")]) or cf.get())).grid(row=9, column=2)
        tk.Label(frm, text="Exported YouTube cookies (Netscape format). Overrides the browser session; fixes the Chrome decryption error.", **muted_kw, wraplength=520, justify="left").grid(row=10, column=1, columnspan=2, sticky="w", padx=12)
        tk.Label(frm, text="API token", **label_kw).grid(row=5, column=0, sticky="w", pady=9)
        t = tk.StringVar(value=self.config["api_token"])
        tk.Entry(frm, textvariable=t, state="readonly", bg=self.colors["field"], fg=self.colors["muted"], relief="flat", readonlybackground=self.colors["field"]).grid(row=5, column=1, sticky="ew", padx=12)
        tk.Label(frm, text="Used by the optional browser extension.", **muted_kw).grid(row=6, column=1, sticky="w", padx=12)
        tk.Label(frm, text="Dislikes", **label_kw).grid(row=8, column=0, sticky="w", pady=9)
        dv = tk.BooleanVar(value=bool(self.config.get("show_dislikes", True)))
        tk.Checkbutton(frm, text="Show estimated dislikes (community data from Return YouTube Dislike)", variable=dv,
                       bg=self.colors["bg"], fg=self.colors["text"], selectcolor=self.colors["field"],
                       activebackground=self.colors["bg"], activeforeground=self.colors["text"],
                       font=(self.font_family, 9)).grid(row=8, column=1, columnspan=2, sticky="w", padx=12)
        tk.Label(frm, text="FFmpeg", **label_kw).grid(row=7, column=0, sticky="w", pady=9)
        ff = executable("ffmpeg")
        tk.Label(frm, text=f"Detected • {ff}" if ff else "Not found", bg=self.colors["bg"], fg=self.colors["success"] if ff else self.colors["danger"], font=(self.font_family, 9)).grid(row=7, column=1, sticky="w", padx=12)
        tk.Label(frm, text="yt-dlp", **label_kw).grid(row=11, column=0, sticky="w", pady=9)
        try:
            import yt_dlp.version as _yv
            ytv = _yv.__version__
        except Exception:
            ytv = "unknown"
        extra = "  (updated copy installed)" if FROZEN and (ROOT / "yt-dlp.exe").is_file() else ""
        yt_status = tk.Label(frm, text=f"Version {ytv}{extra}", bg=self.colors["bg"], fg=self.colors["muted"], font=(self.font_family, 9))
        yt_status.grid(row=11, column=1, sticky="w", padx=12)
        self.text_button(frm, "Update", lambda: self.update_ytdlp(yt_status)).grid(row=11, column=2)
        tk.Label(frm, text="YouTube changes often. If downloads fail with HTTP 403, update yt-dlp, then retry.", **muted_kw, wraplength=520, justify="left").grid(row=12, column=1, columnspan=2, sticky="w", padx=12)
        tk.Label(frm, text="Updates", **label_kw).grid(row=13, column=0, sticky="w", pady=9)
        self._settings_update_label = tk.Label(frm, text=self.update_status_text(), bg=self.colors["bg"], fg=self.colors["muted"], font=(self.font_family, 9), anchor="w")
        self._settings_update_label.grid(row=13, column=1, sticky="w", padx=12)
        self.text_button(frm, "Check now", self.check_updates_now).grid(row=13, column=2)
        au = tk.BooleanVar(value=bool(self.config.get("auto_update", True)))
        tk.Checkbutton(frm, text="Check on start and download updates in the background (installs when the app closes)", variable=au,
                       bg=self.colors["bg"], fg=self.colors["text"], selectcolor=self.colors["field"],
                       activebackground=self.colors["bg"], activeforeground=self.colors["text"],
                       font=(self.font_family, 9)).grid(row=14, column=1, columnspan=2, sticky="w", padx=12)
        tk.Label(frm, text=f"{APP_NAME}  •  {USER_WATERMARK}", **muted_kw).grid(row=15, column=0, columnspan=2, sticky="w", pady=(20,0))
        frm.columnconfigure(1, weight=1)
        def save():
            self.config["download_dir"] = d.get().strip()
            self.config["browser_cookies"] = c.get()
            self.config["api_token"] = t.get()
            self.config["show_dislikes"] = bool(dv.get())
            self.config["auto_update"] = bool(au.get())
            global COOKIES_FILE
            self.config["cookies_file"] = cf.get().strip()
            COOKIES_FILE = self.config["cookies_file"]
            if not self.config["download_dir"]:
                messagebox.showwarning(APP_NAME, "Download folder cannot be empty.", parent=win)
                return
            Path(self.config["download_dir"]).mkdir(parents=True, exist_ok=True)
            CONFIG.write_text(json.dumps(self.config, indent=2), encoding="utf-8")
            win.destroy()
            self.status_var.set("Settings saved")
        self.text_button(frm, "Save Settings", save, accent=True).grid(row=15, column=2, sticky="e", pady=(20,0))

    def close(self):
        for j in list(self.manager.jobs):
            try:
                if j.process and j.process.poll() is None:
                    j.process.terminate()
            except Exception:
                pass
        if self.server:
            try:
                threading.Thread(target=self.server.shutdown, daemon=True).start()
            except Exception:
                pass
        if not self._update_applied and self.updater.enabled and self.config.get("auto_update", True):
            self._update_applied = self.updater.apply(relaunch=False)   # swapped in right after this process exits
        self.root.destroy()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--run-yt-dlp":
        run_yt_dlp_cli(sys.argv[2:])
    else:
        if FROZEN and load_config().get("auto_update", True) and make_updater().apply(relaunch=True):
            sys.exit(0)      # a downloaded update was waiting: it is swapped in and the new version starts
        App().root.mainloop()
