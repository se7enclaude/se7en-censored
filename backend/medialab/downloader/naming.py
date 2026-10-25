"""File naming and smart folders.

Users can write friendly tokens — ``{title}``, ``{uploader} - {title}``,
``{platform}/{uploader}/{year}/{month}`` — or native yt-dlp templates such as
``%(uploader)s - %(title)s``. Both can be mixed. Windows-forbidden characters
are replaced by yt-dlp (``windowsfilenames``) and by ``sanitize_component``
for literal values we inject ourselves.
"""
from __future__ import annotations

import re
from pathlib import Path

TOKENS: dict[str, str] = {
    "title": "%(title).150B",
    "uploader": "%(uploader,channel,creator,uploader_id|Unknown)s",
    "creator": "%(uploader,channel,creator,uploader_id|Unknown)s",
    "channel": "%(channel,uploader|Unknown)s",
    "date": "%(upload_date>%Y-%m-%d|unknown-date)s",
    "year": "%(upload_date>%Y|0000)s",
    "month": "%(upload_date>%m|00)s",
    "day": "%(upload_date>%d|00)s",
    "id": "%(id)s",
    "resolution": "%(height|)sp",
    "height": "%(height|)s",
    "fps": "%(fps|)s",
    "playlist": "%(playlist_title,playlist|)s",
    "index": "%(playlist_index|)s",
    "ext": "%(ext)s",
}
DEFAULT_PLATFORM_FOLDERS = {
    "youtube": "YouTube", "tiktok": "TikTok", "twitter": "X", "instagram": "Instagram",
    "facebook": "Facebook", "reddit": "Reddit", "vimeo": "Vimeo", "twitch": "Twitch",
    "dailymotion": "Dailymotion", "soundcloud": "SoundCloud", "bilibili": "Bilibili",
    "direct": "Direct",
}

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize_component(name: str, max_len: int = 120) -> str:
    """Make a single path component safe on Windows."""
    s = _FORBIDDEN.sub("_", name or "").strip().rstrip(". ")
    s = re.sub(r"\s+", " ", s)
    if s.upper().split(".")[0] in _RESERVED:
        s = f"_{s}"
    return (s[:max_len].rstrip(". ") or "untitled")


def _escape_literal(s: str) -> str:
    return s.replace("%", "%%")


def platform_folder(platform: str | None, overrides: dict[str, str] | None = None,
                    display_name: str | None = None) -> str:
    key = (platform or "other").lower()
    if overrides and overrides.get(key):
        return overrides[key]
    return DEFAULT_PLATFORM_FOLDERS.get(key) or sanitize_component(display_name or key.title())


def to_ytdlp_template(template: str, *, platform_dir: str) -> str:
    """Translate friendly tokens into a yt-dlp output template."""
    def repl(m: re.Match) -> str:
        name = m.group(1).lower()
        if name == "platform":
            return _escape_literal(sanitize_component(platform_dir))
        return TOKENS.get(name, m.group(0))

    return re.sub(r"\{(\w+)\}", repl, template)


def build_outtmpl(*, folder_structure: str, naming_template: str, platform: str | None,
                  platform_display: str | None = None, platform_overrides: dict | None = None,
                  audio_only: bool = False, image_only: bool = False) -> str:
    """Relative output template ``<folders>/<name>.%(ext)s`` for yt-dlp."""
    pdir = platform_folder(platform, platform_overrides, platform_display)
    parts = [p for p in re.split(r"[\\/]+", folder_structure or "") if p.strip()]
    folders = [to_ytdlp_template(p.strip(), platform_dir=pdir) for p in parts]
    name = to_ytdlp_template((naming_template or "{title}").strip(), platform_dir=pdir)
    name = re.sub(r"\.%\(ext\)s$", "", name)
    # Path separators inside the name part would silently create folders.
    name = name.replace("/", "_").replace("\\", "_")
    if not name.strip():
        name = TOKENS["title"]
    return "/".join([*folders, f"{name}.%(ext)s"])


def preview(template_str: str, folder_structure: str, sample: dict | None = None) -> str:
    """Human preview of a naming template with example values (for Settings)."""
    s = {"title": "Example Video", "uploader": "Creator", "creator": "Creator", "channel": "Creator",
         "date": "2026-09-28", "year": "2026", "month": "09", "day": "28", "id": "dQw4w9WgXcQ",
         "resolution": "1080p", "height": "1080", "fps": "30", "platform": "YouTube",
         "playlist": "My Playlist", "index": "1", "ext": "mp4"}
    s.update(sample or {})

    def fill(t: str) -> str:
        t = re.sub(r"\{(\w+)\}", lambda m: s.get(m.group(1).lower(), m.group(0)), t)
        # Very small subset of yt-dlp syntax for previews: %(field)s / %(field).NB
        t = re.sub(r"%\((\w+)[^)]*\)[\d.]*[sdBf]", lambda m: s.get(m.group(1), m.group(1)), t)
        return t

    folders = [sanitize_component(fill(p)) for p in re.split(r"[\\/]+", folder_structure or "") if p.strip()]
    name = sanitize_component(fill(template_str or "{title}"), 180)
    return str(Path(*folders, f"{name}.{s['ext']}")) if folders else f"{name}.{s['ext']}"
