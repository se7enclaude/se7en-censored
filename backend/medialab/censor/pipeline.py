"""Les deux tâches de fond : analyse d'une vidéo (fichier ou lien), puis export de la version floutée."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from ..errors import Canceled, MediaLabError
from ..logging_setup import get_logger
from ..media import ffmpeg
from . import detect, refine, render
from . import project as P
from . import track as T

log = get_logger("censor")
Progress = Callable[[float, str, float | None], None]

_zones_cache: dict[str, tuple[tuple, list[T.Track], dict]] = {}  # les derniers projets ouverts
ZONES_CACHE_MAX = 3
_zones_lock = threading.Lock()


# ------------------------------------------------------------------ source
def _download(project: dict, settings, jctx, progress: Progress) -> Path:
    from ..downloader.base import DownloadContext
    from ..downloader.registry import registry
    url = project["source"]["url"]
    out = P.root() / "sources"
    out.mkdir(parents=True, exist_ok=True)

    def on_progress(p: dict) -> None:
        pct = p.get("percent")
        if pct is None and p.get("total"):
            pct = 100 * (p.get("downloaded") or 0) / max(1, p["total"])
        progress(0.15 * (pct or 0) / 100, "Téléchargement de la vidéo", None)

    provider = registry.get("ytdlp")
    opts = {"mode": "av", "quality": "1080", "container": "mp4", "output_dir": str(out), "folder_structure": "",
            "embed_thumbnail": False, "write_subtitles": False}
    dctx = DownloadContext(job_id=jctx.job["id"], settings=settings, cancel=jctx.cancel, progress=on_progress,
                           temp_dir=jctx.temp_dir)
    res = provider.download(url, opts, dctx)
    project = P.load(project["id"])
    project["source"]["path"] = res.file_path
    if res.title:
        project["name"] = res.title[:120]
    P.save(project)
    return Path(res.file_path)


def source_path(project: dict) -> Path:
    src = Path(project["source"].get("path") or "")
    if not src.is_file():
        raise MediaLabError("source_missing", "La vidéo de ce projet n'existe plus.",
                            "Elle a peut-être été déplacée ou supprimée.")
    return src


# ------------------------------------------------------------------ analyse
def analyze(pid: str, settings, jctx, progress: Progress) -> dict:
    cancel: threading.Event = jctx.cancel
    project = P.patch(pid, {"status": "analyzing", "error": None, "step": "detect"})
    opts = project["options"]
    src_path = project["source"].get("path")
    has_url = bool(project["source"].get("url"))
    if not src_path or not Path(src_path).is_file():
        if not has_url:
            raise MediaLabError("source_missing", "La vidéo de ce projet n'existe plus.")
        P.patch(pid, {"step": "download"})
        src = _download(project, settings, jctx, progress)
    else:
        src = Path(src_path)
    video = P.video_info(src)
    if not video.get("duration"):
        raise MediaLabError("no_duration", "La durée de cette vidéo est inconnue.", "Convertis-la en MP4 d'abord.")
    notes = []
    cache = P.cache_dir(pid)
    thumb = cache / "thumb.jpg"
    if not thumb.exists():
        try:
            ffmpeg.run(["-ss", f"{min(60.0, float(video['duration']) * 0.1):.2f}", "-i", str(src), "-frames:v", "1",
                        "-vf", "scale=480:-2", "-q:v", "4", str(thumb)], label="censor thumbnail")
        except MediaLabError:
            pass
    P.patch(pid, {"video": video, "step": "detect", "notes": notes, "thumb": str(thumb) if thumb.exists() else None})
    project = apply_scope(pid, video)
    lo = 0.15 if has_url else 0.0
    device = "cpu" if getattr(settings.ai, "gpu_id", "auto") == "cpu" else "auto"
    mid = lo + (1 - lo) * 0.9  # l'analyse, puis la 2e passe (courte : seulement autour de la nudité trouvée)
    det, meta = detect.analyze(src, video, cache, step=2 if opts.get("quality") == "fast" else 1,
                               mirror=bool(opts.get("careful")), device=device, cancel=cancel,
                               progress=lambda f, lbl, eta: progress(lo + (mid - lo) * f, lbl, eta),
                               ranges=scope_ranges(project))
    if meta.get("refined") != refine.REFINE_VERSION:
        P.patch(pid, {"step": "verify"})
        try:
            det, meta = refine.run(src, video, cache, det, meta, device=device, cancel=cancel,
                                   progress=lambda f, lbl, eta: progress(mid + (1 - mid) * f, lbl, eta))
        except MediaLabError as e:  # l'analyse reste utilisable sans la 2e passe (relançable plus tard)
            log.warning("Second pass failed: %s", e)
            notes.append(f"La 2e passe n'a pas pu se faire ({e.message}) : les zones viennent de la seule analyse. "
                         "Relance-la plus tard depuis ce projet.")
            P.patch(pid, {"notes": notes})
    with _zones_lock:
        _zones_cache.pop(pid, None)
    project = P.load(pid)
    stats = summary(project)
    project = P.patch(pid, {"status": "review", "step": None, "detect": {k: meta.get(k) for k in
                                                                         ("v", "fps", "frames", "step", "mirror", "seconds", "gpu", "refined")},
                            "stats": stats})
    try:
        progress(1.0, "Analyse terminée", 0)
    except Canceled:  # « Arrêter » au moment même où l'analyse finit : elle est finie, on la garde
        pass
    return project


# ------------------------------------------------------------------ analyse limitée à des passages
def apply_scope(pid: str, video: dict) -> dict:
    """Timecodes donnés à la création : lus avec la vraie cadence de la vidéo, ils deviennent des passages « zones
    de l'IA », et l'analyse se limite à eux (``scope_only``)."""
    project = P.load(pid)
    scope = project.get("scope")
    if not scope or project.get("scope_only"):
        return project
    from . import timecodes as TC
    fps = float(video.get("fps") or 25.0)
    frames = max(1, int(round(float(video.get("duration") or 0) * fps)))
    res = TC.parse(scope.get("text") or "", fps, frames / fps, float(scope.get("margin", 2.0)))
    segs = list(project.get("segments") or [])
    for i, r in enumerate(res["ranges"]):
        f0, f1 = TC.to_frames(r["start"], r["end"], fps, frames)
        segs.append({"id": f"tc{i}", "f0": f0, "f1": f1, "mode": "ai", "label": r["text"][:200]})
    notes = list(project.get("notes") or [])
    if res["skipped"]:
        notes.append(f"{len(res['skipped'])} ligne(s) de timecodes ignorée(s) : "
                     + " ; ".join(f"l. {x['line']} ({x['reason']})" for x in res["skipped"][:5]))
    if not res["ranges"]:
        notes.append("Aucun timecode lisible : toute la vidéo est analysée.")
        return P.patch(pid, {"scope": None, "notes": notes})
    return P.patch(pid, {"segments": T.clean_segments(segs), "scope_only": True, "notes": notes,
                         "options": {**project["options"], "ai_outside": False}})


def scope_ranges(project: dict) -> list[list[int]] | None:
    """Plages d'images à analyser : None pour toute la vidéo, sinon les passages « zones de l'IA »."""
    if not project.get("scope_only"):
        return None
    return [[z["f0"], z["f1"] + 1] for z in T.clean_segments(project.get("segments")) if z["mode"] == "ai"]


def unanalysed(project: dict, meta: dict) -> int:
    """Images de passages « zones de l'IA » pas encore analysées (passage ajouté après l'analyse)."""
    cov = meta.get("covered")
    if cov is None:
        return 0
    want = [[z["f0"], z["f1"] + 1] for z in T.clean_segments(project.get("segments")) if z["mode"] == "ai"]
    return sum(b - a for a, b in detect.subtract_ranges(want, cov))


def zones(project: dict) -> tuple[list[T.Track], dict]:
    """Pistes calculées avec les réglages actuels (mises en cache tant que rien ne change)."""
    pid = project["id"]
    cache = P.cache_dir(pid)
    params = T.Params.from_options(project["options"])
    # clé calculée avant de relire les détections : chaque aperçu d'image ne redécompresse pas des dizaines de Mo
    try:
        stamp = tuple(f.stat().st_mtime_ns for f in detect._store(cache))
    except OSError:
        stamp = None
    key = (stamp, tuple(params.classes), params.sensitivity, params.margin, params.verify)
    with _zones_lock:
        hit = _zones_cache.get(pid)
        if stamp and hit and hit[0] == key:
            _zones_cache[pid] = _zones_cache.pop(pid)  # le plus récent en dernier
            return hit[1], hit[2]
    loaded = detect.load(cache)
    if not loaded or not loaded[1].get("done"):
        raise MediaLabError("not_analyzed", "Cette vidéo n'est pas encore analysée.")
    det, meta = loaded
    meta = {**meta, "cuts": [int(x) for x in det.get("cuts", [])]}  # changements de plan (2e passe)
    tracks = T.build(det, float(meta["fps"]), int(meta["frames"]), params, int(meta.get("step") or 1))
    with _zones_lock:
        _zones_cache.pop(pid, None)
        _zones_cache[pid] = (key, tracks, meta)
        while len(_zones_cache) > ZONES_CACHE_MAX:
            _zones_cache.pop(next(iter(_zones_cache)))
    return tracks, meta


def frame_boxes(project: dict, tracks: list[T.Track]) -> T.FrameBoxes:
    """Tout ce qui sera flouté : zones de l'IA (sauf masquées), zones à la main, passages donnés par timecodes."""
    return T.FrameBoxes(tracks, project.get("manual") or [], project.get("hidden") or [], project.get("segments"),
                        bool(project["options"].get("ai_outside", True)))


