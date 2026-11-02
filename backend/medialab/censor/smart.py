"""Rendu intelligent : seuls les groupes d'images (d'une image-clé à la suivante) qui contiennent du flou sont
réencodés ; tout le reste de l'image est copié tel quel, identique au bit près à la source.

1. les paquets de la source donnent les images-clés ; chaque passage flouté est étendu aux images-clés qui
   l'entourent (``plan``) ;
2. l'image de la source est découpée en morceaux à ces images-clés, sans réencodage (MPEG-TS : les paramètres du
   codec sont répétés à chaque image-clé, un morceau se décode seul) ;
3. les morceaux à flouter sont réencodés depuis la source, avec le même codec, profil, niveau, profondeur, couleurs
   et métadonnées HDR (``source.cpu_encoder_args``) ;
4. le tout est recollé (``concat``), puis le son et les sous-titres sont ajoutés (render._mux).

Si la source ne s'y prête pas (``check``), l'export complet prend le relais.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from ..components import CREATE_NO_WINDOW, require
from ..errors import Canceled, MediaLabError
from ..logging_setup import get_logger
from ..media import ffmpeg
from . import effects
from . import frames as FR
from . import source as S
from .track import FrameBoxes

log = get_logger("censor")
Progress = Callable[[float, str, float | None], None]
CODECS = {"hevc", "h264"}
CONTAINERS = {"matroska", "mov"}  # mkv / webm, mp4 / mov
MAX_SHARE = 0.6  # au-delà de 60 % de la vidéo à réencoder, l'export complet est aussi simple


def blurred_ranges(fb: FrameBoxes, frames: int) -> list[tuple[int, int]]:
    """Plages d'images (début, fin exclue) où il y a quelque chose à flouter."""
    out: list[tuple[int, int]] = []
    start = None
    for i in range(frames):
        on = bool(fb.at(i))
        if on and start is None:
            start = i
        elif not on and start is not None:
            out.append((start, i))
            start = None
    if start is not None:
        out.append((start, frames))
    return out


def plan(ranges: list[tuple[int, int]], keys: list[int], frames: int) -> list[tuple[int, int]]:
    """Plages à réencoder : chaque passage flouté étendu à l'image-clé d'avant et à celle d'après (fin exclue)."""
    keys = sorted(set(int(k) for k in keys if 0 <= k < frames))
    if not keys or keys[0] != 0:
        keys = [0, *keys]
    karr = np.array(keys)
    out: list[tuple[int, int]] = []
    for a, b in ranges:
        lo = int(karr[np.searchsorted(karr, a, side="right") - 1])
        j = np.searchsorted(karr, b, side="left")  # première image-clé ≥ b (b exclue : déjà hors du flou)
        hi = int(karr[j]) if j < len(karr) else frames
        if out and lo <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def check(d: dict, pk: dict, fps: float, frames: int) -> tuple[str | None, list[int]]:
    """Pourquoi le rendu intelligent ne s'applique pas (ou None), et les images-clés (n° d'image)."""
    if d.get("codec") not in CODECS:
        return f"codec {d.get('codec')} non pris en charge", []
    if d.get("container") not in CONTAINERS:
        return f"conteneur {d.get('container')} non pris en charge", []
    if d.get("chroma") != "420" or d.get("depth", 8) > 10:
        return f"format d'image {d.get('pix_fmt')} non pris en charge", []
    if d.get("field_order") not in ("progressive", "unknown"):
        return "vidéo entrelacée", []
    if abs(d.get("start", 0) - d.get("file_start", 0)) > 0.5 / fps:
        return "l'image ne commence pas avec le fichier", []
    pts, key = pk["pts"], pk["key"]
    if len(pts) == 0 or not key.any():
        return "images-clés introuvables", []
    # l'analyse peut compter quelques images de plus à la fin (le filtre de cadence complète jusqu'à la durée
    # annoncée par le conteneur) ; jamais moins
    if not 0 <= frames - len(pts) <= max(1, round(fps)):
        return f"cadence variable ({len(pts)} images pour {frames} attendues)", []
    order = np.sort(pts)
    grid = order[0] + np.arange(len(order)) / fps
    if np.abs(order - grid).max() > 0.5 / fps:
        return "cadence variable", []
    # GOP ouvert : des images affichées avant une image-clé mais décodées après dépendent du groupe précédent
    kpos = np.flatnonzero(key)
    for j, p in enumerate(kpos[1:], 1):
        end = kpos[j + 1] if j + 1 < len(kpos) else len(pts)
        if (pts[p + 1:end] < pts[p] - 1e-6).any():
            return "groupes d'images ouverts (open GOP)", []
    idx = np.rint((pts[kpos] - order[0]) * fps).astype(int)
    return None, sorted(set(int(i) for i in idx))


