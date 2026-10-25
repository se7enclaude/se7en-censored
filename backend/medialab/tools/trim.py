"""Cut videos and audio files: keep (or remove) one or more parts.

Two modes:

* ``fast``    — stream copy: instant and lossless. For video the cut starts on
                the keyframe just before the requested time (FFmpeg cannot copy
                from the middle of a GOP), so it can be up to ~2 s early.
* ``precise`` — frame-accurate: the video is re-encoded (GPU encoder when
                available, same codec family), the sound too when needed.

Audio-only files are frame-accurate in both modes (MP3/AAC frames are ~25 ms).
Parts are exported as separate files or joined into a single file.
"""
from __future__ import annotations

import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from ..config import paths
from ..downloader.formats import acodec_family, vcodec_family
from ..errors import Canceled, MediaLabError
from ..logging_setup import get_logger
from ..media import ffmpeg

log = get_logger("process")
Progress = Callable[[float, str, float | None], None]

MIN_PART = 0.05  # seconds


def normalize_segments(segments: list[dict], duration: float, remove: bool = False) -> list[tuple[float, float]]:
    """Clamp, sort and merge overlapping parts; ``remove`` returns what is left around them."""
    parts: list[tuple[float, float]] = []
    for seg in segments or []:
        try:
            a, b = float(seg["start"]), float(seg["end"])
        except (KeyError, TypeError, ValueError):
            continue
        a, b = max(0.0, min(a, b)), min(duration, max(a, b)) if duration else max(a, b)
        if b - a >= MIN_PART:
            parts.append((a, b))
    parts.sort()
    merged: list[tuple[float, float]] = []
    for a, b in parts:
        if merged and a <= merged[-1][1] + 1e-3:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    if not remove:
        return merged
    keep, cur = [], 0.0
    for a, b in merged:
        if a - cur >= MIN_PART:
            keep.append((cur, a))
        cur = b
    if duration - cur >= MIN_PART:
        keep.append((cur, duration))
    return keep


def _suffix(i: int | None, fr: bool) -> str:
    word = "extrait" if fr else "clip"
    return f" ({word})" if i is None else f" ({word} {i})"


def _unique(p: Path) -> Path:
    n = 1
    cand = p
    while cand.exists():
        cand = p.with_name(f"{p.stem} ({n}){p.suffix}")
        n += 1
    return cand


def _audio_codec_args(src_codec: str | None, container: str, bitrate: int | None) -> list[str]:
    fam = acodec_family(src_codec) or ""
    br = f"{max(96, min(320, (bitrate or 256000) // 1000))}k"
    if (src_codec or "").startswith("pcm_"):
        return ["-c:a", src_codec]
    if src_codec == "alac":
        return ["-c:a", "alac"]
    if container in ("wav",):
        return ["-c:a", "pcm_s16le"]
    if container in ("aiff", "aif"):
        return ["-c:a", "pcm_s16be"]
    if fam == "flac" or container == "flac":
        return ["-c:a", "flac"]
    if fam == "mp3" or container == "mp3":
        return ["-c:a", "libmp3lame", "-b:a", br]
    if fam == "opus" or container in ("opus", "webm"):
        return ["-c:a", "libopus", "-b:a", br]
    if fam == "vorbis" or container in ("ogg", "oga"):
        return ["-c:a", "libvorbis", "-b:a", br]
    if container == "wma":
        return ["-c:a", "wmav2", "-b:a", br]
    return ["-c:a", "aac", "-b:a", br]


def _video_encoder(info: dict, container: str) -> tuple[str, list[str]]:
    fam = vcodec_family(info.get("vcodec")) or "h264"
    if container == "webm":
        fam = "vp9" if fam not in ("vp9", "av1") else fam
    elif fam not in ("h264", "h265", "av1"):
        fam = "h264"
    enc = ffmpeg.best_encoder(fam) or ffmpeg.best_encoder("h264") or "libx264"
    args = ffmpeg.encoder_args(enc, 18 if fam == "h264" else 20)
    if fam == "h265" and container in ("mp4", "mov", "m4v"):
        args += ["-tag:v", "hvc1"]
    return enc, args


