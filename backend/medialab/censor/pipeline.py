"""Les deux tâches de fond : analyse d'une vidéo (fichier ou lien), puis export de la version floutée."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from ..errors import MediaLabError
from ..logging_setup import get_logger
from ..media import ffmpeg
from . import detect, render
from . import project as P
from . import track as T

log = get_logger("censor")
Progress = Callable[[float, str, float | None], None]

_zones_cache: dict[str, tuple[tuple, list[T.Track]]] = {}
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
        raise MediaLabError("no_duration", "La durée de cette vidéo est inconnue.", "Convertissez-la en MP4 d'abord.")
    notes = []
    if video.get("is_hdr"):
        notes.append("Vidéo HDR : la version floutée sort en SDR (couleurs un peu différentes).")
    cache = P.cache_dir(pid)
    thumb = cache / "thumb.jpg"
    if not thumb.exists():
        try:
            ffmpeg.run(["-ss", f"{min(60.0, float(video['duration']) * 0.1):.2f}", "-i", str(src), "-frames:v", "1",
                        "-vf", "scale=480:-2", "-q:v", "4", str(thumb)], label="censor thumbnail")
        except MediaLabError:
            pass
    P.patch(pid, {"video": video, "step": "detect", "notes": notes, "thumb": str(thumb) if thumb.exists() else None})
    lo = 0.15 if has_url else 0.0
    device = "cpu" if getattr(settings.ai, "gpu_id", "auto") == "cpu" else "auto"
    det, meta = detect.analyze(src, video, cache, step=2 if opts.get("quality") == "fast" else 1,
                               mirror=bool(opts.get("careful")), device=device, cancel=cancel,
                               progress=lambda f, lbl, eta: progress(lo + (1 - lo) * f, lbl, eta))
    with _zones_lock:
        _zones_cache.pop(pid, None)
    project = P.load(pid)
    stats = summary(project)
    project = P.patch(pid, {"status": "review", "step": None, "detect": {k: meta.get(k) for k in
                                                                         ("fps", "frames", "step", "mirror", "seconds", "gpu")},
                            "stats": stats})
    progress(1.0, "Analyse terminée", 0)
    return project


def zones(project: dict) -> tuple[list[T.Track], dict]:
    """Pistes calculées avec les réglages actuels (mises en cache tant que rien ne change)."""
    pid = project["id"]
    cache = P.cache_dir(pid)
    loaded = detect.load(cache)
    if not loaded or not loaded[1].get("done"):
        raise MediaLabError("not_analyzed", "Cette vidéo n'est pas encore analysée.")
    det, meta = loaded
    params = T.Params.from_options(project["options"])
    npz = cache / "detections.npz"
    key = (npz.stat().st_mtime, tuple(params.classes), params.sensitivity, params.margin)
    with _zones_lock:
        hit = _zones_cache.get(pid)
        if hit and hit[0] == key:
            return hit[1], meta
    tracks = T.build(det, float(meta["fps"]), int(meta["frames"]), params)
    with _zones_lock:
        _zones_cache[pid] = (key, tracks)
    return tracks, meta


def summary(project: dict) -> dict:
    tracks, meta = zones(project)
    cov = T.coverage(tracks, project.get("manual") or [], project.get("hidden") or [], float(meta["fps"]),
                     int(meta["frames"]))
    return {**cov, "analysis_seconds": meta.get("seconds"), "frames": meta.get("frames")}


# ------------------------------------------------------------------ export
def export(pid: str, settings, cancel: threading.Event, progress: Progress) -> dict:
    project = P.patch(pid, {"status": "exporting", "error": None, "step": "export"})
    src = source_path(project)
    tracks, meta = zones(project)
    fb = T.FrameBoxes(tracks, project.get("manual") or [], project.get("hidden") or [])
    o = project["options"]
    dst = render.output_path(src, P.output_dir(project))
    old = (project.get("export") or {}).get("file")
    res = render.export(src, project["video"], fb, float(meta["fps"]), int(meta["frames"]), dst,
                        style=o.get("style", "blur"), shape=o.get("shape", "ellipse"), strength=int(o.get("strength", 70)),
                        cancel=cancel, progress=progress,
                        use_gpu=bool(getattr(settings.tools, "use_gpu_encoder", True)))
    if old and old != res["file"]:
        Path(old).unlink(missing_ok=True)
    project = P.patch(pid, {"status": "done", "step": None, "export": res})
    progress(1.0, "Vidéo exportée", 0)
    log.info("Export of %s done in %.0fs", project.get("name"), time.time() - res["rendered_at"] + res["seconds"])
    return project
