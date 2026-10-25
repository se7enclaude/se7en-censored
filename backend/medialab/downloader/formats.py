"""Format analysis: quality buckets, size estimates, codec families, Smart Quality.

Pure functions over yt-dlp style format dicts, so they are unit-testable
without network access.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

STANDARD_HEIGHTS = [144, 240, 360, 480, 720, 1080, 1440, 2160, 4320]
QUALITY_LABELS = {144: "144p", 240: "240p", 360: "360p", 480: "480p", 720: "720p",
                  1080: "1080p", 1440: "1440p", 2160: "2160p (4K)", 4320: "4320p (8K)"}

# Which video codecs each container can hold without re-encoding.
CONTAINER_VCODECS = {
    "mp4": ("h264", "h265", "av1", "vp9"),
    "mkv": ("h264", "h265", "av1", "vp9", "vp8", "other"),
    "webm": ("vp9", "av1", "vp8"),
}
CONTAINER_ACODECS = {
    "mp4": ("aac", "mp3", "opus", "flac", "ac3", "eac3"),
    "mkv": ("aac", "opus", "vorbis", "mp3", "flac", "ac3", "eac3", "other"),
    "webm": ("opus", "vorbis"),
}
# Preference order when the user leaves the codec on "Auto": compatibility first.
# VP9 ranks before AV1 in MP4: Windows ships the VP9 decoder, the AV1 one is an optional
# Store extension (without it the default player shows a black picture with sound only).
AUTO_CODEC_ORDER = {
    "mp4": ("h264", "vp9", "av1", "h265"),
    "mkv": ("av1", "vp9", "h265", "h264", "vp8", "other"),
    "webm": ("vp9", "av1", "vp8"),
}
# Codecs every player reads (Windows Media Player, phones, TVs, editing software).
COMPAT_VCODECS = ("h264",)
COMPAT_ACODECS = ("aac", "mp3")
AUDIO_PREF = {"mp4": ("aac", "opus", "mp3"), "webm": ("opus", "vorbis"), "mkv": ("opus", "aac", "vorbis", "mp3")}


def vcodec_family(vcodec: str | None) -> str | None:
    v = (vcodec or "").lower()
    if not v or v == "none":
        return None
    if v.startswith(("avc", "h264")):
        return "h264"
    if v.startswith(("hev", "hvc", "h265", "hevc")):
        return "h265"
    if v.startswith(("av01", "av1")):
        return "av1"
    if v.startswith(("vp09", "vp9")):
        return "vp9"
    if v.startswith(("vp08", "vp8")):
        return "vp8"
    return "other"


def acodec_family(acodec: str | None) -> str | None:
    a = (acodec or "").lower()
    if not a or a == "none":
        return None
    if a.startswith(("mp4a", "aac")):
        return "aac"
    if a.startswith("opus"):
        return "opus"
    if a.startswith("vorbis"):
        return "vorbis"
    if a.startswith(("mp3", "mp4a.40.34")):
        return "mp3"
    if a.startswith("flac"):
        return "flac"
    if a.startswith("ec-3") or a.startswith("eac3"):
        return "eac3"
    if a.startswith("ac-3") or a.startswith("ac3"):
        return "ac3"
    return "other"


def has_video(f: dict) -> bool:
    return f.get("vcodec") not in (None, "none") or (f.get("vcodec") is None and bool(f.get("height")))


def has_audio(f: dict) -> bool:
    ac = f.get("acodec")
    if ac == "none":
        return False
    if ac is None:
        # Unknown: combined formats (e.g. progressive MP4) usually carry audio.
        return not (f.get("vcodec") not in (None, "none") and f.get("video_ext") not in (None, "none")
                    and f.get("audio_ext") == "none")
    return True


def quality_height(f: dict) -> int | None:
    """Bucket a format by the conventional 'p' label (letterbox/portrait aware)."""
    w, h = f.get("width"), f.get("height")
    if not h and not w:
        return None
    if w and h:
        short, long_ = min(w, h), max(w, h)
        eq = max(short, round(long_ * 9 / 16))
    else:
        eq = h or round((w or 0) * 9 / 16)
    return snap_height(eq)


def snap_height(value: int) -> int:
    # Snap to the nearest standard height if within 12 %, else keep the raw value.
    best = min(STANDARD_HEIGHTS, key=lambda s: abs(s - value))
    return best if abs(best - value) <= best * 0.12 else int(value)


def estimate_size(f: dict | None, duration: float | None) -> tuple[int | None, bool]:
    """Return (bytes, is_exact)."""
    if not f:
        return None, False
    if f.get("filesize"):
        return int(f["filesize"]), True
    if f.get("filesize_approx"):
        return int(f["filesize_approx"]), False
    if f.get("tbr") and duration:
        return int(f["tbr"] * 1000 / 8 * duration), False
    return None, False


def _fmt_rank(f: dict, codec_order: Iterable[str]) -> tuple:
    order = list(codec_order)
    fam = vcodec_family(f.get("vcodec")) or "other"
    codec_score = len(order) - order.index(fam) if fam in order else 0
    sdr = 0 if (f.get("dynamic_range") or "SDR").upper() != "SDR" else 1
    return (
        sdr,
        f.get("fps") or 0,
        codec_score,
        1 if f.get("protocol", "https").startswith("http") and "dash" not in (f.get("protocol") or "") else 0,
        f.get("tbr") or f.get("vbr") or 0,
        f.get("quality") or 0,
    )


def _audio_rank(f: dict, pref: Iterable[str]) -> tuple:
    order = list(pref)
    fam = acodec_family(f.get("acodec")) or "other"
    lang_pref = f.get("language_preference") or 0
    return (
        lang_pref,
        0 if "drc" in (f.get("format_id") or "") else 1,
        f.get("abr") or f.get("tbr") or 0,
        len(order) - order.index(fam) if fam in order else 0,
    )


def _pick_audio(pool: list[dict], pref: Iterable[str]) -> dict | None:
    """Best audio; the container's preferred codec wins unless it is clearly worse (<75 % bitrate)."""
    if not pool:
        return None
    best = max(pool, key=lambda f: _audio_rank(f, pref))
    best_abr = best.get("abr") or best.get("tbr") or 0
    for codec in pref:
        cands = [a for a in pool if acodec_family(a.get("acodec")) == codec]
        if cands:
            cand = max(cands, key=lambda f: _audio_rank(f, (codec,)))
            if (cand.get("abr") or cand.get("tbr") or 0) >= 0.75 * best_abr:
                return cand
    return best


