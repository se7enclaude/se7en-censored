"""Provider registry: picks the right engine for a URL, with automatic fallback."""
from __future__ import annotations

import re
from typing import Any

from ..errors import MediaLabError, classify
from ..logging_setup import get_logger
from ..settings import Settings
from .base import SourceProvider
from .direct_provider import DirectProvider
from .gallery_provider import GalleryProvider
from .platforms import domain
from .ytdlp_provider import YtDlpProvider

log = get_logger("download")

URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.I)
_NO_VIDEO_HINTS = ("no video", "no media", "unsupported url", "there's no video", "no suitable",
                   "does not contain", "only images", "no formats")


def extract_urls(text: str) -> list[str]:
    """All http(s) URLs in pasted text (one per line, comma separated, mixed with text...)."""
    seen: dict[str, None] = {}
    for m in URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(").,;]}>'\"")
        seen.setdefault(url, None)
    return list(seen)


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: list[SourceProvider] = []
        for p in (DirectProvider(), YtDlpProvider(), GalleryProvider()):
            self.register(p)

    def register(self, provider: SourceProvider) -> None:
        self._providers = sorted([*self._providers, provider], key=lambda p: -p.priority)
        log.debug("Provider registered: %s", provider.id)

    def get(self, provider_id: str) -> SourceProvider:
        for p in self._providers:
            if p.id == provider_id:
                return p
        raise MediaLabError("unknown_provider", f"Download engine “{provider_id}” is not available.")

    @property
    def providers(self) -> list[SourceProvider]:
        return list(self._providers)

    def detect(self, url: str) -> dict[str, Any]:
        for p in self._providers:
            try:
                if p.can_handle(url):
                    d = p.detect(url)
                    if d:
                        return {**d, "provider": p.id, "url": url}
            except Exception as e:  # detection must never fail loudly
                log.debug("detect via %s failed: %s", p.id, e)
        return {"platform": domain(url).split(".")[0] if domain(url) else "web",
                "platform_name": domain(url) or "Web", "extractor": "generic", "provider": "ytdlp", "url": url}

    def analyze(self, url: str, settings: Settings) -> dict[str, Any]:
        errors: list[MediaLabError] = []
        for p in self._providers:
            if not p.can_handle(url):
                continue
            try:
                info = p.analyze(url, settings)
                if errors:
                    info.setdefault("warnings", []).append(f"Analyzed with {p.name} after {errors[0].code}.")
                return info
            except MediaLabError as e:
                errors.append(e)
                log.info("%s could not analyze %s: %s", p.id, url, e)
                if not self._should_fallback(e):
                    break
            except Exception as e:  # unexpected provider bug: try the next one
                log.exception("Provider %s crashed while analyzing", p.id)
                errors.append(classify(e))
        # Last resort: a direct media file behind a URL without extension.
        direct = self.get("direct")
        if isinstance(direct, DirectProvider) and not direct.can_handle(url) and \
                (not errors or self._should_fallback(errors[0])):
            try:
                return direct.analyze(url, settings)
            except MediaLabError:
                pass
            # ...or a web page no engine knows: every video it shows or links to
            try:
                from .page_scan import analyze_page
                return analyze_page(url)
            except MediaLabError:
                pass
        raise errors[0] if errors else MediaLabError("unsupported_url", "This link is not supported.")

    @staticmethod
    def _should_fallback(e: MediaLabError) -> bool:
        text = f"{e.code} {e.detail} {e.message}".lower()
        return e.code in ("unsupported_url", "unknown", "format_unavailable", "no_media") or \
            any(h in text for h in _NO_VIDEO_HINTS)


registry = ProviderRegistry()
