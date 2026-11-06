"""Le détecteur de nudité (NudeNet v3, YOLOv8 « 640m », ONNX) et l'analyse d'une vidéo entière.

Chaque image est réduite (grand côté 640 px) et complétée en bas / à droite jusqu'à un multiple de 32, comme à
l'entraînement de NudeNet. Les détections brutes (seuil bas) sont gardées : la sensibilité choisie ensuite filtre sans
tout réanalyser. L'analyse est reprise là où elle s'était arrêtée (sauvegarde régulière).
"""
from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from .. import ai_assets
from ..errors import Canceled
from ..logging_setup import get_logger
from ..upscale import onnx_rt
from . import follow as FW
from . import frames as FR
from .track import CLASS_IDS, LABELS, RAW_MIN_SCORE

log = get_logger("censor")
MODEL = "nudenet_640m"
SIDE = 640
DETECT_VERSION = 2  # 2 : suivi visuel des zones entre les détections (colonne « t »)
CHECKPOINT_EVERY = 1500  # images
FOLLOW_OPEN = 0.3   # confiance minimale pour commencer à suivre une zone à l'image
FOLLOW_KEEP_S = 1.5  # une zone perdue par le détecteur est suivie à l'image jusqu'à 1,5 s
FOLLOW_MIN = 0.55   # ressemblance minimale avec la zone telle qu'elle était vue


def _nms(boxes: np.ndarray, scores: np.ndarray, thr: float = 0.45) -> list[int]:
    order = scores.argsort()[::-1]
    keep: list[int] = []
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    while order.size:
        i = int(order[0])
        keep.append(i)
        rest = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        ov = inter / (area[i] + area[rest] - inter + 1e-9)
        order = rest[ov < thr]
    return keep


def decode(out: np.ndarray, w: int, h: int, min_score: float = RAW_MIN_SCORE) -> list[tuple[int, float, list[float]]]:
    """Sortie du modèle (22 × N) → [(classe, confiance, [x1, y1, x2, y2] normalisés)], après NMS par classe."""
    o = out.T  # (N, 22)
    sc = o[:, 4:]
    cls = sc.argmax(1)
    conf = sc.max(1)
    m = conf >= min_score
    if not m.any():
        return []
    o, cls, conf = o[m], cls[m], conf[m]
    b = np.stack([o[:, 0] - o[:, 2] / 2, o[:, 1] - o[:, 3] / 2, o[:, 0] + o[:, 2] / 2, o[:, 1] + o[:, 3] / 2], 1)
    b /= np.array([w, h, w, h], np.float32)
    b = np.clip(b, 0.0, 1.0)
    res = []
    for c in np.unique(cls):
        idx = np.where(cls == c)[0]
        for k in _nms(b[idx], conf[idx]):
            i = idx[k]
            res.append((int(c), float(conf[i]), [float(v) for v in b[i]]))
    return res


