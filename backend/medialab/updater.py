"""Automatic updates from the se7en GitHub releases.

Feed: ``https://github.com/<repo>/releases/latest/download/latest.json`` (one
request, no API rate limit), written by ``packaging/publish.ps1``::

    {"version": "1.2.0", "notes": "...", "published": "2026-10-01",
     "update":   {"name": "se7en-Censored-Update-1.2.0.exe",     "url": "...", "sha256": "...", "size": 123},
     "portable": {"name": "se7en-Censored-Portable-1.2.0.zip",   "url": "...", "sha256": "...", "size": 456}}

* installed copy → the small "Update" installer runs silently, then se7en restarts;
* portable copy  → the new zip is unpacked next to the old one by a helper script
  once se7en has closed (the ``data`` folder is never touched), then it restarts.

Every download is checked against its SHA-256 before anything is run. The repo
comes from ``update.json`` next to the executable (written by the build), or the
``SE7EN_UPDATE_URL`` environment variable (tests / private feeds).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

import httpx

from . import __version__
from .config import app_root, is_frozen, is_portable, paths
from .errors import MediaLabError
from .events import bus
from .logging_setup import get_logger

log = get_logger("app")
CHECK_EVERY = 6 * 3600
# Hidden console (not DETACHED_PROCESS: without any console cmd cannot run the tasklist|find pipe and quits),
# own process group, and out of se7en's job object when allowed so it survives se7en closing.
HIDDEN = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
SYS32 = r"%SystemRoot%\System32"


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


def feed_url() -> str | None:
    env = os.environ.get("SE7EN_UPDATE_URL")
    if env:
        return env
    cfg = app_root() / "update.json"
    try:
        data = json.loads(cfg.read_text("utf-8-sig"))  # tolerates the BOM PowerShell writes
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("feed"):
        return str(data["feed"])
    repo = str(data.get("repo") or "").strip().strip("/")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        return None
    return f"https://github.com/{repo}/releases/latest/download/latest.json"


def _wait_for(pid: int) -> list[str]:
    """Batch lines looping until process ``pid`` has exited (``ping`` works without a console, ``timeout`` does not).
    Full System32 paths: Git/MSYS in PATH would otherwise shadow ``find`` with the Unix tool."""
    return [":wait", f'"{SYS32}\\tasklist.exe" /FI "PID eq {pid}" /NH 2>nul | "{SYS32}\\find.exe" "{pid}" >nul '
                     f'&& ("{SYS32}\\PING.EXE" -n 2 127.0.0.1 >nul & goto wait)']


def _write_script(path: Path, lines: list[str]) -> None:
    """Write a .cmd helper with exact CRLF endings (a stray CR breaks ``goto`` labels), in the console
    code page so folders with accents keep working; UTF-8 + ``chcp 65001`` when that page cannot encode them."""
    body = "\r\n".join(["@echo off", *lines]) + "\r\n"
    try:
        import ctypes
        data = body.encode(f"cp{ctypes.windll.kernel32.GetOEMCP()}")
    except (UnicodeEncodeError, LookupError, AttributeError, OSError):
        data = ("@chcp 65001 >nul\r\n" + body).encode("utf-8")
    path.write_bytes(data)


def _launch(script: Path, cwd: str) -> None:
    """Start the helper so it outlives se7en (it waits for our PID before touching any file)."""
    args = [os.environ.get("COMSPEC", "cmd.exe"), "/c", str(script)]
    io = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    try:
        subprocess.Popen(args, creationflags=HIDDEN | BREAKAWAY, close_fds=True, cwd=cwd, **io)
    except OSError:  # the job forbids breakaway: still fine unless it kills its children on close
        subprocess.Popen(args, creationflags=HIDDEN, close_fds=True, cwd=cwd, **io)


class Updater:
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"state": "idle", "current": __version__, "configured": bool(feed_url()),
                                      "portable": is_portable()}
        self._lock = threading.Lock()
        self._manifest: dict | None = None
        self._file: Path | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------ state
    def _set(self, **kw: Any) -> None:
        self.state = {**self.state, **kw}
        bus.publish("update", self.state)

    def snapshot(self) -> dict:
        return dict(self.state)

    # ------------------------------------------------------------ check
    def check(self, manual: bool = False) -> dict:
        url = feed_url()
        if not url:
            self._set(state="unconfigured", configured=False)
            return self.snapshot()
        if self.state.get("state") in ("downloading", "installing"):
            return self.snapshot()
        before = self.snapshot()
        self._set(state="checking", configured=True, error=None)
        try:
            with httpx.Client(follow_redirects=True, timeout=20, headers={"User-Agent": f"se7en/{__version__}"}) as c:
                r = c.get(url)
                if r.status_code == 404:
                    self._set(state="uptodate", checked=time.time(), latest=__version__)
                    return self.snapshot()
                r.raise_for_status()
                manifest = r.json()
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as e:
            log.info("Update check failed: %s", e)
            self._set(state="error", error={"code": "update_check_failed",
                                            "message": "Could not check for updates.",
                                            "hint": "Check your internet connection."}, checked=time.time())
            return self.snapshot()
        version = str(manifest.get("version") or "")
        pkg = self._package(manifest)
        if not version or not pkg:
            self._set(state="error", error={"code": "bad_manifest", "message": "The update information is invalid."})
            return self.snapshot()
        self._manifest = manifest
        if before.get("state") == "ready" and before.get("latest") == version and self._file and self._file.exists():
            self._set(**{**before, "checked": time.time()})  # already downloaded: stays ready to install
        elif is_newer(version):
            log.info("Update available: %s -> %s", __version__, version)
            self._set(state="available", latest=version, notes=str(manifest.get("notes") or "")[:4000],
                      published=manifest.get("published"), size=pkg.get("size"), checked=time.time())
        else:
            self._set(state="uptodate", latest=version, checked=time.time())
        return self.snapshot()

    def _package(self, manifest: dict) -> dict | None:
        pkg = manifest.get("portable" if is_portable() else "update")
        if not isinstance(pkg, dict) or not str(pkg.get("url", "")).startswith("https://") and \
                not os.environ.get("SE7EN_UPDATE_URL"):
            return None
        if not re.fullmatch(r"[0-9a-f]{64}", str(pkg.get("sha256", "")).lower()):
            return None
        return pkg

    # ------------------------------------------------------------ download
    def download(self) -> dict:
        with self._lock:
            if self.state.get("state") == "ready":
                return self.snapshot()
            if not self._manifest:
                self.check()
            if self.state.get("state") != "available" or not self._manifest:
                return self.snapshot()
            pkg = self._package(self._manifest)
            assert pkg
            folder = paths().data / "updates"
            folder.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^\w.\-]", "_", str(pkg.get("name") or "se7en-update"))
            dest = folder / name
            tmp = dest.with_suffix(dest.suffix + ".part")
            self._set(state="downloading", downloaded=0, total=pkg.get("size"))
            h = hashlib.sha256()
            try:
                with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30, read=120),
                                  headers={"User-Agent": f"se7en/{__version__}"}) as c, c.stream("GET", pkg["url"]) as r:
                    r.raise_for_status()
                    total = int(r.headers.get("content-length") or pkg.get("size") or 0)
                    done, last = 0, 0.0
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_bytes(1 << 20):
                            f.write(chunk)
                            h.update(chunk)
                            done += len(chunk)
                            if time.monotonic() - last > 0.3:
                                last = time.monotonic()
                                self._set(downloaded=done, total=total)
            except (httpx.HTTPError, OSError) as e:
                tmp.unlink(missing_ok=True)
                self._set(state="available", error={"code": "update_download_failed",
                                                    "message": "The update could not be downloaded.",
                                                    "hint": "Check your internet connection and retry."})
                log.warning("Update download failed: %s", e)
                return self.snapshot()
            if h.hexdigest() != str(pkg["sha256"]).lower():
                tmp.unlink(missing_ok=True)
                self._set(state="available", error={"code": "checksum_mismatch",
                                                    "message": "The downloaded update is corrupted.", "hint": "Retry."})
                return self.snapshot()
            os.replace(tmp, dest)
            self._file = dest
            log.info("Update %s downloaded and verified: %s", self._manifest.get("version"), dest)
            self._set(state="ready", downloaded=done, file=str(dest), error=None)
            return self.snapshot()

    # ------------------------------------------------------------ install
    def auto_download(self) -> None:
        """Background download of an available update (at most one attempt every 30 minutes)."""
        now = time.monotonic()
        if now - getattr(self, "_last_auto", -1e9) < 1800:
            return
        self._last_auto = now
        self.download()

    def install(self, relaunch: bool = True) -> dict:
        """Start the installer / swap script, detached. The caller must then exit se7en."""
        if self.state.get("state") != "ready" or not self._file or not self._file.exists():
            raise MediaLabError("update_not_ready", "The update is not downloaded yet.")
        if not is_frozen():
            raise MediaLabError("update_dev", "Updates are installed only in the packaged app.",
                                "Run the build (packaging/build.ps1) to test updates.")
        self._set(state="installing")
        if is_portable():
            self._swap_portable(self._file, relaunch)
        else:
            self._run_installer(self._file, relaunch)
        log.info("Update installer started; se7en closes now")
        return self.snapshot()

    def _run_installer(self, exe: Path, relaunch: bool) -> None:
        """Run the update installer once se7en has fully exited (its files are then free)."""
        folder = paths().data / "updates"
        log_file = folder / "update-install.log"
        script = folder / "apply-update.cmd"
        pid = os.getpid()
        app_exe = app_root() / "se7en-censored.exe"
        _write_script(script, [
            *_wait_for(pid),
            # se7en reopened in the meantime would lock its own files: the installer then aborted halfway and
            # left a broken program folder (1.3.0). Close it now and reopen it once the update is in place.
            f'set "RELAUNCH={1 if relaunch else 0}"',
            f'"{SYS32}\\tasklist.exe" /FI "IMAGENAME eq se7en-censored.exe" /NH 2>nul | "{SYS32}\\find.exe" /I "se7en-censored.exe" >nul '
            f'&& ("{SYS32}\\taskkill.exe" /F /IM se7en-censored.exe >nul 2>&1 & set "RELAUNCH=1" & "{SYS32}\\PING.EXE" -n 3 127.0.0.1 >nul)',
            f'"{exe}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /CLOSEAPPLICATIONS /FORCECLOSEAPPLICATIONS '
            f'/RELAUNCH=%RELAUNCH% /DIR="{app_root()}" /LOG="{log_file}"',
            f'if errorlevel 1 if "%RELAUNCH%"=="1" start "" "{app_exe}"',
            'del "%~f0"',
        ])
        _launch(script, str(folder))

    def _swap_portable(self, archive: Path, relaunch: bool = True) -> None:
        staging = paths().data / "updates" / "staging"
        import shutil
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        with zipfile.ZipFile(archive) as z:
            for m in z.infolist():
                dest = (staging / m.filename).resolve()
                if not str(dest).startswith(str(staging.resolve())):
                    raise MediaLabError("bad_archive", "The update archive contains unsafe paths.")
            z.extractall(staging)
        inner = staging
        if not (inner / "se7en-censored.exe").exists():  # zip with a top folder
            subs = [d for d in staging.iterdir() if d.is_dir() and (d / "se7en-censored.exe").exists()]
            if not subs:
                raise MediaLabError("bad_archive", "The update archive does not contain se7en.")
            inner = subs[0]
        app = app_root()
        script = paths().data / "updates" / "apply-update.cmd"
        _write_script(script, [
            *_wait_for(os.getpid()),
            f'"{SYS32}\\robocopy.exe" "{inner / "_internal"}" "{app / "_internal"}" /MIR /R:5 /W:1 /NFL /NDL /NJH /NJS >nul',
            f'"{SYS32}\\robocopy.exe" "{inner}" "{app}" /E /XD "{inner / "_internal"}" data /XF portable.flag /R:5 /W:1 /NFL /NDL /NJH /NJS >nul',
            f'start "" "{app / "se7en-censored.exe"}"' if relaunch else "rem no relaunch",
            f'rmdir /s /q "{staging}"',
            'del "%~f0"',
        ])
        _launch(script, str(app.parent))

    # ------------------------------------------------------------ "se7en has been updated"
    def note_launch(self) -> None:
        """Remember the version that runs; when it differs from the previous launch, the interface shows once
        what changed. Copies older than this mechanism have no marker: a settings file that existed before
        this process started means se7en was already installed (an update, not a first install)."""
        marker = paths().data / "version.txt"
        try:
            prev = marker.read_text("utf-8").strip() if marker.exists() else None
            if prev is None and paths().settings.exists():
                import psutil
                st = paths().settings.stat()
                born = getattr(st, "st_birthtime", st.st_ctime)
                if born < psutil.Process().create_time() - 2:
                    prev = "older"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(__version__, "utf-8")
        except OSError as e:
            log.info("Version marker not updated: %s", e)
            return
        if prev and prev != __version__ and (prev == "older" or is_newer(__version__, prev)):
            from .changelog import since
            log.info("se7en updated: %s -> %s", prev, __version__)
            self.state = {**self.state, "updated_from": prev, "changes": since(prev, __version__)}

    def seen(self) -> dict:
        self.state = {k: v for k, v in self.state.items() if k not in ("updated_from", "changes")}
        bus.publish("update", self.state)
        return self.snapshot()

    # ------------------------------------------------------------ background checks
    def start_background(self, enabled) -> None:
        def loop() -> None:
            if self._stop.wait(3):  # right after launch: the interface announces a new version at once
                return
            while not self._stop.is_set():
                if enabled():
                    self.check()
                if self._stop.wait(CHECK_EVERY):
                    return

        threading.Thread(target=loop, name="updater", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


updater = Updater()
