"""Cosmetic platform identity (key + display name) derived from extractor keys.

This only affects labels/colors/folders — download support itself comes from
the engine, so unknown platforms still work and get a generic identity.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# extractor-key prefix -> (platform key, display name)
_FAMILIES: list[tuple[str, str, str]] = [
    ("youtube", "youtube", "YouTube"),
    ("tiktok", "tiktok", "TikTok"),
    ("douyin", "tiktok", "TikTok"),
    ("twitter", "twitter", "X"),
    ("instagram", "instagram", "Instagram"),
    ("facebook", "facebook", "Facebook"),
    ("reddit", "reddit", "Reddit"),
    ("vimeo", "vimeo", "Vimeo"),
    ("twitch", "twitch", "Twitch"),
    ("dailymotion", "dailymotion", "Dailymotion"),
    ("soundcloud", "soundcloud", "SoundCloud"),
    ("bilibili", "bilibili", "Bilibili"),
    ("bandcamp", "bandcamp", "Bandcamp"),
    ("kick", "kick", "Kick"),
    ("rumble", "rumble", "Rumble"),
    ("pinterest", "pinterest", "Pinterest"),
    ("tumblr", "tumblr", "Tumblr"),
    ("imgur", "imgur", "Imgur"),
    ("linkedin", "linkedin", "LinkedIn"),
    ("threads", "threads", "Threads"),
    ("bluesky", "bluesky", "Bluesky"),
    ("streamable", "streamable", "Streamable"),
    ("mixcloud", "mixcloud", "Mixcloud"),
    ("niconico", "niconico", "Niconico"),
    ("vk", "vk", "VK"),
]


def identify(extractor_key: str | None, url: str | None = None) -> tuple[str, str]:
    k = (extractor_key or "").lower()
    for prefix, key, name in _FAMILIES:
        if k.startswith(prefix):
            return key, name
    if k in ("", "generic", "html5mediaembed"):
        host = domain(url)
        if host:
            base = host.split(".")[-2] if host.count(".") >= 1 else host
            return base.lower(), base.capitalize()
        return "web", "Web"
    clean = re.sub(r"(ie|tab|playlist|user|channel|vod|clips?|live|post|shorts?|search)$", "", k) or k
    return clean, (extractor_key or clean).rstrip("IE")


def domain(url: str | None) -> str:
    try:
        host = urlparse(url or "").hostname or ""
    except ValueError:
        return ""
    return re.sub(r"^(www|m|mobile|music)\.", "", host)
