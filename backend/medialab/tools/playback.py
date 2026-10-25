"""Built-in player support: what the UI can play directly, and live compatibility streams.

The UI is Chromium (WebView2): it natively plays MP4/WebM/OGG/MP3/FLAC/WAV with
H.264, VP8/9, AV1, AAC, Opus, Vorbis, MP3, FLAC and PCM. Everything else (MKV,
AVI, WMV, HEVC, AC-3, DTS, WMA, ALAC, APE...) is played through a live FFmpeg
stream: a lossless *remux* when only the container is the problem, a fast
*transcode* otherwise (GPU encoder when available, 1080p max). Seeking in a
stream restarts FFmpeg at the new position (``t``).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterator

import numpy as np

from ..components import CREATE_NO_WINDOW, require
from ..config import paths
from ..errors import MediaLabError
from ..logging_setup import get_logger
from ..media import ffmpeg

log = get_logger("process")

BROWSER_VCODECS = {"h264", "vp8", "vp9", "av1"}
BROWSER_ACODECS = {"aac", "mp3", "opus", "vorbis", "flac", "pcm_s16le", "pcm_s24le", "pcm_f32le", "pcm_u8", "pcm_s32le"}
DIRECT_VIDEO_EXT = {".mp4", ".m4v", ".webm", ".ogv", ".mov"}
DIRECT_AUDIO_EXT = {".mp3", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wav", ".weba"}

_streams: dict[str, subprocess.Popen] = {}
_streams_lock = threading.Lock()


def _key(path: Path, extra: str = "") -> str:
    st = path.stat()
    return hashlib.sha1(f"{path.resolve()}|{st.st_mtime_ns}|{st.st_size}|{extra}".encode("utf-8", "replace")).hexdigest()[:24]


def plan(path: Path) -> dict:
    """How the player should open a file: direct | remux | transcode | image."""
    if not path.is_file():
        raise MediaLabError("file_missing", "This file no longer exists.", "It may have been moved or deleted.")
    kind = ffmpeg.file_kind(path)
    if kind == "image":
        return {"mode": "image", "kind": "image", "path": str(path), "name": path.name, "size": path.stat().st_size}
    info = ffmpeg.probe(path)
    ext = path.suffix.lower()
    vc = info.get("vcodec")
    acs = [a.get("codec") for a in info.get("audio", [])]
    a0 = acs[0] if acs else None
    a_ok = a0 is None or a0 in BROWSER_ACODECS
    tags = info.get("tags") or {}
    base = {
        "path": str(path), "name": path.name, "size": info.get("size"), "duration": info.get("duration"),
        "kind": "video" if info.get("has_video") else "audio", "width": info.get("width"),
        "height": info.get("height"), "fps": info.get("fps"), "vcodec": vc, "acodec": a0,
        "audio_tracks": len(acs), "has_cover": info.get("cover_index") is not None,
        "title": tags.get("title"), "artist": tags.get("artist") or tags.get("album_artist"),
        "album": tags.get("album"), "is_hdr": info.get("is_hdr"),
    }
    if info.get("has_video"):
        v_ok = vc in BROWSER_VCODECS
        if v_ok and a_ok and ext in DIRECT_VIDEO_EXT:
            mode, fallback = "direct", "remux"
        elif v_ok and ext == ".mkv" and a_ok:
            mode, fallback = "remux", "transcode"
        elif v_ok:
            mode, fallback = "remux", "transcode"  # remux also converts an unsupported sound to AAC
        elif vc == "hevc" and a_ok and ext in DIRECT_VIDEO_EXT:
            mode, fallback = "direct", "transcode"  # plays when the PC has the HEVC decoder
        else:
            mode, fallback = "transcode", None
    else:
        if a_ok and ext in DIRECT_AUDIO_EXT:
            mode, fallback = "direct", "transcode"
        else:
            mode, fallback = "transcode", None
    return {**base, "mode": mode, "fallback": fallback, "hdr_tonemap": bool(info.get("is_hdr"))}


def stream_command(path: Path, mode: str, start: float, info: dict | None = None) -> tuple[list[str], str]:
    """FFmpeg command line and MIME type of a live playback stream."""
    ff = require("ffmpeg")
    info = info or ffmpeg.probe(path)
    base = [ff, "-hide_banner", "-v", "error", "-nostdin"]
    if start > 0:
        base += ["-ss", f"{start:.3f}"]
    base += ["-i", str(path)]
    frag = ["-f", "mp4", "-movflags", "frag_keyframe+empty_moov+default_base_moof", "-frag_duration", "500000"]
    if not info.get("has_video"):
        return base + ["-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-b:a", "256k", "-f", "mp3", "pipe:1"], "audio/mpeg"
    a0 = (info.get("audio") or [{}])[0].get("codec")
    audio = ["-map", "0:a:0?"]
    audio += ["-c:a", "copy"] if a0 in ("aac", "mp3") else ["-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    if mode == "remux" and info.get("vcodec") in BROWSER_VCODECS:
        return base + ["-map", "0:V:0", *audio, "-c:v", "copy", *frag, "pipe:1"], "video/mp4"
    chain = []
    if info.get("is_hdr"):
        from ..upscale.filters import TONEMAP
        chain.append(TONEMAP)
    chain.append("scale='min(1920,iw)':-2")
    chain.append("format=yuv420p")
    enc = ffmpeg.best_encoder("h264") or "libx264"
    if enc.endswith("_nvenc"):
        venc = ["-c:v", enc, "-preset", "p2", "-tune", "ll", "-rc", "vbr", "-cq", "24", "-b:v", "0"]
    elif enc.endswith("_qsv"):
        venc = ["-c:v", enc, "-preset", "veryfast", "-global_quality", "24"]
    elif enc.endswith("_amf"):
        venc = ["-c:v", enc, "-quality", "speed", "-rc", "cqp", "-qp_i", "24", "-qp_p", "24"]
    else:
        venc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-tune", "zerolatency"]
    return base + ["-map", "0:V:0", "-vf", ",".join(chain), *venc, "-g", "48",
                   "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-map", "0:a:0?", *frag, "pipe:1"], "video/mp4"


def open_stream(path: Path, mode: str, start: float, sid: str) -> tuple[Iterator[bytes], str]:
    """Start (or restart, for a seek) the stream of player ``sid``; yields chunks."""
    cmd, mime = stream_command(path, mode, start)
    with _streams_lock:
        old = _streams.pop(sid, None)
        if old and old.poll() is None:
            old.kill()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                creationflags=CREATE_NO_WINDOW, bufsize=0)
        _streams[sid] = proc
    log.debug("playback stream %s (%s) from %.1fs: %s", sid, mode, start, path.name)

    def gen() -> Iterator[bytes]:
        try:
            assert proc.stdout
            while True:
                chunk = proc.stdout.read(256 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            if proc.poll() is None:
                proc.kill()
            with _streams_lock:
                if _streams.get(sid) is proc:
                    _streams.pop(sid, None)
    return gen(), mime


def stop_stream(sid: str) -> None:
    with _streams_lock:
        proc = _streams.pop(sid, None)
    if proc and proc.poll() is None:
        proc.kill()


def stop_all() -> None:
    with _streams_lock:
        procs = list(_streams.values())
        _streams.clear()
    for p in procs:
        if p.poll() is None:
            p.kill()


# ------------------------------------------------------------------ waveform
def waveform(path: Path, buckets: int = 1600) -> dict:
    """Peak envelope (0..1) of the first sound track, cached on disk."""
    buckets = max(100, min(8000, int(buckets)))
    cache = paths().cache / "waveforms" / f"{_key(path, str(buckets))}.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    info = ffmpeg.probe(path)
    duration = float(info.get("duration") or 0)
    if not info.get("has_audio") or not duration:
        return {"peaks": [], "duration": duration, "buckets": 0}
    # Low sample rate for long files: the envelope does not need 44.1 kHz.
    rate = int(max(400, min(8000, buckets * 48 / duration)))
    total_samples = int(duration * rate) + 1
    per = max(1, total_samples // buckets)
    cmd = [require("ffmpeg"), "-hide_banner", "-v", "error", "-nostdin", "-i", str(path), "-map", "0:a:0",
           "-ac", "1", "-ar", str(rate), "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    peaks: list[float] = []
    carry = np.zeros(0, dtype=np.int16)
    try:
        assert proc.stdout
        while True:
            raw = proc.stdout.read(per * 2 * 256)
            if not raw:
                break
            data = np.concatenate([carry, np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16)])
            n = len(data) // per
            if n:
                block = np.abs(data[: n * per].astype(np.int32)).reshape(n, per)
                peaks.extend((block.max(axis=1) / 32768.0).round(3).tolist())
            carry = data[n * per:]
        if len(carry):
            peaks.append(round(float(np.abs(carry.astype(np.int32)).max()) / 32768.0, 3))
    finally:
        proc.wait(timeout=30)
    top = max(peaks) if peaks else 0
    if top > 0:  # normalise so quiet recordings are still readable
        peaks = [round(min(1.0, p / top), 3) for p in peaks]
    result = {"peaks": peaks, "duration": duration, "buckets": len(peaks)}
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(result), "utf-8")
    return result


# ------------------------------------------------------------------ filmstrip
def filmstrip(path: Path, count: int = 12, height: int = 90) -> dict:
    """Evenly spaced frames for the cutting timeline (thumbnail cache keys)."""
    count = max(2, min(40, int(count)))
    info = ffmpeg.probe(path)
    duration = float(info.get("duration") or 0)
    if not info.get("has_video") or not duration:
        return {"frames": [], "duration": duration}
    ff = require("ffmpeg")
    key = _key(path, f"strip{count}x{height}")
    out_dir = paths().thumbnails
    times = [duration * (i + 0.5) / count for i in range(count)]

    def grab(i: int) -> str | None:
        k = f"{key}{i:02d}"
        out = out_dir / f"{k}.jpg"
        if out.exists():
            return k
        subprocess.run([ff, "-hide_banner", "-v", "error", "-y", "-ss", f"{times[i]:.3f}", "-i", str(path),
                        "-frames:v", "1", "-vf", f"scale=-2:{height}", "-q:v", "5", str(out)],
                       capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
        return k if out.exists() else None

    with ThreadPoolExecutor(max_workers=4) as pool:
        keys = list(pool.map(grab, range(count)))
    return {"frames": [{"t": t, "key": k} for t, k in zip(times, keys) if k], "duration": duration}
