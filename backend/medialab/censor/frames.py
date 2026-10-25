"""Lecture et écriture d'images brutes par FFmpeg (tubes), à cadence constante.

Analyse et rendu lisent la vidéo avec le même filtre ``fps`` : l'image n° i est la même des deux côtés, même pour une
vidéo à cadence variable (capture d'écran, téléphone).
"""
from __future__ import annotations

import collections
import subprocess
import threading
from pathlib import Path
from typing import Iterator

import numpy as np

from ..components import CREATE_NO_WINDOW, require
from ..errors import Canceled, MediaLabError
from ..logging_setup import get_logger

log = get_logger("censor")


def even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def analysis_size(w: int, h: int, side: int = 640) -> tuple[int, int]:
    """Taille d'analyse : grand côté à ``side`` pixels, proportions gardées, dimensions paires."""
    if w >= h:
        return side, even(h * side / w)
    return even(w * side / h), side


class Reader:
    """Images d'une vidéo, une par une (``rgb24`` ou ``yuv420p``), à partir de l'image ``start``."""

    def __init__(self, src: Path, fps: float, size: tuple[int, int], pix_fmt: str = "rgb24", start: int = 0,
                 cancel: threading.Event | None = None, threads: int = 0) -> None:
        self.w, self.h = size
        self.pix_fmt = pix_fmt
        self.frame_bytes = self.w * self.h * 3 if pix_fmt == "rgb24" else self.w * self.h * 3 // 2
        self.cancel = cancel
        self.index = start
        seek = ["-ss", f"{start / fps:.6f}"] if start else []
        vf = f"fps={fps:.6f},scale={self.w}:{self.h}:flags={'area' if self.w < 1000 else 'bicubic'},format={pix_fmt}"
        cmd = [require("ffmpeg"), "-hide_banner", "-nostdin", "-v", "error", *seek, "-i", str(src),
               "-map", "0:v:0", "-vf", vf, "-threads", str(threads), "-f", "rawvideo", "-pix_fmt", pix_fmt, "-"]
        log.debug("reader: %s", subprocess.list2cmdline(cmd))
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                     creationflags=CREATE_NO_WINDOW, bufsize=self.frame_bytes * 4)
        self._err: collections.deque[str] = collections.deque(maxlen=30)
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self) -> None:
        assert self.proc.stderr
        for line in self.proc.stderr:
            self._err.append(line.decode("utf-8", "replace").rstrip())

    def read(self) -> np.ndarray | None:
        """Image suivante (``h×w×3`` en rgb24, plan ``(h*3/2)×w`` en yuv420p) ou None à la fin."""
        if self.cancel is not None and self.cancel.is_set():
            self.close()
            raise Canceled()
        assert self.proc.stdout
        buf = self.proc.stdout.read(self.frame_bytes)
        if not buf or len(buf) < self.frame_bytes:
            self.proc.wait()
            if self.proc.returncode not in (0, None) and not buf:
                detail = "\n".join(self._err)
                raise MediaLabError("decode_failed", "La vidéo n'a pas pu être lue.",
                                    "Le fichier est peut-être abîmé ; essayez de le convertir en MP4.", True, detail)
            return None
        a = np.frombuffer(buf, np.uint8)
        a = a.reshape(self.h, self.w, 3) if self.pix_fmt == "rgb24" else a.reshape(self.h * 3 // 2, self.w)
        self.index += 1
        return a

    def __iter__(self) -> Iterator[np.ndarray]:
        while (f := self.read()) is not None:
            yield f

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def yuv_planes(frame: np.ndarray, w: int, h: int) -> list[np.ndarray]:
    """Vues (sans copie) sur les plans Y, U, V d'une image yuv420p."""
    flat = frame.reshape(-1)
    y = flat[: w * h].reshape(h, w)
    u = flat[w * h: w * h + (w // 2) * (h // 2)].reshape(h // 2, w // 2)
    v = flat[w * h + (w // 2) * (h // 2):].reshape(h // 2, w // 2)
    return [y, u, v]


def grab(src: Path, fps: float, index: int, size: tuple[int, int]) -> np.ndarray:
    """Une seule image (rgb24) : la n° ``index`` (même numérotation que l'analyse et le rendu)."""
    # on se place un peu avant puis on lit jusqu'à l'image voulue : le même filtre fps que l'analyse
    back = min(index, int(round(fps)))
    r = Reader(src, fps, size, "rgb24", start=index - back)
    try:
        frame = None
        for _ in range(back + 1):
            nxt = r.read()
            if nxt is None:
                break
            frame = nxt
        if frame is None:
            raise MediaLabError("frame_extract_failed", "Impossible de lire cette image de la vidéo.",
                                "Choisissez un autre moment.", True)
        return frame.copy()
    finally:
        r.close()
