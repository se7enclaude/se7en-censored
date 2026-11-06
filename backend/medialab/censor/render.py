"""Export : la vidéo floutée, fidèle à la source (codec, profondeur, couleurs, HDR) ; le son et les sous-titres (en MKV)
copiés tels quels. Rendu intelligent (smart.py) quand c'est possible, sinon toute l'image est réencodée."""
from __future__ import annotations

import collections
import glob
import io
import os
import shutil
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
from . import effects, frames as FR, smart
from . import source as S
from .track import FrameBoxes

log = get_logger("censor")
MP4_AUDIO = {"aac", "mp3", "ac3", "eac3", "alac", "opus", "flac"}


def output_path(src: Path, out_dir: Path, previous: str | None = None) -> Path:
    """« <nom> (censuré) » ; un fichier du même nom qui n'est pas l'export précédent de ce projet (autre vidéo du même
    nom, fichier à toi) n'est jamais écrasé : « (censuré 2) », « (censuré 3) »…"""
    ext = src.suffix.lower() if src.suffix.lower() in (".mkv", ".mp4", ".mov") else ".mp4"
    dst = out_dir / f"{src.stem} (censuré){ext}"
    n = 2
    while dst.exists() and not (previous and os.path.normcase(str(dst)) == os.path.normcase(previous)):
        dst = out_dir / f"{src.stem} (censuré {n}){ext}"
        n += 1
    return dst


def leftovers(src: Path, out_dir: Path) -> list[Path]:
    """Fichiers de travail d'un export interrompu (appli fermée de force, PC éteint) : vidéo partielle, image seule,
    morceaux du rendu intelligent."""
    stem = glob.escape(f"{src.stem} (censuré")
    found = [*out_dir.glob(f"{stem}*).part.*"), *out_dir.glob(f"{stem}*).video.*"),
             *out_dir.glob("." + glob.escape(f"{src.stem.replace('%', '_')} (censuré") + "*).morceaux")]
    return found


def export(src: Path, video: dict, fb: FrameBoxes, fps: float, frames: int, dst: Path, *, style: str, shape: str,
           strength: int, cancel: threading.Event, progress: Callable[[float, str, float | None], None],
           use_gpu: bool = True, smart_render: bool = True) -> dict:
    """Rendu intelligent si la source s'y prête (seuls les passages floutés sont réencodés), sinon export complet ;
    dans les deux cas l'image garde le codec, la profondeur, les couleurs et les métadonnées HDR de la source."""
    d = S.describe(src)
    mkv = dst.suffix.lower() == ".mkv"
    acodec = (video.get("acodec") or "").lower()
    audio = ["-map", "1:a?", "-c:a", "copy"] if mkv or acodec in MP4_AUDIO or not acodec else \
        ["-map", "1:a?", "-c:a", "aac", "-b:a", "256k"]
    # Sous-titres lus par une 2e ouverture de la source : lus avec le son, leurs paquets espacés laissaient le son
    # partir des minutes devant l'image dans le fichier, et les lecteurs n'avaient plus de son.
    subs = ["-map", "2:s?", "-c:s", "copy"] if mkv else []
    part = dst.with_name(f"{dst.stem}.part{dst.suffix}")
    work_dir = dst.with_name(f".{dst.stem.replace('%', '_')}.morceaux")  # « % » : motif du muxer segment
    vid = dst.with_name(f"{dst.stem}.video{dst.suffix}")
    t0 = time.monotonic()
    why = "désactivé"
    try:
        stats = None
        if smart_render:
            stats, why = _smart(src, d, fb, fps, frames, work_dir, style, shape, strength, cancel, progress)
            if stats:
                video_in = stats.pop("video_in")
                _mux(video_in, src, part, audio, subs, mkv, d, float(video.get("duration") or 0), cancel, progress,
                     start=0.85)
        if stats is None:
            log.info("Full export (%s)", why)
            stats = _full(src, d, fb, fps, frames, vid, style, shape, strength, cancel, progress, use_gpu)
            stats["smart_skipped"] = why
            _mux(["-i", str(vid)], src, part, audio, subs, mkv, d, float(video.get("duration") or 0), cancel,
                 progress, start=0.95)
        try:
            os.replace(part, dst)
        except PermissionError as e:
            raise MediaLabError("permission_denied", f"Impossible de remplacer « {dst.name} ».",
                                "Le fichier est sans doute ouvert dans un lecteur vidéo : ferme-le, puis exporte à "
                                "nouveau.", True, str(e)) from e
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    finally:
        vid.unlink(missing_ok=True)
        shutil.rmtree(work_dir, ignore_errors=True)
    stats["seconds"] = round(time.monotonic() - t0, 1)
    log.info("Exported %s: %s", dst.name, stats)
    return {"file": str(dst), "size": dst.stat().st_size, "rendered_at": time.time(), **stats}