def run(src: Path, d: dict, fb: FrameBoxes, fps: float, frames: int, vid_dir: Path, *, style: str, shape: str,
        strength: int, cancel: threading.Event, progress: Progress, pk: dict, keys: list[int],
        todo: list[tuple[int, int]]) -> tuple[list[str], dict]:
    """Prépare l'image floutée (morceaux copiés + morceaux réencodés). Renvoie les arguments d'entrée FFmpeg qui la
    lisent d'un seul tenant (concat) et les chiffres du rendu."""
    vid_dir.mkdir(parents=True, exist_ok=True)
    pts0 = float(np.min(pk["pts"]))
    kpts = {int(round((p - pts0) * fps)): float(p) for p in pk["pts"][pk["key"]]}
    bounds = sorted({x for a, b in todo for x in (a, b) if 0 < x < frames})
    t0 = time.monotonic()
    total = sum(b - a for a, b in todo)

    # 1. découpe de l'image de la source aux images-clés, sans réencodage
    progress(0.0, "Découpe de la vidéo", None)
    times = ",".join(f"{kpts[x] - d['file_start'] - 0.5 / fps:.6f}" for x in bounds)
    listing = vid_dir / "parts.csv"
    args = ["-i", str(src), "-map", "0:v:0", "-c", "copy", "-f", "segment", "-segment_format", "mpegts",
            "-reset_timestamps", "0", "-segment_list", str(listing), "-segment_list_type", "csv"]
    if times:
        args += ["-segment_times", times]
    ffmpeg.run([*args, str(vid_dir / "p%05d.ts")], duration=float(frames / fps), cancel=cancel,
               label="Découpe de la vidéo", on_progress=lambda f, _: progress(0.3 * f, "Découpe de la vidéo", None))
    parts = sorted(vid_dir.glob("p*.ts"))
    starts = [0, *bounds]
    if len(parts) != len(starts):
        raise MediaLabError("smart_split", f"Découpe inattendue ({len(parts)} morceaux au lieu de {len(starts)}).")
    try:  # chaque morceau doit commencer exactement à l'image-clé prévue
        got = [float(line.split(",")[1]) for line in listing.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, ValueError, IndexError):
        got = []
    want = [kpts[x] - d["file_start"] for x in starts]
    # FFmpeg peut décaler tous les instants d'un même écart (images B en tête d'un MP4) : seul l'écart doit être fixe
    shift = got[1] - want[1] if len(got) > 1 and len(want) > 1 else 0.0
    if len(got) != len(want) or any(abs(g - w - shift) > 0.5 / fps for g, w in zip(got[1:], want[1:])):
        raise MediaLabError("smart_split", "Découpe décalée par rapport aux images-clés.", "", False,
                            f"attendu {want[:5]}, obtenu {got[:5]}")

    # 2. réencodage des morceaux à flouter
    work = S.work_format(d)
    w, h = int(d["width"]), int(d["height"])
    rate = d.get("avg_frame_rate") or d.get("r_frame_rate") or f"{fps:.6f}"
    redo = {a: b for a, b in todo}
    done = 0
    blurred = 0
    for k, a in enumerate(starts):
        if a not in redo:
            continue
        b = redo[a]
        out = vid_dir / f"r{k:05d}.ts"
        blurred += _encode_part(src, d, fb, fps, rate, a, b, w, h, work, out, style, shape, strength, cancel,
                                lambda i: progress(0.3 + 0.55 * (done + i) / max(1, total),
                                                   f"Réencodage des passages floutés · {done + i}/{total} images",
                                                   None))
        done += b - a
        parts[k] = out

    # 3. liste de recollage : durée exacte de chaque morceau (d'une image-clé à la suivante)
    lines = ["ffconcat version 1.0"]
    for k, a in enumerate(starts):
        lines.append("file '" + parts[k].as_posix().replace("'", "'\\''") + "'")  # « ' » échappée (ffconcat)
        if k + 1 < len(starts):
            lines.append(f"duration {kpts[starts[k + 1]] - kpts[a]:.6f}")
    concat = vid_dir / "concat.txt"
    concat.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stats = {"mode": "smart", "frames": frames, "blurred_frames": blurred, "reencoded_frames": total,
             "reencoded_share": round(total / max(1, frames), 4), "parts": len(starts), "encoder":
             "libx265" if d["codec"] == "hevc" else "libx264", "seconds_video": round(time.monotonic() - t0, 1)}
    return ["-f", "concat", "-safe", "0", "-i", str(concat)], stats