def trim(src: Path, segments: list[dict], opts: dict, cancel: threading.Event, progress: Progress,
         french: bool = False) -> dict:
    if not src.is_file():
        raise MediaLabError("source_missing", "The source file no longer exists.", "It may have been moved or deleted.")
    info = ffmpeg.probe(src)
    duration = float(info.get("duration") or 0)
    if not duration:
        raise MediaLabError("no_duration", "This file has no duration, it cannot be cut.",
                            "Only video and audio files can be cut.")
    parts = normalize_segments(segments, duration, remove=bool(opts.get("remove")))
    if not parts:
        raise MediaLabError("no_segment", "Nothing to export: select at least one part.",
                            "Set a start and an end on the timeline.")
    if len(parts) == 1 and parts[0][0] <= 0.01 and parts[0][1] >= duration - 0.01 and not opts.get("fade"):
        raise MediaLabError("no_change", "The selection covers the whole file.",
                            "Move the start or end markers to cut something.")
    is_video = bool(info.get("has_video"))
    mode = "precise" if opts.get("mode") == "precise" else "fast"
    fade = max(0.0, float(opts.get("fade") or 0))
    container = src.suffix.lstrip(".").lower() or "mp4"
    merge = opts.get("output") == "merge" and len(parts) > 1
    out_dir = Path(opts.get("output_dir") or src.parent)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = paths().temp / "trim" / uuid.uuid4().hex[:10]
    work.mkdir(parents=True, exist_ok=True)
    total = sum(b - a for a, b in parts)
    done = 0.0
    t0 = time.perf_counter()
    outputs: list[Path] = []
    try:
        pieces: list[Path] = []
        for i, (a, b) in enumerate(parts):
            if cancel.is_set():
                raise Canceled()
            if is_video and mode == "fast":
                # Stream copy can only start on a keyframe: start on the one at (or just after) the
                # requested time instead of silently including the whole previous group of pictures.
                kfs = ffmpeg.keyframes_near(src, a)
                start_offset = float(info.get("video_start_offset") or 0)
                after = [k for k in kfs if a - 0.001 <= k - start_offset <= a + 0.3]
                before = [k for k in kfs if k - start_offset <= a + 0.001]
                if after:
                    a = max(0.0, after[0] - start_offset)
                elif before:
                    a = max(0.0, before[-1] - start_offset)
                if b - a < MIN_PART:
                    continue
            length = b - a
            label = f"Cutting part {i + 1}/{len(parts)}" if len(parts) > 1 else "Cutting"
            piece = work / f"part{i:03d}{src.suffix}"
            if is_video and mode == "fast":
                # Stream copy: seek near the part (fast on long files), then drop packets up to the
                # exact keyframe. Input seeking alone keeps whole extra GOPs with MKV sources.
                pre = max(0.0, a - 15.0)
                args = ["-ss", f"{pre:.3f}", "-i", str(src), "-ss", f"{max(0.0, a - pre - 0.002):.3f}",
                        "-t", f"{length:.3f}"]
            else:
                args = ["-ss", f"{a:.3f}", "-i", str(src), "-t", f"{length:.3f}"]
            # Streams: main picture, every sound track, cover art kept for music files.
            if is_video:
                args += ["-map", "0:V:0", "-map", "0:a?"]
                if container in ("mkv",):
                    args += ["-map", "0:s?"]
            else:
                args += ["-map", "0:a"]
                if info.get("cover_index") is not None and container in ("mp3", "m4a", "flac"):
                    args += ["-map", f"0:{info['cover_index']}", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
            lossless_audio = (info.get("audio") or [{}])[0].get("codec", "").startswith(("flac", "pcm_", "alac"))
            # Lossless sound is re-encoded losslessly: exact cut and a correct duration header.
            reencode_audio = bool(fade) or (mode == "precise" and is_video) or (not is_video and lossless_audio)
            if is_video and mode == "precise":
                _enc, vargs = _video_encoder(info, container)
                args += vargs
            elif is_video:
                args += ["-c:v", "copy"]
            if reencode_audio and info.get("has_audio"):
                a0 = (info.get("audio") or [{}])[0]
                args += _audio_codec_args(a0.get("codec"), container, int(a0.get("bitrate") or 0) or None)
                if fade:
                    f = min(fade, length / 2)
                    args += ["-af", f"afade=t=in:st=0:d={f:.3f},afade=t=out:st={max(0.0, length - f):.3f}:d={f:.3f}"]
            elif info.get("has_audio"):
                args += ["-c:a", "copy"]
            if container in ("mkv",):
                args += ["-c:s", "copy"]
            args += ["-map_metadata", "0", "-avoid_negative_ts", "make_zero"]
            if container in ("mp4", "mov", "m4v", "m4a"):
                args += ["-movflags", "+faststart"]
            base = done

            def on_progress(frac: float, _state: dict, base=base, length=length, label=label) -> None:
                progress(min(0.99, (base + frac * length) / total), label, None)

            try:
                ffmpeg.run([*args, str(piece)], duration=length, cancel=cancel, label="cut", on_progress=on_progress)
            except MediaLabError as e:
                if e.code != "encoder_failed" or not (is_video and mode == "precise"):
                    raise
                log.warning("GPU encoder failed while cutting; retrying on the CPU")
                i0 = args.index("-c:v")
                _enc, vargs = _video_encoder(info, container)
                cpu = ffmpeg.encoder_args("libx264", 18)
                args = args[:i0] + cpu + args[i0 + len(vargs):]
                ffmpeg.run([*args, str(piece)], duration=length, cancel=cancel, label="cut", on_progress=on_progress)
            if not piece.exists() or piece.stat().st_size == 0:
                raise MediaLabError("cut_failed", "The part could not be exported.", "Try the precise mode.", True)
            pieces.append(piece)
            done += length
        if merge:
            progress(0.99, "Joining the parts", None)
            listing = work / "list.txt"
            listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in pieces), "utf-8")
            joined = work / f"joined{src.suffix}"
            args = ["-f", "concat", "-safe", "0", "-i", str(listing), "-map", "0", "-c", "copy"]
            if container in ("mp4", "mov", "m4v", "m4a"):
                args += ["-movflags", "+faststart"]
            ffmpeg.run([*args, str(joined)], cancel=cancel, label="join")
            dst = _unique(out_dir / f"{src.stem}{_suffix(None, french)}{src.suffix}")
            shutil.move(str(joined), str(dst))
            outputs.append(dst)
        else:
            for i, piece in enumerate(pieces):
                name = f"{src.stem}{_suffix(i + 1 if len(pieces) > 1 else None, french)}{src.suffix}"
                dst = _unique(out_dir / name)
                shutil.move(str(piece), str(dst))
                outputs.append(dst)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    elapsed = time.perf_counter() - t0
    progress(1.0, "Done", None)
    log.info("Cut %s into %d file(s) (%s, %.1fs kept) in %.1fs", src.name, len(outputs), mode, total, elapsed)
    main = outputs[0]
    return {
        "file_path": str(main), "title": main.name, "size": sum(o.stat().st_size for o in outputs),
        "duration": total, "format": f"{main.suffix.lstrip('.').upper()} · {'precise' if mode == 'precise' else 'lossless'}",
        "resolution": f"{info.get('width')}×{info.get('height')}" if is_video and info.get("width") else None,
        "extra_files": [str(o) for o in outputs[1:]],
        "meta": {"source": str(src), "parts": [[a, b] for a, b in parts], "mode": mode, "files": [str(o) for o in outputs],
                 "elapsed": round(elapsed, 2), "merged": merge},
    }

