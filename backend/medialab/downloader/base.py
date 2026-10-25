"""Source provider abstraction.

A provider knows how to analyze and download a family of URLs. Media Lab
ships three:

* ``YtDlpProvider``   – the ~1800 sites supported by yt-dlp (YouTube, TikTok,
                        X, Instagram, Facebook, Reddit, Vimeo, Twitch, ...)
* ``DirectProvider``  – direct links to media files (``.mp4``, ``.jpg`` ...)
* ``GalleryProvider`` – image posts/galleries through gallery-dl (optional)

Plugins can register more providers (see ``medialab.plugins``). Nothing in the
app is hardcoded per platform: providers expose a ``platform`` key and a
display name, the UI styles itself from that.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..settings import Settings


@dataclass
class DownloadContext:
    job_id: str
    settings: Settings
    cancel: threading.Event
    # progress(dict) keys: downloaded, total, speed, eta, connections, stage, fragment
    progress: Callable[[dict[str, Any]], None]
    temp_dir: Path
    attempt: int = 0
    strategy: dict[str, Any] = field(default_factory=dict)


@dataclass
class DownloadResult:
    file_path: str
    title: str | None = None
    platform: str | None = None
    thumbnail: str | None = None
    resolution: str | None = None
    format: str | None = None
    size: int | None = None
    duration: float | None = None
    extra_files: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


class SourceProvider(ABC):
    id: str = "base"
    name: str = "Base"
    priority: int = 0  # higher = asked first

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """Cheap, offline check (regex / extension)."""

    def detect(self, url: str) -> dict[str, Any] | None:
        """Instant platform detection without network access."""
        return None

    @abstractmethod
    def analyze(self, url: str, settings: Settings) -> dict[str, Any]:
        """Return MediaInfo dict (see ``media_info`` below)."""

    @abstractmethod
    def download(self, url: str, options: dict[str, Any], ctx: DownloadContext) -> DownloadResult:
        ...

    def fallback_strategies(self, error_code: str, attempt: int) -> dict[str, Any] | None:
        """Alternative method to try after a failure; None = no more ideas."""
        return None


def media_info(**kw: Any) -> dict[str, Any]:
    """Canonical shape of an analysis result sent to the UI."""
    base = {
        "url": None, "webpage_url": None, "provider": None, "platform": None, "platform_name": None,
        "id": None, "kind": "video",  # video | audio | image | playlist | gallery
        "title": None, "uploader": None, "uploader_url": None, "thumbnail": None,
        "duration": None, "upload_date": None, "description": None, "tags": [],
        "view_count": None, "like_count": None, "is_live": False,
        "width": None, "height": None, "fps": None, "ext": None,
        "qualities": [], "audio": [], "audio_codecs": [], "video_codecs": [],
        "has_video": False, "has_audio": False, "max_height": None,
        "recommended": None, "entries": [], "entry_count": None, "size": None,
        "warnings": [],
    }
    base.update(kw)
    return base
