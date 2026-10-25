"""Thumbnail cache.

Remote thumbnails are fetched by the backend (sites often refuse hotlinking
and URLs expire), stored on disk, and served to the UI by id. Local media
files get generated thumbnails (FFmpeg for videos, Pillow for images).
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import httpx

from .components import CREATE_NO_WINDOW, find_binary
from .config import paths
from .logging_setup import get_logger

log = get_logger("app")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
MAX_BYTES = 8 * 1024 * 1024


def _key(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", "replace")).hexdigest()[:24]


def path_for(key: str) -> Path | None:
    if not key.isalnum():
        return None
    for ext in (".jpg", ".png", ".webp"):
        p = paths().thumbnails / f"{key}{ext}"
        if p.exists():
            return p
    return None


def cache_remote(url: str | None) -> str | None:
    """Download a remote thumbnail once; returns the cache key."""
    if not url or not url.startswith(("http://", "https://")):
        return None
    key = _key(url)
    if path_for(key):
        return key
    try:
        with httpx.Client(follow_redirects=True, timeout=10, headers={"User-Agent": UA}) as c:
            r = c.get(url)
            r.raise_for_status()
            if len(r.content) > MAX_BYTES:
                return None
            ctype = r.headers.get("content-type", "")
            ext = ".png" if "png" in ctype else ".webp" if "webp" in ctype else ".jpg"
            (paths().thumbnails / f"{key}{ext}").write_bytes(r.content)
            return key
    except (httpx.HTTPError, OSError) as e:
        log.debug("thumbnail fetch failed for %s: %s", url, e)
        return None


def cache_local(file_path: str | Path, at: float = 1.0, size: int = 480) -> str | None:
    """Thumbnail for a local image or video file; returns the cache key."""
    p = Path(file_path)
    if not p.exists():
        return None
    try:
        st = p.stat()
        key = _key(f"{p.resolve()}|{st.st_mtime_ns}|{st.st_size}")
    except OSError:
        return None
    if path_for(key):
        return key
    out = paths().thumbnails / f"{key}.jpg"
    from .media.ffmpeg import media_kind
    kind = media_kind(p)
    try:
        if kind == "image":
            from PIL import Image
            with Image.open(p) as im:
                im.thumbnail((size, size))
                im.convert("RGB").save(out, "JPEG", quality=85)
            return key
        ffmpeg = find_binary("ffmpeg")
        if not ffmpeg:
            return None
        for t in (at, 0):
            subprocess.run([ffmpeg, "-hide_banner", "-v", "error", "-y", "-ss", str(t), "-i", str(p),
                            "-frames:v", "1", "-vf", f"scale={size}:-2", str(out)],
                           capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
            if out.exists():
                return key
    except Exception as e:
        log.debug("local thumbnail failed for %s: %s", p, e)
    return None