def _encode_part(src: Path, d: dict, fb: FrameBoxes, fps: float, rate: str, a: int, b: int, w: int, h: int, work: str,
                 out: Path, style: str, shape: str, strength: int, cancel: threading.Event,
                 tick: Callable[[int], None]) -> int:
    cargs = S.color_args(d)
    cmd = [require("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", work,
           "-s", f"{w}x{h}", "-r", rate, *cargs, "-i", "-", "-map", "0:v:0", *S.frame_colors(d), *S.cpu_encoder_args(d),
           "-f", "mpegts", str(out)]
    log.debug("smart part: %s", subprocess.list2cmdline(cmd))
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=CREATE_NO_WINDOW)
    err: list[str] = []
    th = threading.Thread(target=lambda: err.extend(l.decode("utf-8", "replace") for l in proc.stderr or []),
                          daemon=True)
    th.start()
    reader = FR.Reader(src, fps, (w, h), work, start=a, cancel=cancel, exact=True, rate=rate)
    blurred = 0
    last = 0.0
    try:
        assert proc.stdin
        for i in range(a, b):
            fr = reader.read()
            if fr is None:
                raise MediaLabError("smart_short", f"La vidéo s'arrête avant l'image {i}.")
            boxes = fb.at(i)
            if boxes:
                fr = fr.copy()
                y, u, v = FR.yuv_planes(fr, w, h)
                effects.apply(effects.yuv_planes(y, u, v, d.get("full_range", False)), boxes, style, shape, strength)
                blurred += 1
            try:
                proc.stdin.write(memoryview(fr))
            except (BrokenPipeError, OSError) as e:
                raise MediaLabError("export_failed", "Le réencodage d'un passage a échoué.", "Voir le journal.", True,
                                    "".join(err)[-1500:]) from e
            if time.monotonic() - last > 0.5:
                last = time.monotonic()
                tick(i - a)
        proc.stdin.close()
        proc.wait()
    except BaseException:
        proc.kill()
        raise
    finally:
        reader.close()
        th.join(timeout=3)
    if cancel.is_set():
        raise Canceled()
    if proc.returncode != 0:
        detail = "".join(err)
        log.error("Smart part failed: %s", detail[-1500:])
        if "no space left" in detail.lower():
            raise MediaLabError("disk_full", "Plus assez de place sur le disque.", "Libère de la place puis relance.",
                                True, detail)
        raise MediaLabError("export_failed", "Le réencodage d'un passage a échoué.", "Voir le journal.", True, detail)
    return blurred


def space_needed(src: Path) -> int:
    """Place à prévoir : les morceaux (au plus la taille de la source) et la vidéo finale."""
    return int(src.stat().st_size * 2.1)


def enough_space(src: Path, where: Path) -> bool:
    try:
        return shutil.disk_usage(where).free > space_needed(src)
    except OSError:
        return True