def _smart(src: Path, d: dict, fb: FrameBoxes, fps: float, frames: int, work_dir: Path, style: str, shape: str,
           strength: int, cancel: threading.Event, progress: Callable[[float, str, float | None], None]
           ) -> tuple[dict | None, str]:
    """Le rendu intelligent, ou None et la raison pour laquelle il ne s'applique pas."""
    progress(0.0, "Lecture des images-clés", None)
    try:
        pk = S.packets(src)
    except MediaLabError as e:
        return None, f"images-clés illisibles ({e.message})"
    why, keys = smart.check(d, pk, fps, frames)
    if why:
        return None, why
    if cancel.is_set():
        raise Canceled()
    frames = min(frames, len(pk["pts"]))  # images réellement présentes dans la source
    todo = smart.plan(smart.blurred_ranges(fb, frames), keys, frames)
    share = sum(b - a for a, b in todo) / max(1, frames)
    if share > smart.MAX_SHARE:
        return None, f"{share:.0%} de la vidéo à réencoder"
    if not smart.enough_space(src, work_dir.parent):
        return None, "pas assez de place pour les morceaux"
    try:
        video_in, stats = smart.run(src, d, fb, fps, frames, work_dir, style=style, shape=shape, strength=strength,
                                    cancel=cancel, progress=progress, pk=pk, keys=keys, todo=todo)
    except MediaLabError as e:
        if e.code == "disk_full":
            raise
        log.warning("Smart render failed, full export instead: %s %s", e, e.detail[-500:])
        shutil.rmtree(work_dir, ignore_errors=True)
        return None, f"rendu intelligent impossible ({e.message})"
    stats["video_in"] = video_in
    return stats, ""


def _mux(video_in: list[str], src: Path, part: Path, audio: list[str], subs: list[str], mkv: bool, d: dict,
         duration: float, cancel: threading.Event, progress: Callable[[float, str, float | None], None],
         start: float = 0.95) -> None:
    """Image floutée + son, sous-titres, chapitres de la source, bien entrelacés."""
    progress(start, "Ajout du son", None)
    vtags = [x for k, v in (d.get("tags") or {}).items() for x in ("-metadata:s:v:0", f"{k}={v}")]
    tag = []
    if not mkv and d.get("codec") == "hevc":
        tag = ["-tag:v", d["codec_tag"] if d.get("codec_tag") in ("hvc1", "hev1") else "hvc1"]
    try:
        ffmpeg.run([*video_in, "-i", str(src), *(["-i", str(src)] if subs else []), "-map", "0:v:0",
                    "-c:v", "copy", *tag, *audio, *subs,
                    "-disposition:v:0", "default", *(["-default_mode", "passthrough"] if mkv else
                                                      ["-movflags", "+faststart"]),
                    "-map_metadata", "1", *vtags, "-map_chapters", "1", str(part)],
                   duration=duration or None, cancel=cancel, label="Ajout du son",
                   on_progress=lambda f, _: progress(start + (1 - start) * f, "Ajout du son", None))
    except MediaLabError as e:
        if e.code == "disk_full":
            raise MediaLabError("disk_full", "Plus assez de place sur le disque.", "Libère de la place puis relance.",
                                True, e.detail) from e
        raise MediaLabError("export_failed", "L'ajout du son à la vidéo floutée a échoué.",
                            "Ouvre le journal pour le détail.", True, e.detail) from e


def full_encoders(d: dict, use_gpu: bool) -> list[tuple[str, list[str]]]:
    """Encodeurs à essayer pour l'export complet, dans l'ordre : même codec et même profondeur que la source (HEVC
    pour une source 10 bits autre que H.264). La carte graphique ne sait pas réécrire les métadonnées HDR10 : une
    source qui en a passe par x265."""
    codec = d.get("codec") if d.get("codec") in ("h264", "hevc") else "hevc" if d.get("depth", 8) > 8 else "h264"
    d = {**d, "codec": codec}
    ten = d.get("depth", 8) > 8
    out: list[tuple[str, list[str]]] = []
    gpu = ffmpeg.best_encoder("h265" if codec == "hevc" else codec) if use_gpu else None  # table : « h265 »
    hdr_meta = bool((d.get("hdr") or {}).get("mastering"))
    amf_8bit = bool(gpu) and gpu.endswith("_amf") and ten  # AMF n'écrit ici que du 8 bits
    if gpu and ffmpeg.is_hw(gpu) and not (codec == "h264" and ten) and not hdr_meta and not amf_8bit:
        args = ffmpeg.encoder_args(gpu, 19 if gpu.endswith("_nvenc") else 18, ten_bit=ten)
        out.append((gpu, [*args, *S.color_args(d)]))
    out.append(("libx265" if codec == "hevc" else "libx264", S.cpu_encoder_args(d)))
    return out


