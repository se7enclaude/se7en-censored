"""Authenticated access *to content the user already has access to*.

Two opt-in mechanisms, both session-based (no password is ever requested):

* ``browser`` – yt-dlp reads the cookies of a browser profile the user is
  already signed in with (Firefox, Chrome, Edge, Brave, ...).
* ``file``    – the user imports a Netscape ``cookies.txt`` export. It is
  stored encrypted with Windows DPAPI and only decrypted into a private
  temporary file for the duration of one download.

Nothing here bypasses paywalls, DRM, or access controls: the platform decides
what the signed-in session may access.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path
from typing import Iterator

from ..config import paths
from ..errors import MediaLabError
from ..security import protect, unprotect
from ..settings import Settings

SUPPORTED_BROWSERS = ("firefox", "chrome", "edge", "brave", "opera", "vivaldi", "chromium", "safari")
_STORE = "cookies.dpapi"


def _store_path() -> Path:
    return paths().data / _STORE


def import_cookies_file(content: bytes) -> dict:
    text = content.decode("utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if not lines or not all(len(ln.split("\t")) >= 7 for ln in lines[:20]):
        raise MediaLabError("bad_cookies", "This is not a Netscape cookies.txt file.",
                            "Export cookies in “Netscape” format with a cookies.txt browser extension.")
    _store_path().write_bytes(protect(content))
    domains = sorted({ln.split("\t")[0].lstrip(".") for ln in lines})
    return {"stored": True, "count": len(lines), "domains": domains[:50]}


def cookies_file_info() -> dict:
    p = _store_path()
    return {"stored": p.exists(), "size": p.stat().st_size if p.exists() else 0}


def delete_cookies_file() -> None:
    _store_path().unlink(missing_ok=True)


@contextlib.contextmanager
def ytdlp_cookie_options(settings: Settings, force: bool = False) -> Iterator[dict]:
    """Yield yt-dlp params for the configured cookie mode, cleaning up afterwards."""
    mode = settings.privacy.cookies_mode
    if mode == "browser":
        browser = settings.privacy.cookies_browser
        if browser not in SUPPORTED_BROWSERS:
            raise MediaLabError("bad_browser", f"Unsupported browser “{browser}”.")
        yield {"cookiesfrombrowser": (browser, None, None, None)}
        return
    if mode == "file" and _store_path().exists():
        fd, tmp = tempfile.mkstemp(prefix="mlc_", suffix=".txt", dir=paths().temp)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(unprotect(_store_path().read_bytes()))
            yield {"cookiefile": tmp}
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmp)
        return
    yield {}
