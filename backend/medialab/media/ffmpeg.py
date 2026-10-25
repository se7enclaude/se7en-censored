"""FFmpeg / FFprobe helpers: probing, progress-aware execution, encoder choice."""
from __future__ import annotations

import collections
import json
import subprocess
import threading
from fractions import Fraction
from pathlib import Path
from typing import Callable

from ..components import CREATE_NO_WINDOW, require
from ..errors import Canceled, MediaLabError
from ..logging_setup import get_logger

log = get_logger("process")

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".avif", ".heic", ".heif", ".jfif"}
VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".wmv", ".flv", ".ts", ".mts", ".m2ts", ".mpg",
              ".mpeg", ".3gp", ".ogv", ".vob", ".f4v", ".asf", ".divx", ".mxf"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".oga", ".opus", ".wma", ".aiff", ".aif", ".alac",
              ".ac3", ".dts", ".amr", ".ape", ".mka", ".m4b", ".wv", ".caf", ".mid", ".midi", ".spx", ".weba"}
SUB_EXTS = {".srt", ".vtt", ".ass", ".ssa", ".sub"}


def media_kind(path: str | Path) -> str | None:
    """image | video (what the AI upscaler accepts)."""
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    return None


def file_kind(path: str | Path) -> str | None:
    """image | video | audio | subtitle, from the extension (no disk access)."""
    ext = Path(path).suffix.lower()
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in SUB_EXTS:
        return "subtitle"
    return media_kind(path)


def _fps(rate: str | None) -> float | None:
    try:
        f = Fraction(rate)  # "30000/1001"
        return float(f) if f > 0 else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def probe(path: str | Path) -> dict:
    ffprobe = require("ffprobe")
    cmd = [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60,
                             creationflags=CREATE_NO_WINDOW, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as e:
        raise MediaLabError("probe_failed", "Analyzing the file took too long.", "The file may be damaged.", True) from e
    if out.returncode != 0:
        raise MediaLabError("probe_failed", "This file could not be read as a media file.",
                            "Check that the file is a valid image or video.", False, out.stderr[-2000:])
    data = json.loads(out.stdout or "{}")
    fmt = data.get("format", {})
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    audios = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    cover = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"
                  and s.get("disposition", {}).get("attached_pic")), None)
    subs = [s for s in data.get("streams", []) if s.get("codec_type") == "subtitle"]
    duration = float(fmt.get("duration") or (video or {}).get("duration") or 0) or None
    info: dict = {
        "path": str(path),
        "format": fmt.get("format_name"),
        "size": int(fmt.get("size") or 0) or Path(path).stat().st_size,
        "duration": duration,
        "bitrate": int(fmt.get("bit_rate") or 0) or None,
        "has_audio": bool(audios),
        "has_video": bool(video),
        "audio": [{"codec": a.get("codec_name"), "channels": a.get("channels"), "index": a.get("index"),
                   "sample_rate": a.get("sample_rate"), "bitrate": a.get("bit_rate"),
                   "language": (a.get("tags") or {}).get("language")} for a in audios],
        "cover_index": cover.get("index") if cover else None,
        "subtitles": [{"codec": x.get("codec_name"), "index": x.get("index"),
                       "language": (x.get("tags") or {}).get("language")} for x in subs],
        "tags": {k.lower(): v for k, v in (fmt.get("tags") or {}).items()},
        "kind": file_kind(path) or ("video" if video and duration else "audio" if audios else "image"),
    }
    if video:
        fps = _fps(video.get("avg_frame_rate")) or _fps(video.get("r_frame_rate"))
        nb = video.get("nb_frames")
        frames = int(nb) if nb and str(nb).isdigit() else (int(round(duration * fps)) if duration and fps else None)
        transfer = video.get("color_transfer")
        try:
            v_start = float(video.get("start_time") or 0) - float(fmt.get("start_time") or 0)
        except (TypeError, ValueError):
            v_start = 0.0
        info.update({
            "video_start_offset": max(0.0, v_start),
            "width": video.get("width"), "height": video.get("height"),
            "vcodec": video.get("codec_name"), "pix_fmt": video.get("pix_fmt"),
            "fps": fps, "r_frame_rate": video.get("r_frame_rate"),
            "frames": frames,
            "frames_exact": bool(nb and str(nb).isdigit()),
            "color_transfer": transfer, "color_primaries": video.get("color_primaries"),
            "color_space": video.get("color_space"),
            "is_hdr": transfer in HDR_TRANSFERS,
            "bit_depth": int(video.get("bits_per_raw_sample") or 0) or (10 if "10" in (video.get("pix_fmt") or "") else 8),
            "rotation": _rotation(video),
            "vfr": _fps(video.get("avg_frame_rate")) != _fps(video.get("r_frame_rate")),
        })
        if info["kind"] == "video" and (not duration or (frames or 0) <= 1):
            info["kind"] = "audio" if audios else "image"
    return info


