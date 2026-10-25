"""Direct links to media files (images, videos, audio) with resumable HTTP."""
from __future__ import annotations

import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from ..errors import Canceled, MediaLabError, classify
from ..logging_setup import get_logger
from ..settings import Settings
from .base import DownloadContext, DownloadResult, SourceProvider, media_info
from .naming import platform_folder, sanitize_component
from .platforms import domain

log = get_logger("download")

MEDIA_EXT = {
    "image": {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif", ".heic"},
    "video": {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi"},
    "audio": {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus"},
}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


def referer_headers(referer: str | None) -> dict[str, str]:
    """Referer/Origin of the page a video was found on (some servers only serve it to that page)."""
    if not referer or not referer.startswith(("http://", "https://")):
        return {}
    p = urlparse(referer)
    return {"Referer": referer, "Origin": f"{p.scheme}://{p.netloc}"}


def _ext_kind(url: str) -> str | None:
    ext = Path(urlparse(url).path).suffix.lower()
    for kind, exts in MEDIA_EXT.items():
        if ext in exts:
            return kind
    return None


def _kind_from_ctype(ctype: str) -> str | None:
    ctype = (ctype or "").split(";")[0].strip().lower()
    for k in ("image", "video", "audio"):
        if ctype.startswith(k + "/"):
            return k
    return None


class DirectProvider(SourceProvider):
    id = "direct"
    name = "Direct link"
    priority = 20  # before yt-dlp, but only claims obvious media URLs

    def can_handle(self, url: str) -> bool:
        return url.startswith(("http://", "https://")) and _ext_kind(url) is not None

    def detect(self, url: str) -> dict | None:
        kind = _ext_kind(url)
        if not kind:
            return None
        return {"platform": "direct", "platform_name": domain(url) or "Direct", "extractor": "direct", "kind": kind}

    def probe_url(self, url: str) -> dict[str, Any] | None:
        """HEAD/GET probe used as a fallback when yt-dlp does not support a URL."""
        try:
            with httpx.Client(follow_redirects=True, timeout=15, headers={"User-Agent": UA}) as c:
                r = c.head(url)
                if r.status_code >= 400 or not r.headers.get("content-type"):
                    r = c.get(url, headers={"Range": "bytes=0-0"})
                kind = _kind_from_ctype(r.headers.get("content-type", ""))
                if not kind:
                    return None
                size = r.headers.get("content-length")
                if r.status_code == 206:
                    m = re.search(r"/(\d+)$", r.headers.get("content-range", ""))
                    size = m.group(1) if m else None
                return {"kind": kind, "size": int(size) if size else None,
                        "ctype": r.headers.get("content-type"), "final_url": str(r.url),
                        "resumable": r.headers.get("accept-ranges") == "bytes" or r.status_code == 206}
        except httpx.HTTPError:
            return None

    def analyze(self, url: str, settings: Settings) -> dict[str, Any]:
        p = self.probe_url(url)
        if not p:
            raise MediaLabError("unsupported_url", "This link does not point to a media file.",
                                "Paste the page URL of the video, or a direct image/video link.")
        name = unquote(Path(urlparse(p["final_url"]).path).name) or "download"
        info = media_info(
            url=url, webpage_url=url, provider=self.id, platform="direct",
            platform_name=domain(url) or "Direct", title=Path(name).stem, kind=p["kind"],
            ext=Path(name).suffix.lstrip(".") or (mimetypes.guess_extension(p["ctype"] or "") or "").lstrip("."),
            size=p["size"], thumbnail=url if p["kind"] == "image" else None,
            has_video=p["kind"] == "video", has_audio=p["kind"] in ("video", "audio"),
        )
        if not p["resumable"]:
            info["warnings"].append("The server does not support resuming; an interrupted download restarts.")
        return info

    def download(self, url: str, options: dict[str, Any], ctx: DownloadContext) -> DownloadResult:
        d = ctx.settings.download
        out_root = Path(options.get("output_dir") or d.default_folder).expanduser()
        sub = platform_folder("direct", d.platform_folders)
        folder = out_root / sub if "{platform}" in (options.get("folder_structure", d.folder_structure) or "") else out_root
        folder.mkdir(parents=True, exist_ok=True)
        name = unquote(Path(urlparse(url).path).name) or "download"
        stem = sanitize_component(options.get("title") or Path(name).stem, 150)
        ext = Path(name).suffix or ".bin"
        dest = folder / f"{stem}{ext}"
        n = 1
        while dest.exists():
            dest = folder / f"{stem} ({n}){ext}"
            n += 1
        part = ctx.temp_dir / f"{ctx.job_id}.part"
        ctx.temp_dir.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent": UA, **referer_headers(options.get("referer"))}
        done = part.stat().st_size if part.exists() else 0
        if done:
            headers["Range"] = f"bytes={done}-"
        t_last, bytes_last = time.monotonic(), done
        speed = 0.0
        try:
            with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(20, read=60)) as c, \
                    c.stream("GET", url, headers=headers) as r:
                if r.status_code == 416:  # already complete
                    pass
                else:
                    if r.status_code >= 400:
                        raise MediaLabError("http_error", f"The server answered HTTP {r.status_code}.",
                                            "The link may have expired.", r.status_code >= 500)
                    if done and r.status_code != 206:
                        done = 0  # server ignored Range: restart
                    total = int(r.headers.get("content-length") or 0) + done or None
                    with open(part, "ab" if done else "wb") as f:
                        for chunk in r.iter_bytes(1 << 18):
                            if ctx.cancel.is_set():
                                raise Canceled()
                            f.write(chunk)
                            done += len(chunk)
                            now = time.monotonic()
                            if now - t_last >= 0.3:
                                speed = (done - bytes_last) / (now - t_last)
                                t_last, bytes_last = now, done
                                ctx.progress({"stage": "downloading", "downloaded": done, "total": total,
                                              "percent": done / total * 100 if total else None, "speed": speed,
                                              "eta": (total - done) / speed if total and speed else None,
                                              "connections": 1})
        except (Canceled, MediaLabError):
            raise
        except (httpx.HTTPError, OSError) as e:
            raise classify(e) from e
        os.replace(part, dest)
        return DownloadResult(file_path=str(dest), title=stem, platform="direct", size=dest.stat().st_size,
                              thumbnail=url if _ext_kind(url) == "image" else None,
                              format=ext.lstrip(".").upper(), meta={"platform_name": domain(url)})
