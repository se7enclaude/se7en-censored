"""Open a web page in a real browser (Microsoft Edge, headless) and watch which videos it loads.

Many sites only insert their player with JavaScript, or load the video after a click on "play": the raw
HTML does not contain it. Edge is part of every Windows 10/11 install, so no browser is bundled: it is
driven over the DevTools protocol, with a fresh empty profile (no cookies, no account, nothing of the
user's) that is deleted afterwards.

What is collected: every network response that is a video or a stream manifest (m3u8/mpd), with the
page or frame that asked for it (its Referer), the <video> elements, the final HTML and the links.
Nothing is decrypted: an encrypted stream stays encrypted.
"""
from __future__ import annotations

import itertools
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from ..logging_setup import get_logger

log = get_logger("download")

MEDIA_EXT = (".mp4", ".webm", ".mov", ".m4v", ".mkv", ".ogv", ".m3u8", ".mpd")
SEGMENT_EXT = (".ts", ".m4s", ".aac", ".m4a", ".vtt", ".key", ".mp3")
MANIFEST_MIME = ("application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl",
                 "audio/x-mpegurl", "application/dash+xml")


def find_edge() -> str | None:
    """msedge.exe (or Chrome as a fallback), or None."""
    bases = [os.environ.get(v) for v in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    for exe, rel in (("msedge.exe", r"Microsoft\Edge\Application\msedge.exe"),
                     ("chrome.exe", r"Google\Chrome\Application\chrome.exe")):
        if os.name == "nt":
            import winreg
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                        path = (winreg.QueryValue(k, None) or "").strip('"')
                    if path and Path(path).is_file():
                        return path
                except OSError:
                    continue
        for base in bases:
            if base and (Path(base) / rel).is_file():
                return str(Path(base) / rel)
    for name in ("msedge", "microsoft-edge", "chromium", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def is_media(url: str, mime: str = "", rtype: str = "") -> bool:
    """A video file or a stream manifest (never the small pieces a stream is made of)."""
    path = urlparse(url).path.lower()
    mime = (mime or "").split(";")[0].strip().lower()
    if url.startswith(("blob:", "data:")) or path.endswith(SEGMENT_EXT) or mime in ("video/mp2t", "video/iso.segment"):
        return False
    if path.endswith(MEDIA_EXT) or mime in MANIFEST_MIME:
        return True
    return mime.startswith("video/") or (rtype == "Media" and not mime.startswith("audio/"))


@dataclass
class Rendered:
    url: str
    final_url: str = ""
    title: str = ""
    html: str = ""
    media: list[dict] = field(default_factory=list)  # {url, referer, mime}
    videos: list[dict] = field(default_factory=list)  # {src, poster, title, frame}
    links: list[str] = field(default_factory=list)
    frames: list[str] = field(default_factory=list)
    image: str | None = None


class _CDP:
    """Minimal DevTools protocol client: one websocket, a reader thread, commands answered by id."""

    def __init__(self, ws_url: str) -> None:
        from websockets.sync.client import connect
        self.ws = connect(ws_url, max_size=None, open_timeout=10, close_timeout=2).__enter__()
        self._ids = itertools.count(1)
        self._waiting: dict[int, queue.Queue] = {}
        self._lock = threading.Lock()
        self.handlers: list[Callable[[str, dict, str | None], None]] = []
        self.closed = False
        threading.Thread(target=self._read, name="cdp-reader", daemon=True).start()

    def _read(self) -> None:
        try:
            for raw in self.ws:
                msg = json.loads(raw)
                if "id" in msg:
                    q = self._waiting.pop(msg["id"], None)
                    if q:
                        q.put(msg)
                elif "method" in msg:
                    for h in list(self.handlers):
                        try:
                            h(msg["method"], msg.get("params") or {}, msg.get("sessionId"))
                        except Exception as e:  # noqa: BLE001 - a handler bug must not stop the reader
                            log.debug("CDP handler failed: %s", e)
        except Exception:  # noqa: BLE001 - connection closed
            pass
        finally:
            self.closed = True
            for q in list(self._waiting.values()):
                q.put({"error": {"message": "closed"}})

    def send(self, method: str, params: dict | None = None, session: str | None = None,
             wait: bool = True, timeout: float = 15) -> dict:
        mid = next(self._ids)
        msg: dict[str, Any] = {"id": mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        q: queue.Queue = queue.Queue()
        if wait:
            self._waiting[mid] = q
        with self._lock:
            self.ws.send(json.dumps(msg))
        if not wait:
            return {}
        try:
            res = q.get(timeout=timeout)
        except queue.Empty:
            self._waiting.pop(mid, None)
            raise TimeoutError(method) from None
        if "error" in res:
            raise RuntimeError(f"{method}: {res['error'].get('message')}")
        return res.get("result") or {}

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


_SCROLL_JS = """(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  for (let i = 0; i < 10; i++) {
    window.scrollBy(0, Math.max(400, innerHeight * 0.9));
    await sleep(300);
    if (innerHeight + scrollY >= document.documentElement.scrollHeight - 4) break;
  }
  window.scrollTo(0, 0);
  return true;
})()"""

# Start what can be played: <video> elements, then the usual "big play button" of web players.
_PLAY_JS = """(() => {
  let n = 0;
  const docs = [document];
  for (const f of document.querySelectorAll('iframe')) { try { if (f.contentDocument) docs.push(f.contentDocument); } catch (e) {} }
  for (const d of docs) {
    for (const v of d.querySelectorAll('video')) {
      try { v.muted = true; v.preload = 'auto'; const p = v.play(); if (p && p.catch) p.catch(() => {}); n++; } catch (e) {}
    }
    const sel = ['.vjs-big-play-button', '.jw-icon-display', '.jw-display-icon-container', '.plyr__control--overlaid',
      '.fp-play', '.mejs__overlay-button', '.ytp-large-play-button', 'button[aria-label*="play" i]',
      'button[title*="play" i]', '[class*="play-button" i]', '[class*="playbutton" i]', '[class*="btn-play" i]'];
    for (const el of d.querySelectorAll(sel.join(','))) {
      if (el.closest('a[href]')) continue;  // never follow a link
      try { el.click(); n++; } catch (e) {}
      if (n > 40) break;
    }
  }
  return n;
})()"""

_COLLECT_JS = """(() => {
  const abs = u => { try { return new URL(u, location.href).href; } catch (e) { return null; } };
  const vids = [], links = new Set(), frames = [];
  const docs = [[document, location.href]];
  for (const f of document.querySelectorAll('iframe')) {
    const src = f.src || f.getAttribute('data-src');
    if (src) frames.push(abs(src));
    try { if (f.contentDocument) docs.push([f.contentDocument, f.contentWindow.location.href]); } catch (e) {}
  }
  for (const [d, href] of docs) {
    for (const v of d.querySelectorAll('video')) {
      const srcs = [v.currentSrc, v.src, ...[...v.querySelectorAll('source')].map(s => s.src)];
      const title = v.title || v.getAttribute('aria-label') || '';
      for (const s of srcs) if (s) vids.push({src: s, poster: v.poster || '', title, frame: href});
    }
  }
  for (const a of document.querySelectorAll('a[href]')) { const u = abs(a.getAttribute('href')); if (u) links.add(u); }
  const og = document.querySelector('meta[property="og:image"],meta[name="twitter:image"]');
  return {title: document.title, url: location.href, html: document.documentElement.outerHTML.slice(0, 6000000),
          videos: vids, links: [...links].slice(0, 3000), frames: frames.filter(Boolean),
          image: og ? abs(og.content) : null};
})()"""


class Browser:
    """One headless Edge for a whole scan (several pages). Use as a context manager."""

    def __init__(self, exe: str | None = None, temp_root: Path | None = None) -> None:
        self.exe = exe or find_edge()
        if not self.exe:
            raise RuntimeError("No Edge/Chrome browser found")
        root = Path(temp_root) if temp_root else Path(tempfile.gettempdir())
        root.mkdir(parents=True, exist_ok=True)
        self.profile = Path(tempfile.mkdtemp(prefix="se7en-scan-", dir=root))
        self.proc: subprocess.Popen | None = None
        self.cdp: _CDP | None = None

    def __enter__(self) -> "Browser":
        args = [self.exe, "--headless=new", "--remote-debugging-port=0", f"--user-data-dir={self.profile}",
                "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--disable-sync",
                "--mute-audio", "--autoplay-policy=no-user-gesture-required", "--disable-gpu",
                "--disable-background-networking", "--disable-component-update", "--disable-default-apps",
                "--window-size=1366,900", "about:blank"]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, creationflags=flags)
        port_file = self.profile / "DevToolsActivePort"
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if port_file.is_file():
                lines = port_file.read_text("utf-8", errors="replace").split()
                if len(lines) >= 2:
                    self.cdp = _CDP(f"ws://127.0.0.1:{lines[0]}{lines[1]}")
                    return self
            if self.proc.poll() is not None:
                break
            time.sleep(0.1)
        self.__exit__(None, None, None)
        raise RuntimeError("The browser did not start")

    def __exit__(self, *exc: Any) -> None:
        if self.cdp:
            try:
                self.cdp.send("Browser.close", timeout=3)
            except Exception:  # noqa: BLE001
                pass
            self.cdp.close()
        if self.proc:
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        for _ in range(10):  # Edge's helper processes release the profile a moment after exiting
            shutil.rmtree(self.profile, ignore_errors=True)
            if not self.profile.exists():
                break
            time.sleep(0.3)

    # ---------------------------------------------------------------- one page
    def render(self, url: str, cancel: threading.Event | None = None, load_timeout: float = 20,
               listen: float = 2.5, referer: str | None = None) -> Rendered:
        assert self.cdp
        cdp = self.cdp
        out = Rendered(url=url)
        seen: set[str] = set()
        sessions: dict[str, str] = {}  # session id -> target url
        loaded = threading.Event()
        lock = threading.Lock()

        def stop() -> bool:
            return bool(cancel and cancel.is_set()) or cdp.closed

        def note(media_url: str, referer_url: str, mime: str) -> None:
            with lock:
                if media_url not in seen:
                    seen.add(media_url)
                    out.media.append({"url": media_url, "referer": referer_url, "mime": mime})

        def on_event(method: str, p: dict, sid: str | None) -> None:
            if sid is None or sid not in sessions:
                return  # the browser itself, or another page
            if method == "Target.attachedToTarget":
                child = p["sessionId"]
                sessions[child] = p.get("targetInfo", {}).get("url", "")
                for m, prm in (("Network.enable", {}),
                               ("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True,
                                                         "flatten": True}),
                               ("Runtime.runIfWaitingForDebugger", {})):
                    cdp.send(m, prm, session=child, wait=False)
            elif method == "Network.requestWillBeSent":
                req = p.get("request") or {}
                u = req.get("url", "")
                if is_media(u, "", p.get("type", "")):
                    hdr = {k.lower(): v for k, v in (req.get("headers") or {}).items()}
                    note(u, hdr.get("referer") or p.get("documentURL") or sessions.get(sid or "", "") or url, "")
            elif method == "Network.responseReceived":
                r = p.get("response") or {}
                u, mime = r.get("url", ""), r.get("mimeType", "")
                if is_media(u, mime, p.get("type", "")):
                    hdr = {k.lower(): v for k, v in (r.get("requestHeaders") or {}).items()}
                    note(u, hdr.get("referer") or sessions.get(sid or "", "") or url, mime)
            elif method == "Page.loadEventFired" and sid == main:
                loaded.set()

        main = ""
        target = cdp.send("Target.createTarget", {"url": "about:blank"})["targetId"]
        try:
            main = cdp.send("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
            sessions[main] = url
            cdp.handlers.append(on_event)
            for m, prm in (("Page.enable", {}), ("Network.enable", {}),
                           ("Network.setUserAgentOverride", {"userAgent": _user_agent(cdp)}),
                           ("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": True,
                                                     "flatten": True})):
                try:
                    cdp.send(m, prm, session=main)
                except Exception as e:  # noqa: BLE001
                    log.debug("CDP %s failed: %s", m, e)
            nav: dict[str, Any] = {"url": url}
            if referer:
                nav["referrer"] = referer
            res = cdp.send("Page.navigate", nav, session=main, timeout=load_timeout)
            if res.get("errorText"):
                raise RuntimeError(res["errorText"])
            end = time.monotonic() + load_timeout
            while not loaded.wait(0.2) and time.monotonic() < end and not stop():
                pass
            if not stop():
                self._eval(main, _SCROLL_JS, timeout=8)
                for sid in list(sessions):
                    self._eval(sid, _PLAY_JS, timeout=4)
                end = time.monotonic() + listen
                while time.monotonic() < end and not stop():
                    time.sleep(0.1)
                for sid in list(sessions):  # players started late (after the click) get one more chance
                    self._eval(sid, _PLAY_JS, timeout=4)
                time.sleep(min(1.0, listen))
            data = self._eval(main, _COLLECT_JS, timeout=10) or {}
            out.final_url = data.get("url") or url
            out.title = data.get("title") or ""
            out.html = data.get("html") or ""
            out.videos = data.get("videos") or []
            out.links = data.get("links") or []
            out.frames = data.get("frames") or []
            out.image = data.get("image")
            for sid, frame_url in list(sessions.items()):  # out-of-process frames (other sites' players)
                if sid == main:
                    continue
                fdata = self._eval(sid, _COLLECT_JS, timeout=6) or {}
                for v in fdata.get("videos") or []:
                    out.videos.append({**v, "frame": fdata.get("url") or frame_url})
                if fdata.get("url"):
                    out.frames.append(fdata["url"])
        finally:
            if on_event in cdp.handlers:
                cdp.handlers.remove(on_event)
            try:
                cdp.send("Target.closeTarget", {"targetId": target}, timeout=5)
            except Exception:  # noqa: BLE001
                pass
        return out

    def _eval(self, session: str, expr: str, timeout: float) -> Any:
        assert self.cdp
        try:
            r = self.cdp.send("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True,
                                                   "timeout": int(timeout * 1000)}, session=session,
                              timeout=timeout + 2)
            return (r.get("result") or {}).get("value")
        except Exception as e:  # noqa: BLE001 - a frame that went away, a page that blocks scripts...
            log.debug("CDP evaluate failed: %s", e)
            return None


_UA_CACHE: dict[int, str] = {}


def _user_agent(cdp: _CDP) -> str:
    """The browser's own user agent without the "Headless" marker some sites refuse."""
    key = id(cdp)
    if key not in _UA_CACHE:
        try:
            ua = cdp.send("Browser.getVersion").get("userAgent", "")
        except Exception:  # noqa: BLE001
            ua = ""
        _UA_CACHE[key] = ua.replace("Headless", "") or \
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
    return _UA_CACHE[key]