class Detector:
    def __init__(self, device: str = "auto", progress: Callable[[int, int], None] | None = None) -> None:
        path = ai_assets.ensure(MODEL, progress)
        self.sess, self.lock = onnx_rt.session(path, device)
        self.input = self.sess.get_inputs()[0].name
        self.gpu = onnx_rt.uses_gpu(device)

    def run(self, rgb: np.ndarray, mirror: bool = False) -> list[tuple[int, float, list[float]]]:
        """``rgb`` : image réduite (h×w×3, uint8). ``mirror`` : analyse aussi l'image retournée (plus sûr)."""
        h, w = rgb.shape[:2]
        ph, pw = -(-h // 32) * 32, -(-w // 32) * 32
        canvas = np.zeros((ph, pw, 3), np.uint8)
        canvas[:h, :w] = rgb
        x = canvas.transpose(2, 0, 1)[None].astype(np.float32) * (1 / 255.0)
        with self.lock:
            out = self.sess.run(None, {self.input: x})[0][0]
        res = decode(out, w, h)
        if mirror:
            canvas[:h, :w] = rgb[:, ::-1]
            x = canvas.transpose(2, 0, 1)[None].astype(np.float32) * (1 / 255.0)
            with self.lock:
                out = self.sess.run(None, {self.input: x})[0][0]
            for c, s, b in decode(out, w, h):
                res.append((c, s, [1 - b[2], b[1], 1 - b[0], b[3]]))
            res = _merge(res)
        return res


def _merge(res: list[tuple[int, float, list[float]]]) -> list[tuple[int, float, list[float]]]:
    if not res:
        return res
    out = []
    cls = np.array([r[0] for r in res])
    conf = np.array([r[1] for r in res], np.float32)
    boxes = np.array([r[2] for r in res], np.float32)
    for c in np.unique(cls):
        idx = np.where(cls == c)[0]
        for k in _nms(boxes[idx], conf[idx]):
            i = idx[k]
            out.append((int(c), float(conf[i]), boxes[i].tolist()))
    return out


# ------------------------------------------------------------------ suivi des zones pendant l'analyse
class Watch:
    """Zones vues récemment : quand le détecteur ne les retrouve plus, on les suit à l'image (ressemblance)."""

    def __init__(self, keep_frames: float, step: int) -> None:
        self.keep = keep_frames
        self.reach = 0.6 * step  # une image sur deux en mode rapide : la zone a pu bouger deux fois plus
        self.zones: list[dict] = []  # {cls, f (dernière vue par le détecteur), fw (suivi)}
        self.wanted = set(CLASS_IDS.values())

    def step(self, i: int, rgb: np.ndarray, found: list[tuple[int, float, list[float]]]) -> list[tuple[int, float, list[float]]]:
        g = None
        seen: set[int] = set()
        for c, s, b in sorted(found, key=lambda x: -x[1]):
            if c not in self.wanted:
                continue
            k = self._match(c, b, seen)
            if k is None and s < FOLLOW_OPEN:
                continue
            g = FW.gray(rgb) if g is None else g
            if k is None:
                self.zones.append({"cls": c, "f": i, "fw": FW.Follower(g, b)})
                seen.add(len(self.zones) - 1)
            else:
                z = self.zones[k]
                z["f"] = i
                z["fw"].refresh(g, b)
                seen.add(k)
        out = []
        keep = []
        for k, z in enumerate(self.zones):
            if k in seen:
                keep.append(z)
                continue
            if i - z["f"] > self.keep or not z["fw"].ok:
                continue
            g = FW.gray(rgb) if g is None else g
            r = z["fw"].track(g, self.reach)
            if r is None or r[1] < FOLLOW_MIN:
                continue
            out.append((z["cls"], float(r[1]), r[0].tolist()))
            keep.append(z)
        self.zones = keep
        return out

    def _match(self, c: int, b: list[float], taken: set[int]) -> int | None:
        best, best_v = None, 0.1
        bb = np.asarray(b, np.float32)
        for k, z in enumerate(self.zones):
            if k in taken or z["cls"] != c:
                continue
            a = z["fw"].box
            x1, y1 = max(a[0], bb[0]), max(a[1], bb[1])
            x2, y2 = min(a[2], bb[2]), min(a[3], bb[3])
            inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
            u = (a[2] - a[0]) * (a[3] - a[1]) + (bb[2] - bb[0]) * (bb[3] - bb[1]) - inter
            v = inter / u if u > 0 else 0.0
            if v > best_v:
                best, best_v = k, v
        return best


# ------------------------------------------------------------------ analyse d'une vidéo
def _store(cache: Path) -> tuple[Path, Path]:
    return cache / "detections.npz", cache / "detections.json"


def load(cache: Path) -> tuple[dict, dict] | None:
    npz, meta = _store(cache)
    if not (npz.exists() and meta.exists()):
        return None
    m = json.loads(meta.read_text("utf-8"))
    if m.get("v") not in (1, DETECT_VERSION):
        return None
    with np.load(npz) as z:
        det = {k: z[k] for k in ("f", "c", "s", "b")}
        det["t"] = z["t"] if "t" in z.files else np.zeros(len(det["f"]), np.int8)
        if "v" in z.files:  # 2e passe (refine.py) : note de vérification, changements de plan
            det["v"] = z["v"]
            det["cuts"] = z["cuts"]
    return det, m


def save_arrays(cache: Path, det: dict, meta: dict) -> None:
    npz, mf = _store(cache)
    tmp = npz.with_name("detections.tmp.npz")
    np.savez_compressed(tmp, **det)
    tmp.replace(npz)
    mf.write_text(json.dumps(meta), "utf-8")


def _save(cache: Path, rows: list, meta: dict) -> None:
    npz, mf = _store(cache)
    if rows:
        f = np.array([r[0] for r in rows], np.int32)
        c = np.array([r[1] for r in rows], np.int8)
        s = np.array([r[2] for r in rows], np.float16)
        b = np.array([r[3] for r in rows], np.float32).reshape(-1, 4)
        t = np.array([r[4] if len(r) > 4 else 0 for r in rows], np.int8)
    else:
        f, c, s, b = (np.zeros(0, np.int32), np.zeros(0, np.int8), np.zeros(0, np.float16), np.zeros((0, 4), np.float32))
        t = np.zeros(0, np.int8)
    tmp = npz.with_name("detections.tmp.npz")
    np.savez_compressed(tmp, f=f, c=c, s=s, b=b, t=t)
    tmp.replace(npz)
    mf.write_text(json.dumps(meta), "utf-8")


def merge_ranges(ranges) -> list[list[int]]:
    """Plages d'images [début, fin exclue] triées et fusionnées."""
    out: list[list[int]] = []
    for a, b in sorted((int(a), int(b)) for a, b in ranges if int(b) > int(a)):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def subtract_ranges(want, have) -> list[list[int]]:
    """Parties de ``want`` qui ne sont pas dans ``have``."""
    out = []
    have = merge_ranges(have)
    for a, b in merge_ranges(want):
        cur = a
        for c, d in have:
            if d <= cur or c >= b:
                continue
            if c > cur:
                out.append([cur, c])
            cur = max(cur, d)
        if cur < b:
            out.append([cur, b])
    return out


def analyze(src: Path, video: dict, cache: Path, *, step: int = 1, mirror: bool = False, device: str = "auto",
            cancel: threading.Event, progress: Callable[[float, str, float | None], None],
            ranges: list | None = None) -> tuple[dict, dict]:
    """Passe le détecteur sur la vidéo (une image sur ``step``). Reprend une analyse interrompue.

    ``ranges`` : seulement ces plages d'images [début, fin exclue] (passages donnés par timecodes). Ce qui a déjà
    été analysé (``covered``) est gardé : un passage ajouté plus tard est analysé seul."""
    if ranges is not None:
        return _analyze_ranges(src, video, cache, step=step, mirror=mirror, device=device, cancel=cancel,
                               progress=progress, ranges=ranges)
    fps = float(video.get("fps") or 25.0)
    total = max(1, int(round(float(video.get("duration") or 0) * fps)))
    w, h = FR.analysis_size(int(video["width"]), int(video["height"]), SIDE)
    meta = {"v": DETECT_VERSION, "fps": fps, "step": step, "mirror": mirror, "size": [w, h], "frames": total,
            "next": 0, "done": False}
    rows: list = []
    prev = load(cache)
    same = bool(prev) and prev[1].get("v") == DETECT_VERSION and prev[1].get("step") == step \
        and prev[1].get("mirror") == mirror and abs(prev[1].get("fps", 0) - fps) < 1e-3
    if same and prev[1].get("covered") is not None:  # analyse limitée à des passages : on la complète
        return _analyze_ranges(src, video, cache, step=step, mirror=mirror, device=device, cancel=cancel,
                               progress=progress, ranges=[[0, total]], full=True)
    if same and not prev[1].get("done"):
        det, meta = prev
        rows = [(int(f), int(c), float(s), b.tolist(), int(t))
                for f, c, s, b, t in zip(det["f"], det["c"], det["s"], det["b"], det["t"])]
        log.info("Resuming the analysis at frame %d / %d", meta["next"], total)
    elif same:
        return prev

    progress(0.0, "Chargement du détecteur", None)
    detector = Detector(device, lambda d, t: progress(0.0, f"Téléchargement du détecteur · {d * 100 // max(t, 1)} %", None))
    start = int(meta["next"])
    reader = FR.Reader(src, fps, (w, h), "rgb24", start=start, cancel=cancel)
    q: queue.Queue = queue.Queue(maxsize=48)
    err: list[BaseException] = []

    def feed() -> None:  # décodage en parallèle de l'IA
        try:
            i = start
            while (fr := reader.read()) is not None:
                if i % step == 0:
                    q.put((i, fr))
                i += 1
        except BaseException as e:  # noqa: BLE001 - remonté au fil principal
            err.append(e)
        finally:
            q.put(None)

    th = threading.Thread(target=feed, name="censor-decode", daemon=True)
    th.start()
    watch = Watch(fps * FOLLOW_KEEP_S, step)
    t0 = time.monotonic()
    done_frames = 0
    last_ui = 0.0
    last_idx = start
    try:
        while True:
            item = q.get()
            if item is None:
                break
            if cancel.is_set():
                raise Canceled()
            i, fr = item
            found = detector.run(fr, mirror)
            for c, s, b in found:
                rows.append((i, c, s, b, 0))
            for c, s, b in watch.step(i, fr, found):  # zones perdues par le détecteur, suivies à l'image
                rows.append((i, c, s, b, 1))
            done_frames += 1
            last_idx = i
            if done_frames % CHECKPOINT_EVERY == 0:
                _save(cache, rows, {**meta, "next": i + 1})
            now = time.monotonic()
            if now - last_ui > 0.5:
                last_ui = now
                el = now - t0
                rate = done_frames * step / el if el > 0 else 0
                frac = min(1.0, (i + 1) / total)
                eta = (total - i - 1) / rate if rate > 0 else None
                progress(frac, f"Analyse des images · {rate:.0f} images/s", eta)
        if err:
            raise err[0]
    except BaseException:
        _save(cache, rows, {**meta, "next": last_idx + 1 if done_frames else start})
        raise
    finally:
        reader.close()
        # le fil de décodage peut attendre une place dans la file : on la vide pour qu'il se termine
        deadline = time.monotonic() + 5
        while th.is_alive() and time.monotonic() < deadline:
            try:
                q.get(timeout=0.1)
            except queue.Empty:
                pass
        th.join(timeout=1)
    frames_seen = max(total, last_idx + 1)
    meta = {**meta, "frames": frames_seen, "next": frames_seen, "done": True,
            "seconds": round(time.monotonic() - t0, 1), "gpu": detector.gpu}
    _save(cache, rows, meta)
    log.info("Analysis done: %d frames, %d raw detections in %.0fs (%s)", frames_seen, len(rows), meta["seconds"],
             "GPU" if detector.gpu else "CPU")
    return load(cache)  # type: ignore[return-value]


def _rows(det: dict) -> list:
    return [(int(f), int(c), float(s), b.tolist(), int(t))
            for f, c, s, b, t in zip(det["f"], det["c"], det["s"], det["b"], det["t"])]


def _analyze_ranges(src: Path, video: dict, cache: Path, *, step: int, mirror: bool, device: str,
                    cancel: threading.Event, progress: Callable[[float, str, float | None], None],
                    ranges: list, full: bool = False) -> tuple[dict, dict]:
    """Analyse seulement les plages demandées qui ne l'ont pas encore été (voir ``analyze``)."""
    fps = float(video.get("fps") or 25.0)
    total = max(1, int(round(float(video.get("duration") or 0) * fps)))
    ranges = merge_ranges([[max(0, int(a)), min(total, int(b))] for a, b in ranges])
    w, h = FR.analysis_size(int(video["width"]), int(video["height"]), SIDE)
    prev = load(cache)
    same = bool(prev) and prev[1].get("v") == DETECT_VERSION and prev[1].get("step") == step \
        and prev[1].get("mirror") == mirror and abs(prev[1].get("fps", 0) - fps) < 1e-3
    if same and prev[1].get("covered") is None and prev[1].get("done"):
        return prev  # toute la vidéo est déjà analysée
    covered = merge_ranges(prev[1].get("covered") or []) if same else []
    rows: list = _rows(prev[0]) if same else []
    meta = {**(prev[1] if same else {}), "v": DETECT_VERSION, "fps": fps, "step": step, "mirror": mirror,
            "size": [w, h], "frames": total, "covered": covered, "done": False}
    todo = subtract_ranges(ranges, covered)
    if not todo and same and prev[1].get("done"):
        return prev
    if todo:
        meta.pop("refined", None)  # de nouvelles images : la 2e passe est à refaire
    need = sum(b - a for a, b in todo)
    log.info("Analysing %d frames in %d passages (%.1f %% of the video)", need, len(todo), 100 * need / total)
    t0 = time.monotonic()
    seen = 0
    gpu = bool(meta.get("gpu"))
    if todo:
        progress(0.0, "Chargement du détecteur", None)
        detector = Detector(device, lambda d, t: progress(0.0, f"Téléchargement du détecteur · {d * 100 // max(t, 1)} %",
                                                          None))
        gpu = detector.gpu
        last_ui = 0.0
        for a, b in todo:
            reader = FR.Reader(src, fps, (w, h), "rgb24", start=a, cancel=cancel)
            watch = Watch(fps * FOLLOW_KEEP_S, step)
            i = a
            try:
                while i < b:
                    fr = reader.read()
                    if fr is None:
                        break
                    if i % step == 0:
                        found = detector.run(fr, mirror)
                        rows.extend((i, c, sc, bx, 0) for c, sc, bx in found)
                        rows.extend((i, c, sc, bx, 1) for c, sc, bx in watch.step(i, fr, found))
                    i += 1
                    seen += 1
                    now = time.monotonic()
                    if now - last_ui > 0.5:
                        last_ui = now
                        rate = seen / max(1e-6, now - t0)
                        progress(seen / need, f"Analyse des passages · {rate:.0f} images/s",
                                 (need - seen) / max(rate, 1e-6))
            except BaseException:
                covered = merge_ranges([*covered, [a, i]])
                _save(cache, rows, {**meta, "covered": covered})
                raise
            finally:
                reader.close()
            covered = merge_ranges([*covered, [a, b]])
            _save(cache, rows, {**meta, "covered": covered})
    meta = {**meta, "covered": covered, "done": True, "next": total, "gpu": gpu,
            "seconds": round(float(meta.get("seconds") or 0) + time.monotonic() - t0, 1)}
    if full and not subtract_ranges([[0, total]], covered):
        meta.pop("covered", None)  # toute la vidéo est maintenant analysée
    _save(cache, rows, meta)
    log.info("Passages analysed: %d frames, %d raw detections in %.0fs", seen, len(rows), time.monotonic() - t0)
    return load(cache)  # type: ignore[return-value]


__all__ = ["Detector", "analyze", "load", "decode", "LABELS"]
