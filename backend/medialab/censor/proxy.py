"""Copie d'aperçu : une version légère de la vidéo que la fenêtre de l'appli lit sans problème (H.264 + AAC en MP4,
1280 px au plus, couleurs HDR ramenées en SDR), pour la vérification. Même cadence que l'analyse et images
numérotées depuis 0 : l'image n de l'aperçu est l'image n de l'analyse et de l'export (instant n / fps).

Seulement quand la vidéo d'origine ne se lit pas telle quelle (MKV, HEVC, DTS, HDR…). La copie vit dans le cache
du projet : elle part avec lui."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from ..errors import MediaLabError
from ..logging_setup import get_logger
from ..media import ffmpeg
from ..tools import playback

log = get_logger("censor")
MAX_W = 1280


def needed(src: Path) -> bool:
    """La fenêtre de l'appli (Chromium) ne lit pas cette vidéo telle quelle, ou pas avec les bonnes couleurs."""
    try:
        plan = playback.plan(src)
    except MediaLabError:
        return True
    return not (plan.get("mode") == "direct" and plan.get("vcodec") in playback.BROWSER_VCODECS
                and not plan.get("is_hdr"))


def path_for(cache: Path) -> Path:
    return cache / "preview.mp4"


def encoder_args() -> list[str]:
    enc = ffmpeg.best_encoder("h264") or "libx264"
    if enc.endswith("_nvenc"):
        return ["-c:v", enc, "-preset", "p4", "-rc", "vbr", "-cq", "25", "-b:v", "0"]
    if enc.endswith("_qsv"):
        return ["-c:v", enc, "-preset", "fast", "-global_quality", "25"]
    if enc.endswith("_amf"):
        return ["-c:v", enc, "-quality", "speed", "-rc", "cqp", "-qp_i", "25", "-qp_p", "25"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "24"]


def build(src: Path, video: dict, dst: Path, cancel: threading.Event,
          progress: Callable[[float], None] | None = None) -> dict:
    """Fabrique la copie d'aperçu ``dst`` (écrite à côté puis renommée : jamais de copie à moitié faite)."""
    fps = float(video.get("fps") or 25.0)
    w = int(video.get("width") or MAX_W)
    out_w = min(MAX_W, w - w % 2)
    chain = [f"fps={fps:.6f}", f"scale={out_w}:-2:flags=area"]
    if video.get("is_hdr"):
        from ..upscale.filters import TONEMAP
        chain.append(TONEMAP)  # après la réduction : le passage en SDR coûte peu à 1280 px
    chain += ["format=yuv420p", "setpts=N/FRAME_RATE/TB"]  # image n à l'instant n / fps, depuis 0
    part = dst.with_name(dst.stem + ".part.mp4")
    gop = max(1, round(fps))  # une image-clé par seconde : les sauts dans la vidéo sont immédiats
    args = ["-hwaccel", "auto", "-i", str(src), "-map", "0:v:0", "-map", "0:a:0?", "-vf", ",".join(chain),
            *encoder_args(), "-g", str(gop), "-keyint_min", str(gop), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-ac", "2", "-sn", "-dn", "-map_metadata", "-1",
            "-movflags", "+faststart", str(part)]
    try:
        ffmpeg.run(args, duration=float(video.get("duration") or 0) or None, cancel=cancel,
                   label="censor preview", on_progress=(lambda f, _: progress(f)) if progress else None)
        part.replace(dst)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    log.info("Preview copy ready: %s (%.0f MB)", dst.name, dst.stat().st_size / 1e6)
    return info(dst, video)


def info(dst: Path, video: dict) -> dict:
    """Ce que l'interface doit savoir de la copie d'aperçu."""
    w = int(video.get("width") or MAX_W)
    out_w = min(MAX_W, w - w % 2)
    h = int(round(out_w * int(video.get("height") or out_w) / max(1, w) / 2)) * 2
    return {"path": str(dst), "width": out_w, "height": h, "fps": float(video.get("fps") or 25.0)}


class Background:
    """Fabrication de la copie en parallèle de l'analyse (elle décode la vidéo de son côté)."""

    def __init__(self, src: Path, video: dict, dst: Path, cancel: threading.Event) -> None:
        self.frac = 0.0
        self.result: dict | None = None
        self.error: BaseException | None = None
        self._th = threading.Thread(target=self._run, args=(src, video, dst, cancel), name="censor-preview",
                                    daemon=True)
        self._th.start()

    def _run(self, src: Path, video: dict, dst: Path, cancel: threading.Event) -> None:
        try:
            self.result = build(src, video, dst, cancel, lambda f: setattr(self, "frac", f))
        except BaseException as e:  # noqa: BLE001 - lu par l'analyse à la fin
            self.error = e

    def wait(self, tick: Callable[[float], None], cancel: threading.Event) -> None:
        while self._th.is_alive():
            if cancel.is_set():
                break
            tick(self.frac)
            self._th.join(timeout=0.5)
