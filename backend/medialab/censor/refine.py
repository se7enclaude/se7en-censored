"""La 2e passe : seulement autour des moments où le détecteur a vu de la nudité (quelques % d'une vidéo).

  - l'image est relue 1,5 fois plus grande et analysée en tuiles : les parties petites, lointaines ou mal cadrées
    ressortent, et leurs boîtes sont plus justes ;
  - CLIP vérifie chaque zone (colonne « v » des détections, voir verify.py) ;
  - les changements de plan sont repérés (``cuts`` : première image de chaque nouveau plan) : une zone ne passe jamais
    d'un plan à l'autre.

Elle tourne après l'analyse, et seule sur une vidéo déjà analysée par une version précédente (pas besoin de tout
refaire). Interrompue, elle repart de zéro la fois suivante : elle est courte.
"""
from __future__ import annotations

import collections
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from ..errors import Canceled
from ..logging_setup import get_logger
from . import detect as D
from . import follow as FW
from . import frames as FR
from . import verify as V
from .track import CLASS_IDS, LABELS, RAW_MIN_SCORE

log = get_logger("censor")
REFINE_VERSION = 1
HOT = 0.15          # une détection à partir de 0,15 (le seuil le plus bas de l'appli) mérite la 2e passe
FINE_S = 0.5        # tuiles et vérification ±0,5 s autour
CUT_S = 2.0         # changements de plan cherchés ±2 s autour (zones suivies à l'image, marges avant / après)
SCALE = 1.5         # image relue 1,5 fois plus grande que pour l'analyse
CUT_MIN = 0.08      # écart moyen minimal entre deux images pour un changement de plan (0..1)
WANTED = set(CLASS_IDS.values())
KEEP = WANTED | {LABELS.index("MALE_BREAST_EXPOSED")}  # torse masculin : sert à écarter les pecs (track.py)


def plan(det: dict, frames: int, fps: float) -> tuple[np.ndarray, list[tuple[int, int]]]:
    """Images à passer en tuiles (masque) et plages à relire (début, fin incluse)."""
    f, c, s, t = (np.asarray(det[k]) for k in ("f", "c", "s", "t"))
    hot = np.unique(f[np.isin(c, list(WANTED)) & (t == 0) & (s >= HOT)])
    fine = np.zeros(frames, bool)
    span = np.zeros(frames, bool)
    r1, r2 = int(round(FINE_S * fps)), int(round(CUT_S * fps))
    for x in hot:
        fine[max(0, x - r1): x + r1 + 1] = True
        span[max(0, x - r2): x + r2 + 1] = True
    segs, start = [], None
    for i in np.flatnonzero(np.diff(np.concatenate([[0], span.astype(np.int8), [0]]))):
        if start is None:
            start = int(i)
        else:
            segs.append((start, int(i) - 1))
            start = None
    return fine, segs


class Cuts:
    """Changements de plan francs : l'image change d'un coup, bien plus que d'habitude dans ce plan."""

    def __init__(self) -> None:
        self.prev: np.ndarray | None = None
        self.recent: collections.deque[float] = collections.deque(maxlen=8)

    def step(self, rgb: np.ndarray) -> bool:
        g = FW.gray(rgb[::4, ::4])
        if self.prev is None or self.prev.shape != g.shape:
            self.prev = g
            return False
        d = float(np.abs(g - self.prev).mean()) / 255
        usual = float(np.median(self.recent)) if self.recent else 0.0
        self.prev = g
        cut = d >= CUT_MIN and d >= 4 * usual + 0.02  # mouvement rapide : écart fort mais habituel, pas une coupe
        if cut:
            self.recent.clear()  # nouveau plan : son mouvement habituel est à réapprendre
        else:
            self.recent.append(d)
        return cut


def tiles(w: int, h: int, side: int = D.SIDE) -> list[tuple[int, int, int, int]]:
    """Tuiles d'au plus ``side`` px qui se chevauchent de moitié et couvrent toute l'image."""
    def starts(n: int) -> list[int]:
        if n <= side:
            return [0]
        k = int(np.ceil((n - side) / (side / 2))) + 1
        return [int(round(v)) for v in np.linspace(0, n - side, k)]
    return [(x, y, min(w, x + side), min(h, y + side)) for y in starts(h) for x in starts(w)]