def usable(formats: list[dict]) -> list[dict]:
    """Drop storyboards, DRM formats, and formats without URL info."""
    out = []
    for f in formats or []:
        if f.get("has_drm"):
            continue
        if (f.get("format_note") or "").lower() == "storyboard" or f.get("ext") == "mhtml":
            continue
        if f.get("protocol") in ("mhtml",):
            continue
        out.append(f)
    return out


def summarize(info: dict) -> dict[str, Any]:
    """Build the quality table the UI shows (only qualities that really exist)."""
    formats = usable(info.get("formats") or [])
    duration = info.get("duration")
    videos = [f for f in formats if has_video(f)]
    audios = [f for f in formats if has_audio(f) and not has_video(f)]
    best_audio = max(audios, key=lambda f: _audio_rank(f, ("opus", "aac")), default=None)
    best_audio_aac = max((a for a in audios if acodec_family(a.get("acodec")) == "aac"),
                         key=lambda f: _audio_rank(f, ("aac",)), default=None)

    buckets: dict[int, list[dict]] = {}
    for f in videos:
        q = quality_height(f)
        if q:
            buckets.setdefault(q, []).append(f)

    qualities = []
    for q in sorted(buckets, reverse=True):
        fs = buckets[q]
        best = max(fs, key=lambda f: _fmt_rank(f, AUTO_CODEC_ORDER["mkv"]))
        vsize, exact = estimate_size(best, duration)
        audio_for_size = None if has_audio(best) else (best_audio_aac or best_audio)
        asize, aexact = estimate_size(audio_for_size, duration)
        codecs = sorted({vcodec_family(f.get("vcodec")) or "other" for f in fs})
        qualities.append({
            "height": q,
            "label": QUALITY_LABELS.get(q, f"{q}p"),
            "width": best.get("width"),
            "fps": max((f.get("fps") or 0) for f in fs) or None,
            "codecs": codecs,
            "hdr": any((f.get("dynamic_range") or "SDR").upper() != "SDR" for f in fs),
            "size": (vsize or 0) + (asize or 0) if vsize else None,
            "size_exact": exact and (aexact or audio_for_size is None),
            "tbr": best.get("tbr"),
            "sizes_by_codec": {
                c: _bucket_size(fs, c, duration, audio_for_size) for c in codecs
            },
        })

    audio_opts = []
    seen = set()
    for a in sorted(audios, key=lambda f: -(f.get("abr") or f.get("tbr") or 0)):
        fam = acodec_family(a.get("acodec")) or "other"
        abr = int(a.get("abr") or a.get("tbr") or 0)
        if fam == "other" and not abr:
            continue  # unidentifiable stream: never shown as an option
        key = (fam, abr // 16)
        if key in seen:
            continue
        seen.add(key)
        size, exact = estimate_size(a, duration)
        audio_opts.append({"format_id": a.get("format_id"), "codec": fam, "abr": abr or None,
                           "ext": a.get("ext"), "size": size, "size_exact": exact,
                           "language": a.get("language")})

    # Combined-only sources (e.g. some TikTok / X / direct files): audio lives inside video.
    audio_in_video = not audios and any(has_audio(f) for f in videos)
    return {
        "qualities": qualities,
        "audio": audio_opts,
        "audio_codecs": sorted({o["codec"] for o in audio_opts}) or (["embedded"] if audio_in_video else []),
        "has_video": bool(videos),
        "has_audio": bool(audios) or audio_in_video,
        "max_height": qualities[0]["height"] if qualities else None,
        "video_codecs": sorted({c for q in qualities for c in q["codecs"]}),
    }


def _bucket_size(fs: list[dict], codec: str, duration, audio) -> int | None:
    cand = [f for f in fs if (vcodec_family(f.get("vcodec")) or "other") == codec]
    if not cand:
        return None
    best = max(cand, key=lambda f: _fmt_rank(f, (codec,)))
    v, _ = estimate_size(best, duration)
    a, _ = estimate_size(audio, duration) if audio and not has_audio(best) else (0, True)
    return (v or 0) + (a or 0) if v else None


def select(formats: list[dict], *, mode: str = "av", quality: str = "best", container: str = "mp4",
           vcodec: str = "auto", audio_format: str = "best", audio_bitrate: int | None = None) -> dict[str, Any]:
    """Pick concrete format ids for the user's abstract choice.

    ``vcodec``: "compat" (plays everywhere: H.264 in MP4, converted when the source
    has no H.264 at this quality), "auto" (best original codec, never converted) or
    an explicit codec (converted when the source does not offer it).

    Returns {"spec": "<yt-dlp format spec>", "video": fmt|None, "audio": fmt|None,
             "needs_reencode": bool, "reencode_to": str|None, "reencode_reason": str|None,
             "height": int|None}
    """
    formats = usable(formats)
    videos = [f for f in formats if has_video(f)]
    audios = [f for f in formats if has_audio(f) and not has_video(f)]
    result: dict[str, Any] = {"video": None, "audio": None, "needs_reencode": False, "reencode_to": None,
                              "reencode_reason": None, "height": None}

    if mode == "audio":
        pool = audios or [f for f in videos if has_audio(f)]
        if not pool:
            raise LookupError("no audio")
        target = audio_format
        pref = {"m4a": ("aac",), "aac": ("aac",), "opus": ("opus",), "mp3": ("mp3", "aac"),
                "flac": ("flac", "opus", "aac"), "wav": ("flac", "opus", "aac")}.get(target, ("opus", "aac"))
        best = max(pool, key=lambda f: _audio_rank(f, pref))
        # If the exact codec exists at (near) the best bitrate, prefer it to avoid re-encoding.
        same = [f for f in pool if acodec_family(f.get("acodec")) in pref[:1]]
        if same:
            cand = max(same, key=lambda f: _audio_rank(f, pref))
            if (cand.get("abr") or 0) >= 0.8 * (best.get("abr") or 0):
                best = cand
        result["audio"] = best
        result["spec"] = str(best["format_id"])
        return result

    if not videos:
        raise LookupError("no video")

    limit: int | None = None if quality in ("best", "", None) else int(str(quality).rstrip("p"))
    by_q: dict[int, list[dict]] = {}
    for f in videos:
        q = quality_height(f)
        if q:
            by_q.setdefault(q, []).append(f)
    if not by_q:  # no dimension info at all: take the best
        by_q = {0: videos}
    heights = sorted(by_q, reverse=True)
    if limit is None:
        chosen_h = heights[0]
    else:
        le = [h for h in heights if h <= limit]
        chosen_h = le[0] if le else heights[-1]
    pool = by_q[chosen_h]
    result["height"] = chosen_h or None

    allowed = CONTAINER_VCODECS.get(container, CONTAINER_VCODECS["mkv"])
    order = AUTO_CODEC_ORDER.get(container, AUTO_CODEC_ORDER["mkv"])
    compat = vcodec == "compat"
    if compat:
        result["compat"] = container == "mp4"
        exact = [f for f in pool if vcodec_family(f.get("vcodec")) in COMPAT_VCODECS]
        if exact:
            pool = exact
        elif container == "mp4":
            # e.g. YouTube 1440p/4K only exist in VP9/AV1: download the best one, convert to H.264.
            result["reencode_to"] = "h264"
            result["reencode_reason"] = "compat"
    elif vcodec != "auto":
        exact = [f for f in pool if vcodec_family(f.get("vcodec")) == vcodec]
        if exact:
            pool = exact
        else:
            result["reencode_to"] = vcodec
            result["reencode_reason"] = f"{vcodec.upper()} is not offered by the source at this quality"
            order = (vcodec,) + tuple(order)
    compatible = [f for f in pool if (vcodec_family(f.get("vcodec")) or "other") in allowed]
    if compatible:
        pool = compatible
    elif not result["reencode_to"]:
        result["reencode_to"] = "h264" if container == "mp4" else ("vp9" if container == "webm" else None)
        result["reencode_reason"] = f"No source codec fits in {container.upper()}"
    result["needs_reencode"] = bool(result["reencode_to"])
    video = max(pool, key=lambda f: _fmt_rank(f, order))
    result["video"] = video

    if mode == "video":
        result["spec"] = str(video["format_id"]) if not has_audio(video) else f"{video['format_id']}"
        result["strip_audio"] = has_audio(video)
        return result

    if has_audio(video) and not audios:
        result["spec"] = str(video["format_id"])
        return result
    pref = AUDIO_PREF.get(container, AUDIO_PREF["mkv"])
    a_allowed = CONTAINER_ACODECS.get(container, CONTAINER_ACODECS["mkv"])
    if result.get("compat"):
        a_allowed = COMPAT_ACODECS
    a_pool = [a for a in audios if (acodec_family(a.get("acodec")) or "other") in a_allowed] or audios
    if audio_bitrate:
        capped = [a for a in a_pool if (a.get("abr") or 0) <= audio_bitrate * 1.1]
        a_pool = capped or a_pool
    audio = _pick_audio(a_pool, pref)
    result["audio"] = audio
    if audio and not has_audio(video):
        result["spec"] = f"{video['format_id']}+{audio['format_id']}"
    else:
        result["spec"] = str(video["format_id"])
    return result


def smart_recommendation(summary: dict, *, container: str = "mp4", avg_speed_bps: float | None = None,
                         max_seconds: float = 180, max_bytes: int | None = None) -> dict[str, Any]:
    """Explainable recommendation. Returns quality + human-readable reasons.

    Rule: the highest quality whose estimated download time at the user's
    measured average speed stays under ``max_seconds`` and whose codec fits
    the container without re-encoding; ties broken by bitrate-per-pixel so a
    bucket that is only an upscale of a lower one is not preferred.
    """
    qualities = summary.get("qualities") or []
    reasons: list[str] = []
    if not qualities:
        return {"quality": "best", "reasons": ["Audio-only or single-format source"], "codec": "auto"}
    speed = avg_speed_bps or 8 * 1024 * 1024  # 8 MB/s default assumption
    allowed = set(CONTAINER_VCODECS.get(container, ()))
    chosen = None
    for q in qualities:
        fits_codec = any(c in allowed for c in q["codecs"])
        size = q.get("size")
        eta = size / speed if size else 0
        if not fits_codec:
            continue
        if size and eta > max_seconds and q is not qualities[-1]:
            continue
        if max_bytes and size and size > max_bytes:
            continue
        chosen = q
        break
    chosen = chosen or qualities[-1]
    top = qualities[0]
    if chosen is top:
        reasons.append(f"Highest resolution the source offers ({top['label']})")
    else:
        reasons.append(f"{top['label']} would take longer than {int(max_seconds // 60)} min at your speed")
    if chosen.get("size"):
        reasons.append(f"≈ {_human(chosen['size'])}, ≈ {_human_time(chosen['size'] / speed)} at {_human(speed)}/s")
    order = AUTO_CODEC_ORDER.get(container, ())
    codec = next((c for c in order if c in chosen["codecs"]), chosen["codecs"][0] if chosen["codecs"] else "auto")
    reasons.append(f"{codec.upper()} fits {container.upper()} without re-encoding")
    if chosen.get("fps") and chosen["fps"] > 30:
        reasons.append(f"{int(chosen['fps'])} fps available")
    return {"quality": str(chosen["height"]), "label": chosen["label"], "codec": codec, "reasons": reasons}


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _human_time(s: float) -> str:
    s = max(0, int(math.ceil(s)))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"
