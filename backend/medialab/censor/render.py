"""Export : la vidéo entière réencodée avec les zones floutées ; le son (et les sous-titres en MKV) copiés tels quels."""
from __future__ import annotations

import collections
import io
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

from ..components import CREATE_NO_WINDOW, require
from ..errors import Canceled, MediaLabError
from ..logging_setup import get_logger
from ..media import ffmpeg
from . import effects, frames as FR
from .track import FrameBoxes

log = get_logger("censor")
MP4_AUDIO = {"aac", "mp3", "ac3", "eac3", "alac", "opus", "flac"}


def output_path(src: Path, out_dir: Path) -> Path:
    ext = src.suffix.lower() if src.suffix.lower() in (".mkv", ".mp4", ".mov") else ".mp4"
    dst = out_dir / f"{src.stem} (censuré){ext}"
    return dst


def export(src: Path, video: dict, fb: FrameBoxes, fps: float, frames: int, dst: Path, *, style: str, shape: str,
           strength: int, cancel: threading.Event, progress: Callable[[float, str, float | None], None],
           use_gpu: bool = True) -> dict:
    w, h = FR.even(int(video["width"])), FR.even(int(video["height"]))
    mkv = dst.suffix.lower() == ".mkv"
    acodec = (video.get("acodec") or "").lower()
    audio = ["-map", "1:a?", "-c:a", "copy"] if mkv or acodec in MP4_AUDIO or not acodec else \
        ["-map", "1:a?", "-c:a", "aac", "-b:a", "256k"]
    subs = ["-map", "1:s?", "-c:s", "copy"] if mkv else []
    part = dst.with_name(f"{dst.stem}.part{dst.suffix}")
    last_err: MediaLabError | None = None
    for gpu in ([True, False] if use_gpu else [False]):
        enc = (ffmpeg.best_encoder("h264") if gpu else None) or "libx264"
        try:
            stats = _encode(src, w, h, fps, frames, fb, part, enc, audio, subs, mkv, style, shape, strength, cancel,
                            progress)
            last_err = None
            break
        except MediaLabError as e:
            part.unlink(missing_ok=True)
            last_err = e
            if e.code == "encoder_failed" and gpu:
                log.warning("GPU encoder failed; exporting on the CPU")
                continue
            raise
        except BaseException:
            part.unlink(missing_ok=True)
            raise
    if last_err:
        raise last_err
    os.replace(part, dst)
    return {"file": str(dst), "size": dst.stat().st_size, "rendered_at": time.time(), **stats}


def _encode(src: Path, w: int, h: int, fps: float, frames: int, fb: FrameBoxes, part: Path, enc: str,
            audio: list[str], subs: list[str], mkv: bool, style: str, shape: str, strength: int,
            cancel: threading.Event, progress: Callable[[float, str, float | None], None]) -> dict:
    vargs = ffmpeg.encoder_args(enc, 19 if enc.endswith("_nvenc") else 18)
    cmd = [require("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{w}x{h}", "-r", f"{fps:.6f}", "-i", "-",
           "-i", str(src), "-map", "0:v:0", *audio, *subs, *vargs, "-g", str(max(1, int(round(fps * 2)))),
           *([] if mkv else ["-movflags", "+faststart"]), "-map_metadata", "1", "-map_chapters", "1", str(part)]
    log.debug("export: %s", subprocess.list2cmdline(cmd))
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=CREATE_NO_WINDOW, bufsize=w * h * 3)
    tail: collections.deque[str] = collections.deque(maxlen=40)

    def drain() -> None:
        assert proc.stderr
        for line in proc.stderr:
            tail.append(line.decode("utf-8", "replace").rstrip())

    th = threading.Thread(target=drain, daemon=True)
    th.start()
    reader = FR.Reader(src, fps, (w, h), "yuv420p", cancel=cancel)
    t0 = time.monotonic()
    last_ui = 0.0
    i = 0
    blurred = 0
    try:
        assert proc.stdin
        while (fr := reader.read()) is not None:
            boxes = fb.at(i)
            if boxes:
                fr = fr.copy()
                y, u, v = FR.yuv_planes(fr, w, h)
                effects.apply(effects.yuv_planes(y, u, v), boxes, style, shape, strength)
                blurred += 1
            try:
                proc.stdin.write(fr.tobytes() if boxes else memoryview(fr))
            except (BrokenPipeError, OSError) as e:
                raise _failure(proc, tail, enc) from e
            i += 1
            now = time.monotonic()
            if now - last_ui > 0.5:
                last_ui = now
                el = now - t0
                rate = i / el if el > 0 else 0
                eta = (frames - i) / rate if rate > 0 and frames > i else None
                progress(min(0.999, i / max(1, frames)), f"Export · {rate:.0f} images/s · {enc}", eta)
        proc.stdin.close()
        proc.wait()
    except Canceled:
        proc.kill()
        raise
    finally:
        reader.close()
        if proc.poll() is None:
            proc.kill()
        th.join(timeout=3)
    if cancel.is_set():
        raise Canceled()
    if proc.returncode != 0:
        raise _failure(proc, tail, enc)
    log.info("Exported %s: %d frames, %d blurred, %.0fs (%s)", part.name, i, blurred, time.monotonic() - t0, enc)
    return {"frames": i, "blurred_frames": blurred, "encoder": enc, "seconds": round(time.monotonic() - t0, 1)}


def _failure(proc: subprocess.Popen, tail: collections.deque, enc: str) -> MediaLabError:
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    detail = "\n".join(tail)
    low = detail.lower()
    log.error("Export failed (%s): %s", enc, detail[-1500:])
    if "no space left" in low:
        return MediaLabError("disk_full", "Plus assez de place sur le disque.", "Libérez de la place puis relancez.",
                             True, detail)
    if ffmpeg.is_hw(enc) or "nvenc" in low:
        return MediaLabError("encoder_failed", "L'encodeur vidéo de la carte graphique a échoué.", "", True, detail)
    return MediaLabError("export_failed", "L'export de la vidéo a échoué.", "Ouvrez le journal pour le détail.",
                         True, detail)


def preview(src: Path, video: dict, fps: float, index: int, boxes: list[np.ndarray], *, style: str, shape: str,
            strength: int, blur: bool, max_w: int = 1280) -> bytes:
    """Une image en JPEG, floutée comme à l'export (``blur``) ou telle quelle."""
    w, h = int(video["width"]), int(video["height"])
    if w > max_w:
        w, h = max_w, h * max_w / w
    size = (FR.even(w), FR.even(h))
    frame = FR.grab(src, fps, index, size)
    if blur and boxes:
        effects.apply(effects.rgb_planes(frame), boxes, style, shape, strength)
    buf = io.BytesIO()
    Image.fromarray(frame).save(buf, "JPEG", quality=88)
    return buf.getvalue()