def detect_tiles(detector: D.Detector, rgb: np.ndarray) -> list[tuple[int, float, list[float]]]:
    """Détections sur les tuiles, en coordonnées de l'image entière. Une boîte coupée par le bord d'une tuile (et
    pas par celui de l'image) est ignorée : une autre tuile la voit entière."""
    h, w = rgb.shape[:2]
    out = []
    for x0, y0, x1, y1 in tiles(w, h):
        tw, th = x1 - x0, y1 - y0
        for c, s, b in detector.run(np.ascontiguousarray(rgb[y0:y1, x0:x1])):
            if c not in KEEP:
                continue
            px = [b[0] * tw + x0, b[1] * th + y0, b[2] * tw + x0, b[3] * th + y0]
            cut_edge = (x0 > 0 and px[0] <= x0 + 2) or (x1 < w and px[2] >= x1 - 2) \
                or (y0 > 0 and px[1] <= y0 + 2) or (y1 < h and px[3] >= y1 - 2)
            if not cut_edge:
                out.append((c, s, [px[0] / w, px[1] / h, px[2] / w, px[3] / h]))
    return out


def run(src: Path, video: dict, cache: Path, det: dict, meta: dict, *, device: str = "auto",
        cancel: threading.Event, progress: Callable[[float, str, float | None], None]) -> tuple[dict, dict]:
    fps = float(meta["fps"])
    frames = int(meta["frames"])
    fine, segs = plan(det, frames, fps)
    total = sum(b - a + 1 for a, b in segs)
    log.info("Second pass: %d frames in %d ranges (%.1f %% of the video)", total, len(segs), 100 * total / max(1, frames))
    f, c, s, b, t = (np.asarray(det[k]) for k in ("f", "c", "s", "b", "t"))
    keep_rows = ~((t == 0) & fine[np.clip(f, 0, frames - 1)])  # remplacées par la fusion (détection + tuiles)
    old_by_frame: dict[int, list[int]] = {}
    for i in np.flatnonzero(~keep_rows):
        old_by_frame.setdefault(int(f[i]), []).append(int(i))
    rows: list[tuple[int, int, float, list[float], int, float]] = []
    cuts: list[int] = []
    if total:
        progress(0.0, "Chargement de la vérification", None)
        detector = D.Detector(device)
        verifier = V.Verifier(device, lambda d, n: progress(0.0, f"Téléchargement de la vérification · "
                                                                 f"{d * 100 // max(n, 1)} %", None))
        size = FR.analysis_size(int(video["width"]), int(video["height"]), int(round(D.SIDE * SCALE)))
        done, t0, last_ui = 0, time.monotonic(), 0.0
        for a, z in segs:
            reader = FR.Reader(src, fps, size, "rgb24", start=a, cancel=cancel)
            finder = Cuts()
            try:
                for i in range(a, z + 1):
                    if cancel.is_set():
                        raise Canceled()
                    img = reader.read()
                    if img is None:
                        break
                    if finder.step(img):
                        cuts.append(i)
                    if fine[i]:
                        found = [(int(c[k]), float(s[k]), [float(v) for v in b[k]]) for k in old_by_frame.get(i, [])]
                        found = D._merge(found + detect_tiles(detector, img))
                        check = [k for k, r in enumerate(found) if r[0] in WANTED and r[1] >= RAW_MIN_SCORE]
                        notes = dict(zip(check, verifier.frame(img, [found[k][2] for k in check])))
                        for k, (cc, ss, bb) in enumerate(found):
                            rows.append((i, cc, ss, bb, 0, notes.get(k, np.nan)))
                    done += 1
                    now = time.monotonic()
                    if now - last_ui > 0.5:
                        last_ui = now
                        rate = done / max(1e-6, now - t0)
                        progress(done / total, f"Vérification des zones · {rate:.0f} images/s", (total - done) / max(rate, 1e-6))
            finally:
                reader.close()
    n = int(keep_rows.sum())
    out = {
        "f": np.concatenate([f[keep_rows], np.array([r[0] for r in rows], np.int32)]).astype(np.int32),
        "c": np.concatenate([c[keep_rows], np.array([r[1] for r in rows], np.int8)]).astype(np.int8),
        "s": np.concatenate([s[keep_rows], np.array([r[2] for r in rows], np.float16)]).astype(np.float16),
        "b": np.concatenate([b[keep_rows].reshape(-1, 4), np.array([r[3] for r in rows], np.float32).reshape(-1, 4)]),
        "t": np.concatenate([t[keep_rows], np.array([r[4] for r in rows], np.int8)]).astype(np.int8),
        "v": np.concatenate([np.full(n, np.nan, np.float16), np.array([r[5] for r in rows], np.float16)]),
        "cuts": np.array(sorted(set(cuts)), np.int32),
    }
    meta = {**meta, "refined": REFINE_VERSION}
    D.save_arrays(cache, out, meta)
    log.info("Second pass done: %d checked detections, %d shot changes", int(np.isfinite(out["v"]).sum()), len(cuts))
    return out, meta