def coverage(project: dict, tracks: list[T.Track], meta: dict) -> dict:
    return T.coverage(tracks, project.get("manual") or [], project.get("hidden") or [], float(meta["fps"]),
                      int(meta["frames"]), project.get("segments"), bool(project["options"].get("ai_outside", True)))


def summary(project: dict) -> dict:
    tracks, meta = zones(project)
    cov = coverage(project, tracks, meta)
    return {**cov, "analysis_seconds": meta.get("seconds"), "frames": meta.get("frames")}


# ------------------------------------------------------------------ export
def export(pid: str, settings, cancel: threading.Event, progress: Progress) -> dict:
    project = P.patch(pid, {"status": "exporting", "error": None, "step": "export"})
    src = source_path(project)
    tracks, meta = zones(project)
    fb = frame_boxes(project, tracks)
    o = project["options"]
    old = (project.get("export") or {}).get("file")
    dst = render.output_path(src, P.output_dir(project), old)
    res = render.export(src, project["video"], fb, float(meta["fps"]), int(meta["frames"]), dst,
                        style=o.get("style", "blur"), shape=o.get("shape", "ellipse"), strength=int(o.get("strength", 70)),
                        cancel=cancel, progress=progress,
                        use_gpu=bool(getattr(settings.tools, "use_gpu_encoder", True)))
    if old and old != res["file"]:
        Path(old).unlink(missing_ok=True)
    project = P.patch(pid, {"status": "done", "step": None, "export": res})
    try:
        progress(1.0, "Vidéo exportée", 0)
    except Canceled:  # l'export est déjà écrit
        pass
    log.info("Export of %s done in %.0fs", project.get("name"), time.time() - res["rendered_at"] + res["seconds"])
    return project


