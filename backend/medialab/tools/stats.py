"""Usage statistics computed from the history (nothing is sent anywhere)."""
from __future__ import annotations

import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..db import Database

KINDS = ("download", "upscale", "convert", "trim", "studio", "subtitles", "ai")


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def compute(db: Database, days: int = 30, check_files: bool = True) -> dict[str, Any]:
    items = db.all_history()
    now = time.time()
    days = max(7, min(365, int(days)))
    since = now - days * 86400
    by_kind: dict[str, dict[str, float]] = {k: {"count": 0, "bytes": 0, "seconds": 0.0} for k in KINDS}
    platforms: dict[str, dict[str, Any]] = {}
    formats: Counter[str] = Counter()
    resolutions: Counter[str] = Counter()
    uploaders: Counter[str] = Counter()
    daily: dict[str, dict[str, int]] = defaultdict(lambda: {k: 0 for k in KINDS})
    daily_bytes: dict[str, int] = defaultdict(int)
    hours: Counter[int] = Counter()
    media_seconds = 0.0
    speeds: list[float] = []
    biggest = None
    longest = None
    on_disk = 0
    missing = 0
    active_days: set[str] = set()

    for it in items:
        kind = it.get("kind") or "download"
        if kind not in by_kind:
            by_kind[kind] = {"count": 0, "bytes": 0, "seconds": 0.0}
        size = int(it.get("size") or 0)
        meta = it.get("meta") or {}
        k = by_kind[kind]
        k["count"] += 1
        k["bytes"] += size
        elapsed = float(meta.get("elapsed") or 0)
        k["seconds"] += elapsed
        ts = float(it.get("created_at") or now)
        active_days.add(_day(ts))
        hours[datetime.fromtimestamp(ts).hour] += 1
        if ts >= since:
            daily[_day(ts)][kind] = daily[_day(ts)].get(kind, 0) + 1
            daily_bytes[_day(ts)] += size
        if kind == "download":
            pname = meta.get("platform_name") or (it.get("platform") or "web").title()
            p = platforms.setdefault(it.get("platform") or "web", {"name": pname, "count": 0, "bytes": 0})
            p["count"] += 1
            p["bytes"] += size
            if meta.get("uploader"):
                uploaders[str(meta["uploader"])] += 1
            if elapsed > 0.5 and size:
                speeds.append(size / elapsed)
            media_seconds += float(it.get("duration") or 0)
        fmt = (it.get("format") or "").split("·")[0].strip().split(" ")[0]
        if fmt:
            formats[fmt.upper()] += 1
        if it.get("resolution"):
            resolutions[str(it["resolution"])] += 1
        if size and (not biggest or size > biggest["size"]):
            biggest = {"title": it.get("title"), "size": size, "kind": kind}
        dur = float(it.get("duration") or 0)
        if dur and (not longest or dur > longest["duration"]):
            longest = {"title": it.get("title"), "duration": dur, "kind": kind}
        if check_files and it.get("file_path"):
            try:
                p_ = Path(it["file_path"])
                if p_.is_file():
                    on_disk += p_.stat().st_size
                else:
                    missing += 1
            except OSError:
                missing += 1

    timeline = []
    for i in range(days - 1, -1, -1):
        d = (datetime.fromtimestamp(now) - timedelta(days=i)).strftime("%Y-%m-%d")
        row = daily.get(d, {k: 0 for k in KINDS})
        timeline.append({"day": d, **{k: row.get(k, 0) for k in KINDS}, "bytes": daily_bytes.get(d, 0)})
    # streak of consecutive active days ending today (or yesterday)
    streak = 0
    cur = datetime.fromtimestamp(now)
    if _day(now) not in active_days:
        cur -= timedelta(days=1)
    while cur.strftime("%Y-%m-%d") in active_days:
        streak += 1
        cur -= timedelta(days=1)
    total = sum(v["count"] for v in by_kind.values())
    speeds.sort()
    return {
        "total": total,
        "by_kind": by_kind,
        "platforms": sorted(({"id": k, **v} for k, v in platforms.items()), key=lambda x: -x["count"])[:12],
        "formats": [{"name": n, "count": c} for n, c in formats.most_common(10)],
        "resolutions": [{"name": n, "count": c} for n, c in resolutions.most_common(8)],
        "uploaders": [{"name": n, "count": c} for n, c in uploaders.most_common(8)],
        "timeline": timeline,
        "hours": [hours.get(h, 0) for h in range(24)],
        "media_seconds": media_seconds,
        "median_speed": speeds[len(speeds) // 2] if speeds else None,
        "biggest": biggest,
        "longest": longest,
        "on_disk": on_disk,
        "missing": missing,
        "active_days": len(active_days),
        "streak": streak,
        "first": min((float(i.get("created_at") or now) for i in items), default=None),
        "days": days,
    }