def _full(src: Path, d: dict, fb: FrameBoxes, fps: float, frames: int, vid: Path, style: str, shape: str,
          strength: int, cancel: threading.Event, progress: Callable[[float, str, float | None], None],
          use_gpu: bool) -> dict:
    last_err: MediaLabError | None = None
    for enc, args in full_encoders(d, use_gpu):
        try:
            return _encode(src, d, fps, frames, fb, vid, enc, args, style, shape, strength, cancel,
                           lambda f, label, eta: progress(f * 0.95, label, eta))
        except MediaLabError as e:
            vid.unlink(missing_ok=True)
            last_err = e
            if e.code == "encoder_failed" and ffmpeg.is_hw(enc):
                log.warning("GPU encoder failed; exporting on the CPU")
                continue
            raise
    assert last_err
    raise last_err


def _encode(src: Path, d: dict, fps: float, frames: int, fb: FrameBoxes, part: Path, enc: str, vargs: list[str],
            style: str, shape: str, strength: int,
            cancel: threading.Event, progress: Callable[[float, str, float | None], None]) -> dict:
    exact = int(d["width"]) % 2 == 0 and int(d["height"]) % 2 == 0
    w, h = (int(d["width"]), int(d["height"])) if exact else (FR.even(int(d["width"])), FR.even(int(d["height"])))
    work = S.work_format(d)
    rate = d.get("avg_frame_rate") if d.get("avg_frame_rate") not in (None, "0/0") else f"{fps:.6f}"
    cargs = S.color_args(d)
    cmd = [require("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", work, "-s", f"{w}x{h}", "-r", rate, *cargs, "-i", "-",
           "-map", "0:v:0", *S.frame_colors(d), *vargs, "-g", str(max(1, int(round(fps * 2)))), str(part)]
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
    reader = FR.Reader(src, fps, (w, h), work, cancel=cancel, exact=exact, rate=rate)
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
                effects.apply(effects.yuv_planes(y, u, v, d.get("full_range", False)), boxes, style, shape, strength)
                blurred += 1
            try:
                proc.stdin.write(memoryview(fr))
            except (BrokenPipeError, OSError) as e:
                raise _failure(proc, tail, enc, th) from e
            i += 1
            now = time.monotonic()
            if now - last_ui > 0.5:
                last_ui = now
                el = now - t0
                rate_now = i / el if el > 0 else 0
                eta = (frames - i) / rate_now if rate_now > 0 and frames > i else None
                progress(min(0.999, i / max(1, frames)), f"Export complet · {rate_now:.0f} images/s · {enc}", eta)
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
        raise _failure(proc, tail, enc, th)
    log.info("Encoded %s: %d frames, %d blurred, %.0fs (%s)", part.name, i, blurred, time.monotonic() - t0, enc)
    return {"mode": "full", "frames": i, "blurred_frames": blurred, "reencoded_frames": i, "reencoded_share": 1.0,
            "encoder": enc}


def _failure(proc: subprocess.Popen, tail: collections.deque, enc: str,
             drain: threading.Thread | None = None) -> MediaLabError:
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    if drain is not None:  # tout le message d'erreur, « disque plein » compris
        drain.join(timeout=5)
    detail = "\n".join(tail)
    low = detail.lower()
    log.error("Export failed (%s): %s", enc, detail[-1500:])
    if "no space left" in low:
        return MediaLabError("disk_full", "Plus assez de place sur le disque.", "Libère de la place puis relance.",
                             True, detail)
    if ffmpeg.is_hw(enc) or "nvenc" in low:
        return MediaLabError("encoder_failed", "L'encodeur vidéo de la carte graphique a échoué.", "", True, detail)
    return MediaLabError("export_failed", "L'export de la vidéo a échoué.", "Ouvre le journal pour le détail.",
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


def sample(src: Path, video: dict, fps: float, index: int, boxes: list[np.ndarray], *, style: str, shape: str,
           strength: int, max_w: int = 1920, side: int = 360) -> bytes:
    """Gros plan sur la plus grande zone de l'image, floutée comme à l'export : l'aperçu du niveau de flou."""
    w, h = int(video["width"]), int(video["height"])
    if w > max_w:
        w, h = max_w, h * max_w / w
    frame = FR.grab(src, fps, index, (FR.even(w), FR.even(h)))
    fh, fw = frame.shape[:2]
    if boxes:
        effects.apply(effects.rgb_planes(frame), boxes, style, shape, strength)
        b = max(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        cx, cy = (b[0] + b[2]) / 2 * fw, (b[1] + b[3]) / 2 * fh
        half = max((b[2] - b[0]) * fw, (b[3] - b[1]) * fh, 32) * 0.9  # la zone et un peu d'entourage
        x0, y0 = int(max(0, min(fw - 2 * half, cx - half))), int(max(0, min(fh - 2 * half, cy - half)))
        frame = frame[y0:int(y0 + 2 * half), x0:int(x0 + 2 * half)]
    img = Image.fromarray(frame)
    img.thumbnail((side, side), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue()