def keyframes_near(path: str | Path, t: float, before: float = 12.0, after: float = 3.0) -> list[float]:
    """Keyframe times of the main video stream around ``t`` (reads only that part of the file)."""
    ffprobe = require("ffprobe")
    start = max(0.0, t - before)
    cmd = [ffprobe, "-v", "error", "-select_streams", "V:0", "-read_intervals", f"{start:.3f}%{t + after:.3f}",
           "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, creationflags=CREATE_NO_WINDOW,
                             errors="replace").stdout
    except (OSError, subprocess.SubprocessError):
        return []
    times = []
    for line in out.splitlines():
        parts = line.strip().split(",")
        if len(parts) >= 2 and "K" in parts[1]:
            try:
                times.append(float(parts[0]))
            except ValueError:
                pass
    return sorted(set(times))


def _rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                return int(sd["rotation"])
            except (TypeError, ValueError):
                pass
    try:
        return int(stream.get("tags", {}).get("rotate", 0))
    except (TypeError, ValueError):
        return 0


def run(args: list[str], *, duration: float | None = None,
        on_progress: Callable[[float, dict], None] | None = None,
        cancel: threading.Event | None = None, label: str = "ffmpeg", cwd: str | None = None) -> None:
    """Run ffmpeg with machine-readable progress; raises MediaLabError on failure. ``cwd`` lets filters load
    model files by a plain relative name (no drive-letter escaping in the filter graph)."""
    ffmpeg = require("ffmpeg")
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", *args]
    log.debug("%s: %s", label, subprocess.list2cmdline(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=cwd,
                            creationflags=CREATE_NO_WINDOW, text=True, encoding="utf-8", errors="replace")
    tail: collections.deque[str] = collections.deque(maxlen=40)

    def read_err() -> None:
        assert proc.stderr
        for line in proc.stderr:
            tail.append(line.rstrip())

    t = threading.Thread(target=read_err, daemon=True)
    t.start()
    state: dict = {}
    assert proc.stdout
    try:
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                proc.kill()
                raise Canceled()
            key, _, value = line.strip().partition("=")
            state[key] = value
            if key == "progress" and on_progress:
                us = state.get("out_time_us") or state.get("out_time_ms")
                try:
                    seconds = int(us) / 1_000_000 if us and us != "N/A" else 0.0
                except ValueError:
                    seconds = 0.0
                frac = min(1.0, seconds / duration) if duration else 0.0
                on_progress(frac, dict(state))
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
        t.join(timeout=2)
    if cancel is not None and cancel.is_set():
        raise Canceled()
    if proc.returncode != 0:
        detail = "\n".join(tail)
        log.error("%s failed (code %s): %s", label, proc.returncode, detail[-1500:])
        low = detail.lower()
        if "no space left" in low:
            raise MediaLabError("disk_full", "Not enough disk space.", "Free up space and resume.", True, detail)
        if "out of memory" in low or "cannot allocate" in low:
            raise MediaLabError("out_of_memory", "FFmpeg ran out of memory.", "Lower the output resolution.", True, detail)
        if "openencodesession" in low or ("nvenc" in low or "_qsv" in low or "mfx" in low or "amf" in low)                 and "error" in low:
            raise MediaLabError("encoder_failed", "The GPU video encoder failed.",
                                "Choose a CPU encoder (libx264/libx265) in Settings › AI.", True, detail)
        raise MediaLabError("ffmpeg_failed", f"{label} failed.", "Open Logs for the FFmpeg output.", True, detail)


# ------------------------------------------------------------ encoders
_encoders_cache: set[str] | None = None
_enc_lock = threading.Lock()


def reset_encoders() -> None:
    """Forget the detected encoders (after FFmpeg was installed or updated)."""
    global _encoders_cache
    with _enc_lock:
        _encoders_cache = None


def working_encoders() -> set[str]:
    """Encoders that are compiled in *and* actually initialise on this machine."""
    global _encoders_cache
    with _enc_lock:
        if _encoders_cache is not None:
            return _encoders_cache
        ffmpeg = require("ffmpeg")
        listed = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True,
                                creationflags=CREATE_NO_WINDOW, errors="replace").stdout
        candidates = [e for e in (*HW_ENCODERS, "libx264", "libx265", "libsvtav1", "libaom-av1", "libvpx-vp9")
                      if f" {e} " in listed]
        ok: set[str] = set()
        for enc in candidates:
            if enc not in HW_ENCODERS:
                ok.add(enc)
                continue
            # Hardware encoders are compiled in every build: keep only those that really start here.
            try:
                test = subprocess.run(
                    [ffmpeg, "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
                     "-pix_fmt", "nv12" if "_qsv" in enc else "yuv420p", "-c:v", enc, "-f", "null", "-"],
                    capture_output=True, text=True, creationflags=CREATE_NO_WINDOW, timeout=30,
                )
            except subprocess.TimeoutExpired:
                continue
            if test.returncode == 0:
                ok.add(enc)
        log.info("Working video encoders: %s", ", ".join(sorted(ok)) or "none")
        _encoders_cache = ok
        return ok


