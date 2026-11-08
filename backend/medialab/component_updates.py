"""Are the components up to date? Installed version vs. the latest one available.

* ``annotate(items)`` adds to every component of ``components.status()``:
  ``state`` (missing | up_to_date | update_available | restart_required | unknown),
  ``latest`` (newest version known), ``checked_at`` (ISO time of the last check) and ``pinned``
  (version chosen by se7en: it only changes with a se7en update).
* ``check()`` asks GitHub for the newest yt-dlp, FFmpeg and Deno (at most every 12 h unless forced),
  remembers the answer in ``<data>/components-state.json`` and publishes a ``component_updates``
  event the first time a new version shows up, so the window can tell the user.
* ``record_install()`` remembers what se7en itself installed (version + SHA-256 + date): a component
  that is already up to date is never downloaded again.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .config import paths
from .events import bus
from .logging_setup import get_logger

log = get_logger("app")

CHECK_EVERY = 12 * 3600
GITHUB_LATEST = "https://api.github.com/repos/{repo}/releases/latest"

# Components whose version is pinned (and SHA-256 checked) by se7en: they move with se7en updates.
PINNED = {
    "realesrgan": "20220424",
    "realcugan": "20220728",
    "rife": "20221029",
    "whisper": "b5130",
    "llama": "b11284",
    "aria2": "1.37.0",
}
# Components that follow their project's newest release.
TRACKED = ("ytdlp", "ffmpeg", "deno")

_lock = threading.RLock()
_stop = threading.Event()
_thread: threading.Thread | None = None


# ------------------------------------------------------------------ state file
def _file() -> Path:
    return paths().data / "components-state.json"


def load() -> dict[str, Any]:
    try:
        data = json.loads(_file().read_text("utf-8"))
        if isinstance(data, dict):
            data.setdefault("latest", {})
            data.setdefault("installed", {})
            data.setdefault("notified", {})
            return data
    except (OSError, ValueError):
        pass
    return {"checked_at": None, "latest": {}, "installed": {}, "notified": {}, "errors": {}}


def _save(data: dict[str, Any]) -> None:
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    tmp.replace(f)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_install(cid: str, version: str | None, sha256: str | None = None) -> None:
    """Remember a component se7en installed itself (shown as « installé le … »)."""
    with _lock:
        data = load()
        data["installed"][cid] = {"version": version, "sha256": sha256, "at": _now()}
        _save(data)


def forget_install(cid: str) -> None:
    with _lock:
        data = load()
        if data["installed"].pop(cid, None) is not None:
            _save(data)


def installed_by_se7en(cid: str) -> dict | None:
    return load()["installed"].get(cid)


# ------------------------------------------------------------------ versions
def _nums(v: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def ffmpeg_branch(v: str | None) -> tuple[int, ...] | None:
    """``n7.1.1-12-g…-20250620`` / ``7.1-full_build`` -> (7, 1). Development builds (``N-…``) -> None."""
    m = re.match(r"^n?(\d+)\.(\d+)", (v or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def compare(cid: str, installed: str | None, latest: str | None) -> str:
    """up_to_date | update_available | unknown (version that cannot be compared)."""
    if not installed or not latest:
        return "unknown"
    if cid == "ffmpeg":
        have, want = ffmpeg_branch(installed), ffmpeg_branch(latest)
        if have is None or want is None:
            return "unknown"
        return "up_to_date" if have >= want else "update_available"
    have, want = _nums(installed), _nums(latest)
    if not have or not want:
        return "unknown"
    return "up_to_date" if have >= want else "update_available"


def _client() -> httpx.Client:
    return httpx.Client(follow_redirects=True, timeout=httpx.Timeout(20, read=30),
                        headers={"User-Agent": "se7en-ComponentManager", "Accept": "application/vnd.github+json"})


def _latest_tag(c: httpx.Client, repo: str) -> dict:
    r = c.get(GITHUB_LATEST.format(repo=repo))
    r.raise_for_status()
    return r.json()


def fetch_latest(cid: str, c: httpx.Client) -> str | None:
    if cid == "ytdlp":
        return str(_latest_tag(c, "yt-dlp/yt-dlp").get("tag_name") or "").strip() or None
    if cid == "deno":
        return str(_latest_tag(c, "denoland/deno").get("tag_name") or "").lstrip("v").strip() or None
    if cid == "ffmpeg":
        rel = _latest_tag(c, "BtbN/FFmpeg-Builds")
        branches = []
        for a in rel.get("assets", []):
            m = re.fullmatch(r"ffmpeg-n[\d.]+-latest-win64-gpl-shared-(\d+)\.(\d+)\.zip", a.get("name", ""))
            if m:
                branches.append((int(m.group(1)), int(m.group(2))))
        return "%d.%d" % max(branches) if branches else None
    return None


# ------------------------------------------------------------------ check + notify
def due(data: dict | None = None) -> bool:
    data = data or load()
    ts = data.get("checked_at")
    if not ts:
        return True
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
    except ValueError:
        return True
    return age >= CHECK_EVERY


def check(force: bool = False, items: list[dict] | None = None) -> dict[str, Any]:
    """Refresh the newest versions (network). Never raises: errors are kept per component."""
    with _lock:
        data = load()
        if not force and not due(data):
            return data
    latest: dict[str, str] = {}
    errors: dict[str, str] = {}
    try:
        with _client() as c:
            for cid in TRACKED:
                try:
                    v = fetch_latest(cid, c)
                    if v:
                        latest[cid] = v
                except (httpx.HTTPError, ValueError) as e:
                    errors[cid] = str(e)[:200]
    except httpx.HTTPError as e:  # pragma: no cover - client creation
        errors["*"] = str(e)[:200]
    with _lock:
        data = load()
        data["latest"].update(latest)
        data["errors"] = errors
        if latest:  # offline: keep the previous check date, it is the last one that really happened
            data["checked_at"] = _now()
        _save(data)
    log.info("Component versions checked: %s%s", latest or "-", f" (errors: {list(errors)})" if errors else "")
    _notify_new(items)
    return load()


def _notify_new(items: list[dict] | None) -> None:
    """Publish ``component_updates`` once per new version."""
    if items is None:
        from . import components
        items = components.status()
    fresh = []
    with _lock:
        data = load()
        for it in annotate(items):
            if it.get("state") != "update_available":
                continue
            if data["notified"].get(it["id"]) == it.get("latest"):
                continue
            data["notified"][it["id"]] = it.get("latest")
            fresh.append({"id": it["id"], "name": it["name"], "version": it.get("version"),
                          "latest": it.get("latest")})
        if fresh:
            _save(data)
    if fresh:
        bus.publish("component_updates", {"items": fresh})


def annotate(items: list[dict]) -> list[dict]:
    data = load()
    for it in items:
        cid = it["id"]
        mine = data["installed"].get(cid) or {}
        it["checked_at"] = data.get("checked_at")
        it["installed_at"] = mine.get("at")
        it["pinned"] = cid in PINNED or cid == "aipack"
        if cid == "aipack":
            it["latest"] = None
            it["state"] = "up_to_date" if it.get("installed") else "missing"
            continue
        if not it.get("installed"):
            it["state"] = "missing"
            it["latest"] = PINNED.get(cid) or data["latest"].get(cid)
            continue
        if cid in PINNED:
            it["latest"] = PINNED[cid]
            it["state"] = "up_to_date"  # same pinned build as the SHA-256 in se7en
            continue
        if cid == "deno" and it.get("runtime") not in (None, "deno"):
            it["latest"] = None
            it["state"] = "up_to_date"  # Node / Bun installed on the PC: Windows / its installer updates it
            it["external"] = True
            continue
        latest = data["latest"].get(cid)
        it["latest"] = latest
        if cid == "ytdlp" and it.get("pending_version"):
            pending = it["pending_version"]
            state = compare(cid, pending, latest) if latest else "up_to_date"
            it["state"] = "restart_required" if state == "up_to_date" else state
            continue
        it["state"] = compare(cid, it.get("version"), latest) if latest else "unknown"
    return items


def state_of(cid: str, items: list[dict]) -> str | None:
    for it in annotate([dict(i) for i in items]):
        if it["id"] == cid:
            return it.get("state")
    return None


# ------------------------------------------------------------------ background
def start_background(first_delay: float = 20.0) -> None:
    """Check at launch (when the last check is older than 12 h), then every 12 h."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()

    def loop() -> None:
        if _stop.wait(first_delay):
            return
        while not _stop.is_set():
            try:
                if due():
                    check()
            except Exception:  # never let the checker die
                log.exception("Component update check failed")
            if _stop.wait(3600):
                return

    _thread = threading.Thread(target=loop, name="component-updates", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()