# ------------------------------------------------------------------ zones à la main qui suivent toutes seules
FOLLOW_MANUAL_MIN = 0.45  # en dessous, la zone est perdue (sortie du cadre, changement de plan)


def follow_zone(project: dict, f: int, box: list[float], seconds: float = 10.0) -> dict:
    """Suit à l'image une zone tracée à la main, de l'image ``f`` jusqu'à ce qu'on la perde (au plus ``seconds``).
    Renvoie les images clés ({f, box}), où le suivi s'est arrêté et pourquoi."""
    from . import follow as FW
    from . import frames as FR
    src = source_path(project)
    video = project["video"]
    loaded = detect.load(P.cache_dir(project["id"]))
    meta = loaded[1] if loaded else {}
    cuts = {int(x) for x in loaded[0].get("cuts", [])} if loaded else set()
    fps = float(meta.get("fps") or video.get("fps") or 25.0)
    frames = int(meta.get("frames") or round(float(video.get("duration") or 0) * fps))
    size = FR.analysis_size(int(video["width"]), int(video["height"]), detect.SIDE)
    f = max(0, min(frames - 1, int(f)))
    box = [float(np.clip(v, 0, 1)) for v in box]
    keys = [{"f": f, "box": box}]
    last = min(frames - 1, f + int(round(seconds * fps)), min((x - 1 for x in cuts if x > f), default=frames))
    reader = FR.Reader(src, fps, size, "rgb24", start=f)
    reason = "end"
    try:
        first = reader.read()
        if first is None:
            return {"keys": keys, "until": f, "reason": "end"}
        fw = FW.Follower(FW.gray(first), box, scales=(0.94, 1.0, 1.06))
        if not fw.ok:
            return {"keys": keys + [{"f": min(last, f + int(round(2 * fps))), "box": box}], "until": f, "reason": "flat"}
        i = f
        while i < last:
            fr = reader.read()
            if fr is None:
                break
            i += 1
            r = fw.track(FW.gray(fr))
            if r is None or r[1] < FOLLOW_MANUAL_MIN:
                reason = "lost"
                i -= 1
                break
            if (i - f) % 2 == 0 or i == last:
                keys.append({"f": i, "box": [round(float(v), 4) for v in r[0]]})
        if keys[-1]["f"] != i and i > f:
            keys.append({"f": i, "box": [round(float(v), 4) for v in fw.box]})
        if i >= last and reason != "lost":
            reason = "cut" if last + 1 in cuts else "limit"
    finally:
        reader.close()
    return {"keys": keys, "until": keys[-1]["f"], "reason": reason, "seconds": round((keys[-1]["f"] - f) / fps, 2)}
