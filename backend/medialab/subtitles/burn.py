"""Burn subtitles into the picture (libass through FFmpeg's ``ass`` filter).

Quality is preserved as far as re-encoding allows: same resolution, frame rate and orientation, the same codec
family (H.264 / H.265 / AV1, 10-bit kept for 10-bit or HDR sources) at a high constant quality, the sound and the
metadata copied untouched. The GPU encoder is used when available and the CPU takes over if it fails.
The .ass file is written next to FFmpeg's working directory and passed by a plain relative name: no drive-letter
escaping inside the filter graph.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from ..downloader.formats import vcodec_family
from ..errors import MediaLabError
from ..logging_setup import get_logger
from ..media import ffmpeg
from . import formats

log = get_logger("process")
Progress = Callable[[float, str, float | None], None]
_KEEP_CONTAINER = {"mp4", "mkv", "mov", "m4v"}


def output_path(video: dict, folder: str | None, french: bool) -> Path:
    src = Path(video["path"])
    ext = src.suffix.lower().lstrip(".")
    ext = ext if ext in _KEEP_CONTAINER else "mp4"
    out_dir = Path(folder) if folder else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    label = "sous-titré" if french else "subtitled"
    cand = out_dir / f"{src.stem} ({label}).{ext}"
    n = 1
    while cand.exists():
        cand = out_dir / f"{src.stem} ({label}) ({n}).{ext}"
        n += 1
    return cand


def _encoder(video: dict, container: str, use_gpu: bool) -> tuple[str, list[str]]:
    fam = vcodec_family(video.get("vcodec")) or "h264"
    ten_bit = int(video.get("bit_depth") or 8) > 8 or bool(video.get("is_hdr"))
    # H.264 plays everywhere (AV1 needs an extra Windows extension); H.265 only to keep 10-bit / HDR
    fam = "h265" if ten_bit else "h264"
    enc = (ffmpeg.best_encoder(fam) if use_gpu else None) or {"h264": "libx264", "h265": "libx265",
                                                              "av1": "libsvtav1"}[fam]
    args = ffmpeg.encoder_args(enc, 16 if fam == "h264" else 19, ten_bit=ten_bit)
    size, duration = video.get("size") or 0, video.get("duration") or 0
    if size and duration > 1 and (enc.endswith("_nvenc") or enc in ("libx264", "libx265")):
        # a re-encode never needs much more than the source's own bitrate: constant quality, capped at twice it
        # (a 2 Mb/s source no longer comes out at 6 Mb/s; the picture is the same)
        cap = max(2_000_000, int(size * 8 / duration * 2))
        args += ["-maxrate", str(cap), "-bufsize", str(cap * 2)]
    if fam == "h265" and container in ("mp4", "mov", "m4v"):
        args += ["-tag:v", "hvc1"]
    return enc, args


def burn(project: dict, dst: Path, cancel: threading.Event, progress: Progress, use_gpu: bool = True,
         french: bool = True) -> dict:
    video = project["video"]
    src = Path(video["path"]).resolve()
    dst = Path(dst).resolve()  # FFmpeg runs from a temporary folder
    if not src.is_file():
        raise MediaLabError("source_missing", "The video of this project no longer exists.")
    if not video.get("has_video"):
        raise MediaLabError("no_video_stream", "This file has no picture to write subtitles on.",
                            "Export an SRT / VTT file instead.")
    cues = project.get("cues") or []
    if not cues:
        raise MediaLabError("no_subtitles", "There are no subtitles to burn in.", "Generate or add subtitles first.")
    lang = project.get("target_lang") or project.get("source_lang") or ""
    tmp = Path(tempfile.mkdtemp(prefix="se7en-burn-"))
    part = dst.with_name(f"{dst.stem}.se7en-part{dst.suffix}")
    label = "Incrustation des sous-titres" if french else "Burning in the subtitles"
    t0 = time.perf_counter()
    try:
        formats.write(tmp / "subs.ass", formats.to_ass(cues, project.get("style"), video.get("width") or 1920,
                                                        video.get("height") or 1080, lang))
        container = dst.suffix.lower().lstrip(".")
        for attempt_gpu in ([True, False] if use_gpu else [False]):
            enc, vargs = _encoder(video, container, attempt_gpu)
            mp4_audio_ok = (video.get("acodec") or "") in ("aac", "mp3", "alac", "ac3", "eac3")
            aargs = ["-c:a", "copy"] if container not in ("mp4", "mov", "m4v") or mp4_audio_ok else                 ["-c:a", "aac", "-b:a", "256k"]  # Opus / Vorbis in MP4 is not read by every Windows player
            args = ["-i", str(src), "-map", "0:v:0", "-map", "0:a?", "-vf", "ass=subs.ass", *vargs, *aargs,
                    "-map_metadata", "0", "-map_chapters", "0"]
            if container in ("mp4", "mov", "m4v"):
                args += ["-movflags", "+faststart"]
            args.append(str(part))

            def on_progress(frac: float, state: dict, enc=enc) -> None:
                el = time.perf_counter() - t0
                progress(frac, f"{label} · {enc}", el / frac - el if frac > 0.02 else None)

            try:
                progress(0.0, label, None)
                ffmpeg.run(args, duration=video.get("duration"), cancel=cancel, label="subtitle burn-in",
                           on_progress=on_progress, cwd=str(tmp))
                break
            except MediaLabError as e:
                part.unlink(missing_ok=True)
                if e.code == "encoder_failed" and attempt_gpu:
                    log.warning("GPU encoder %s failed for burn-in; retrying on the CPU", enc)
                    continue
                if "copy" in (e.detail or "") and "audio" in (e.detail or "").lower():
                    raise MediaLabError("audio_copy_failed", "The sound could not be copied into this format.",
                                        "Retry: it will be converted.", True, e.detail) from e
                raise
        os.replace(part, dst)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    elapsed = time.perf_counter() - t0
    log.info("Burned %d subtitles into %s in %.1fs", len(cues), dst.name, elapsed)
    return {"file_path": str(dst), "size": dst.stat().st_size, "title": dst.name,
            "format": f"{dst.suffix.lstrip('.').upper()} · {'sous-titres incrustés' if french else 'burned-in subtitles'}",
            "resolution": f"{video.get('width')}×{video.get('height')}", "duration": video.get("duration"),
            "meta": {"source": str(src), "elapsed": round(elapsed, 2), "cues": len(cues), "encoder": enc}}
