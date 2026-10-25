"""User-facing error model.

Every failure is converted to a ``MediaLabError`` carrying a stable ``code``
(translated by the UI), a readable English message, a hint telling the user
what to do, whether a retry can help, and the raw technical detail for logs.
"""
from __future__ import annotations

import errno
import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class MediaLabError(Exception):
    code: str
    message: str
    hint: str = ""
    retryable: bool = False
    detail: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Canceled(Exception):
    """Raised inside workers when the user cancels or pauses a job."""

    def __init__(self, reason: str = "canceled"):
        super().__init__(reason)
        self.reason = reason  # "canceled" | "paused"


# (code, regex, message, hint, retryable)
_RULES: list[tuple[str, str, str, str, bool]] = [
    ("ffmpeg_missing", r"ffmpeg (is )?not (found|installed)|ffprobe (and ffmpeg )?not found|ffmpeg-location",
     "FFmpeg is required for this operation but was not found.",
     "Install it from Settings › Components (one click).", False),
    ("drm", r"drm|widevine|playready|fairplay",
     "This content is protected by DRM.",
     "DRM-protected media cannot be downloaded. se7en does not bypass DRM.", False),
    ("private", r"private video|this video is private|is private|private account",
     "This media is private.",
     "Only the owner or accounts they allowed can access it. If you have access, enable browser cookies in Settings › Privacy.", False),
    ("members_only", r"members[- ]only|join this channel|premium members|subscriber[- ]only|patreon",
     "This media is restricted to members or subscribers.",
     "If your account has access, enable browser cookies in Settings › Privacy.", False),
    ("age_restricted", r"age[- ]restrict|confirm your age|inappropriate for some users|sign in to confirm your age",
     "This media is age-restricted.",
     "Sign in to the site in your browser, then enable browser cookies in Settings › Privacy.", False),
    ("login_required", r"login required|log in|sign in|authentication|cookies|--cookies|not a bot|rate-limit reached or login",
     "The site requires you to be signed in to access this media.",
     "Sign in to the site in your browser, then enable browser cookies in Settings › Privacy.", True),
    ("geo_blocked", r"geo.?restrict|available in your country|geo.?block|from your location|in your region",
     "This media is not available in your region.",
     "The platform restricts this content geographically.", False),
    ("live_not_ready", r"live event will begin|premieres in|this live event|is upcoming",
     "This live stream or premiere has not started yet.",
     "Try again once it has started or finished.", True),
    ("removed", r"removed|deleted|terminated|no longer available|has been blocked|copyright",
     "This media has been removed or is no longer available.",
     "Check that the link still works in your browser.", False),
    ("unsupported_url", r"unsupported url|no suitable extractor|not a valid url|is not a valid",
     "This link is not supported.",
     "Check the URL. Only public media pages or direct media links are supported.", False),
    ("format_unavailable", r"requested format is not available|no video formats found|format not available",
     "The requested quality/format is not available for this media.",
     "Choose another quality or use “Best”.", True),
    ("rate_limited", r"http error 429|too many requests|rate.?limit",
     "The platform is rate-limiting requests.",
     "Wait a few minutes before retrying, or reduce concurrent downloads.", True),
    ("forbidden", r"http error 403|forbidden",
     "Access was refused by the server (HTTP 403).",
     "Media links can expire; se7en will re-analyze and retry. If it persists, update the engine in Settings › Components.", True),
    ("not_found", r"http error 404|not found",
     "The media was not found (HTTP 404).",
     "The link may be broken or the media deleted.", False),
    ("server_error", r"http error 5\d\d",
     "The platform's server returned an error.",
     "This is usually temporary. Retry in a moment.", True),
    ("network", r"getaddrinfo|name resolution|connection (reset|refused|aborted)|network is unreachable|"
                r"timed out|timeout|ssl|urlopen error|remote end closed|incomplete read|connectionerror",
     "Network error while contacting the platform.",
     "Check your internet connection. The download will resume from where it stopped.", True),
    ("disk_full", r"no space left|disk full|errno 28|not enough space",
     "Not enough disk space.",
     "Free up space or choose another output folder.", True),
    ("permission_denied", r"permission denied|access is denied|errno 13|winerror 5\b",
     "se7en cannot write to the output folder.",
     "Choose another folder, or close the program that is using the file.", True),
    ("path_too_long", r"filename too long|path too long|winerror 206|errno 36",
     "The output file path is too long for Windows.",
     "Use a shorter naming template or a shorter output folder.", True),
    ("extractor_broken", r"unable to extract|failed to parse|please report this issue|nsig|signature",
     "The platform changed how it delivers media and the engine could not read it.",
     "Update the download engine in Settings › Components, then retry.", True),
]


def classify(exc: BaseException | str, default_code: str = "unknown") -> MediaLabError:
    if isinstance(exc, MediaLabError):
        return exc
    text = str(exc) or exc.__class__.__name__
    detail = _strip_ansi(text)
    low = detail.lower()
    if isinstance(exc, OSError) and not isinstance(exc, (ConnectionError, TimeoutError)):
        if exc.errno == errno.ENOSPC:
            return _make("disk_full", detail)
        if exc.errno in (errno.EACCES, errno.EPERM):
            return _make("permission_denied", detail)
    for code, pattern, message, hint, retryable in _RULES:
        if re.search(pattern, low):
            return MediaLabError(code, message, hint, retryable, detail)
    return MediaLabError(
        default_code,
        _first_line(detail) or "An unexpected error occurred.",
        "Open Logs for technical details. You can retry the task.",
        True,
        detail,
    )


def _make(code: str, detail: str) -> MediaLabError:
    for c, _p, message, hint, retryable in _RULES:
        if c == code:
            return MediaLabError(code, message, hint, retryable, detail)
    return MediaLabError(code, detail)


def _strip_ansi(s: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", s)


def _first_line(s: str) -> str:
    line = s.strip().splitlines()[0] if s.strip() else ""
    line = re.sub(r"^(ERROR|WARNING):\s*", "", line)
    line = re.sub(r"^\[[\w:]+\]\s*[\w-]+:\s*", "", line)
    return line[:300]


def component_missing(name: str) -> MediaLabError:
    return MediaLabError(
        f"{name}_missing" if name == "ffmpeg" else "component_missing",
        f"The component “{name}” is not installed.",
        "Install it from Settings › Components (one click).",
        False,
        extra={"component": name},
    )