HW_ENCODERS = ("h264_nvenc", "hevc_nvenc", "av1_nvenc", "h264_qsv", "hevc_qsv", "av1_qsv",
               "h264_amf", "hevc_amf", "av1_amf")
_CODEC_ENCODERS = {
    "h264": ("h264_nvenc", "h264_qsv", "h264_amf", "libx264"),
    "h265": ("hevc_nvenc", "hevc_qsv", "hevc_amf", "libx265"),
    "av1": ("av1_nvenc", "av1_qsv", "av1_amf", "libsvtav1", "libaom-av1"),
    "vp9": ("libvpx-vp9",),
}


def best_encoder(codec: str) -> str | None:
    """Fastest working encoder for a codec family: NVIDIA › Intel › AMD › CPU."""
    enc = working_encoders()
    return next((e for e in _CODEC_ENCODERS.get(codec, ()) if e in enc), None)


def is_hw(encoder: str) -> bool:
    return encoder in HW_ENCODERS


def pick_encoder(preference: str, hdr: bool = False) -> str:
    enc = working_encoders()
    if preference != "auto":
        if preference in enc:
            return preference
        log.warning("Encoder %s unavailable, falling back to automatic choice", preference)
    order = (["hevc_nvenc", "hevc_qsv", "hevc_amf", "libx265", "libx264"] if hdr
             else ["h264_nvenc", "h264_qsv", "h264_amf", "libx264", "hevc_nvenc", "libx265"])
    for e in order:
        if e in enc:
            return e
    raise MediaLabError("encoder_missing", "No usable video encoder was found in FFmpeg.",
                        "Reinstall FFmpeg from Settings › Components.")


def encoder_args(encoder: str, quality: int, ten_bit: bool = False, fast: bool = False) -> list[str]:
    """Quality-based (constant quality) arguments for an encoder. ``fast`` trades a little
    compression efficiency for speed on CPU encoders (used by quick conversions)."""
    if encoder.endswith("_qsv"):
        return ["-c:v", encoder, "-global_quality", str(quality), "-preset", "faster" if fast else "medium",
                "-look_ahead", "0", "-pix_fmt", "p010le" if ten_bit and encoder != "h264_qsv" else "nv12"]
    if encoder.endswith("_amf"):
        q = str(quality)
        return ["-c:v", encoder, "-quality", "speed" if fast else "quality", "-rc", "cqp",
                "-qp_i", q, "-qp_p", q, *(["-qp_b", q] if encoder == "h264_amf" else []), "-pix_fmt", "yuv420p"]
    if encoder == "libvpx-vp9":
        return ["-c:v", "libvpx-vp9", "-crf", str(quality + 12), "-b:v", "0", "-row-mt", "1",
                "-deadline", "good", "-cpu-used", "4" if fast else "2", "-pix_fmt", "yuv420p"]
    if encoder.endswith("_nvenc"):
        args = ["-c:v", encoder, "-preset", "p5", "-tune", "hq", "-rc", "vbr", "-cq", str(quality), "-b:v", "0"]
        if ten_bit and encoder != "h264_nvenc":
            args += ["-pix_fmt", "p010le"] + (["-profile:v", "main10"] if encoder == "hevc_nvenc" else [])
        else:
            args += ["-pix_fmt", "yuv420p"]
        return args
    if encoder == "libx265":
        return ["-c:v", "libx265", "-crf", str(quality), "-preset", "fast" if fast else "medium", "-tag:v", "hvc1",
                "-pix_fmt", "yuv420p10le" if ten_bit else "yuv420p"]
    if encoder == "libsvtav1":
        return ["-c:v", encoder, "-crf", str(quality + 10), "-preset", "10" if fast else "7",
                "-pix_fmt", "yuv420p10le" if ten_bit else "yuv420p"]
    if encoder == "libaom-av1":
        return ["-c:v", encoder, "-crf", str(quality + 10), "-cpu-used", "8" if fast else "4",
                "-row-mt", "1", "-pix_fmt", "yuv420p10le" if ten_bit else "yuv420p"]
    return ["-c:v", "libx264", "-crf", str(quality), "-preset", "faster" if fast else "medium",
            "-profile:v", "high", "-pix_fmt", "yuv420p"]


def extract_frame(src: str | Path, at: float, out: str | Path) -> Path:
    run(["-ss", f"{max(0.0, at):.3f}", "-i", str(src), "-frames:v", "1", "-an", str(out)], label="extract frame")
    if not Path(out).exists():
        raise MediaLabError("frame_extract_failed", "Could not extract a frame at this position.",
                            "Choose another position in the video.", True)
    return Path(out)
