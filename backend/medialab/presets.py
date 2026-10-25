"""Download presets (applied automatically by platform when enabled)."""
from __future__ import annotations

from typing import Any

from .db import Database

PRESET_KEYS = ("mode", "quality", "container", "vcodec", "audio_format", "audio_bitrate", "output_dir",
               "naming_template", "folder_structure", "embed_metadata", "embed_thumbnail", "embed_chapters",
               "write_subtitles", "write_description", "image_format")

EXAMPLES = [
    {"name": "My YouTube Preset", "platforms": ["youtube"], "auto_apply": False,
     "options": {"mode": "av", "quality": "best", "container": "mp4", "vcodec": "compat", "embed_metadata": True}},
    {"name": "My TikTok Preset", "platforms": ["tiktok"], "auto_apply": False,
     "options": {"mode": "av", "quality": "best", "container": "mp4", "vcodec": "compat"}},
    {"name": "Music (MP3 320)", "platforms": [], "auto_apply": False,
     "options": {"mode": "audio", "audio_format": "mp3", "audio_bitrate": 320, "embed_thumbnail": True}},
]


def seed(db: Database) -> None:
    if not db.list_presets():
        for p in EXAMPLES:
            db.upsert_preset(p)


def clean_options(options: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in (options or {}).items() if k in PRESET_KEYS and v not in (None, "")}


def match(db: Database, platform: str | None) -> dict | None:
    if not platform:
        return None
    for p in db.list_presets():
        if p.get("auto_apply") and platform.lower() in [x.lower() for x in p.get("platforms") or []]:
            return p
    return None
