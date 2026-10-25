"""Image posts & galleries through gallery-dl (optional dependency).

Used when yt-dlp finds no video in a post (e.g. X/Instagram/Reddit image
posts, Imgur albums, Pinterest pins...). gallery-dl's configuration is
process-global, so gallery downloads are serialized with a lock.
"""
from __future__ import annotations

import io
import threading
from pathlib import Path
from typing import Any

from ..errors import Canceled, MediaLabError, classify
from ..logging_setup import get_logger
from ..settings import Settings
from .base import DownloadContext, DownloadResult, SourceProvider, media_info
from .cookies import ytdlp_cookie_options
from .naming import platform_folder
from .platforms import identify

log = get_logger("download")
_lock = threading.Lock()

try:
    from gallery_dl import config as gconfig
    from gallery_dl import extractor as gextractor
    from gallery_dl import job as gjob
    AVAILABLE = True
except Exception:  # pragma: no cover
    AVAILABLE = False


def _configure(settings: Settings, cookie_opts: dict, base_dir: str | None = None) -> None:
    gconfig.clear()
    gconfig.set(("extractor",), "timeout", 20)
    gconfig.set(("extractor",), "retries", settings.download.max_retries)
    gconfig.set(("extractor",), "directory", [])
    gconfig.set(("output",), "mode", "null")
    if base_dir:
        gconfig.set(("extractor",), "base-directory", base_dir)
    if "cookiefile" in cookie_opts:
        gconfig.set(("extractor",), "cookies", cookie_opts["cookiefile"])
    elif "cookiesfrombrowser" in cookie_opts:
        gconfig.set(("extractor",), "cookies", [cookie_opts["cookiesfrombrowser"][0]])


class GalleryProvider(SourceProvider):
    id = "gallery"
    name = "gallery-dl"
    priority = 5  # asked after yt-dlp

    def can_handle(self, url: str) -> bool:
        if not AVAILABLE or not url.startswith(("http://", "https://")):
            return False
        try:
            ex = gextractor.find(url)
        except Exception:
            return False
        return ex is not None and ex.category not in ("directlink", "generic", "recursive")

    def detect(self, url: str) -> dict | None:
        if not AVAILABLE:
            return None
        ex = gextractor.find(url)
        if not ex:
            return None
        key, name = identify(ex.category, url)
        return {"platform": key, "platform_name": name, "extractor": f"gallery-dl:{ex.category}"}

    def analyze(self, url: str, settings: Settings) -> dict[str, Any]:
        with _lock, ytdlp_cookie_options(settings) as cookie_opts:
            _configure(settings, cookie_opts)
            j = gjob.DataJob(url, file=io.StringIO())
            j.run()
        if j.exception:
            raise classify(j.exception)
        files = [m for m in j.data if m and m[0] == 3]  # Message.Url
        if not files:
            raise MediaLabError("no_media", "No images or videos were found at this link.",
                                "The post may be private, deleted, or contain only text.")
        first = files[0][2]
        cat = first.get("category") or ""
        key, name = identify(cat, url)
        images = [f for f in files if (f[2].get("extension") or "").lower() in ("jpg", "jpeg", "png", "webp", "gif")]
        entries = [{"url": f[1], "title": f[2].get("filename"), "thumbnail": f[1] if f in images else None,
                    "ext": f[2].get("extension"), "width": f[2].get("width"), "height": f[2].get("height")}
                   for f in files[:200]]
        title = (first.get("title") or first.get("description") or first.get("content")
                 or first.get("filename") or "Gallery")
        return media_info(
            url=url, webpage_url=url, provider=self.id, platform=key, platform_name=name,
            title=str(title)[:200], uploader=first.get("author", {}).get("name") if isinstance(first.get("author"), dict)
            else first.get("author") or first.get("user") or first.get("username"),
            kind="gallery" if len(files) > 1 else ("image" if images else "video"),
            thumbnail=images[0][1] if images else None, entries=entries, entry_count=len(files),
            width=first.get("width"), height=first.get("height"), ext=first.get("extension"),
        )

    def download(self, url: str, options: dict[str, Any], ctx: DownloadContext) -> DownloadResult:
        d = ctx.settings.download
        out_root = Path(options.get("output_dir") or d.default_folder).expanduser()
        folder = out_root / platform_folder(options.get("platform"), d.platform_folders, options.get("platform_name"))
        folder.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        total = options.get("entry_count") or 0

        class _Job(gjob.DownloadJob):
            def handle_url(self, u, kwdict):
                if ctx.cancel.is_set():
                    raise Canceled()
                super().handle_url(u, kwdict)
                p = getattr(self.pathfmt, "path", None)
                if p and Path(p).exists():
                    saved.append(p)
                ctx.progress({"stage": "downloading", "downloaded": len(saved), "total": total or None,
                              "percent": len(saved) / total * 100 if total else None, "unit": "files",
                              "connections": 1})

        with _lock, ytdlp_cookie_options(ctx.settings) as cookie_opts:
            _configure(ctx.settings, cookie_opts, str(folder))
            try:
                status = _Job(url).run()
            except Canceled:
                raise
        if ctx.cancel.is_set():
            raise Canceled()
        if not saved:
            raise MediaLabError("no_media", "Nothing could be downloaded from this link.",
                                "Check Logs; the post may require being signed in.", True, f"gallery-dl status {status}")
        first = Path(saved[0])
        return DownloadResult(
            file_path=str(first) if len(saved) == 1 else str(first.parent),
            title=options.get("title"), platform=options.get("platform"),
            thumbnail=options.get("thumbnail"), size=sum(Path(s).stat().st_size for s in saved),
            format=first.suffix.lstrip(".").upper() if len(saved) == 1 else f"{len(saved)} files",
            extra_files=saved, meta={"files": len(saved), "platform_name": options.get("platform_name")},
        )

