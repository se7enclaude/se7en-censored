"""Job workers: glue between the queue and the download / upscale engines."""
from __future__ import annotations

from pathlib import Path

from ..db import Database
from ..downloader.base import DownloadContext
from ..downloader.registry import registry
from ..errors import MediaLabError
from ..media import ffmpeg
from ..settings import SettingsStore
from ..tools import convert as converter
from ..subtitles import burn as subs_burn
from ..subtitles import pipeline as subs_pipeline
from ..subtitles import project as subs_project
from ..tools import audio as audio_tools
from ..tools import trim as trimmer
from ..upscale import engine
from ..upscale.video import process_video
from .manager import JobContext


def is_french(settings) -> bool:
    lang = settings.general.language
    if lang != "auto":
        return lang == "fr"
    try:
        from ..preflight import is_french as windows_french
        return windows_french()
    except Exception:
        return False


def _progress(jctx: JobContext):
    def cb(frac: float, label: str, eta: float | None) -> None:
        jctx.progress({"stage": "processing", "label": label, "percent": frac * 100, "eta": eta, "speed": 0})
    return cb


def make_download_worker(settings: SettingsStore):
    def worker(job: dict, jctx: JobContext) -> dict:
        s = settings.get()
        opts = job.get("options") or {}
        provider = registry.get(opts.get("provider") or "ytdlp")
        jctx.progress({"stage": "downloading", "label": "Connecting…", "percent": None, "speed": 0,
                       "attempt": jctx.attempt + 1, "strategy": (jctx.strategy or {}).get("label")})
        dctx = DownloadContext(job_id=job["id"], settings=s, cancel=jctx.cancel, progress=jctx.progress,
                               temp_dir=jctx.temp_dir, attempt=jctx.attempt, strategy=jctx.strategy)
        res = provider.download(job["source"], opts, dctx)
        return {
            "file_path": res.file_path, "title": res.title or job.get("title"), "platform": res.platform or job.get("platform"),
            "thumbnail_url": res.thumbnail, "resolution": res.resolution, "format": res.format, "size": res.size,
            "duration": res.duration, "extra_files": res.extra_files, "meta": res.meta,
        }
    return worker


def make_upscale_worker(settings: SettingsStore, db: Database):
    def worker(job: dict, jctx: JobContext) -> dict:
        s = settings.get()
        opts = dict(job.get("options") or {})
        src = Path(job["source"])
        if not src.exists():
            raise MediaLabError("source_missing", "The source file no longer exists.",
                                "It may have been moved or deleted. Remove this task and import the file again.")
        kind = ffmpeg.media_kind(src) or opts.get("kind") or "image"
        # Keep the same destination across pauses/retries so checkpoints stay valid.
        dst = Path(opts["_dst"]) if opts.get("_dst") else None
        if dst is None:
            if kind == "image":
                ext = (opts.get("image_format") or s.ai.image_format).lower()
                if opts.get("remove_bg") and opts.get("bg_mode", "transparent") in ("transparent", "mask") and ext == "jpg":
                    ext = "png"  # transparency needs PNG
            else:
                ext = (opts.get("container") or ("mkv" if src.suffix.lower() == ".mkv" else "mp4")).lower()
                opts["container"] = ext
            dst = engine.output_path_for(src, opts, s, ext)
            opts["_dst"] = str(dst)
            db.update_job(job["id"], options=opts)

        def img_progress(frac: float, label: str) -> None:
            jctx.progress({"stage": "processing", "label": label, "percent": frac * 100})

        if kind == "image":
            res = engine.process_image(src, dst, opts, s, jctx.cancel, img_progress, jctx.strategy)
        else:
            res = process_video(job["id"], src, dst, opts, s, jctx.cancel, jctx.progress, jctx.strategy)
        upscaled = engine.wants_upscale(opts)
        model = engine.resolve_model(opts.get("model") or s.ai.default_model)[0] if upscaled else None
        tools = engine.ai_operations(opts) + (["interpolate"] if opts.get("interpolate") and kind == "video" else [])
        fr = is_french(s)
        names = {"face": ("Visages", "Faces"), "colorize": ("Colorisation", "Colorize"),
                 "background": ("Détourage", "Background"), "interpolate": ("Fluidité", "Smooth motion")}
        label = model.name if model else " + ".join(names.get(x, (x, x))[0 if fr else 1] for x in tools) or "AI"
        return {
            "file_path": str(dst), "output_path": str(dst), "title": src.name,
            "resolution": f"{res['width']}×{res['height']}", "size": dst.stat().st_size,
            "format": f"{dst.suffix.lstrip('.').upper()} · {label}", "duration": res.get("duration"),
            "platform": "upscale",
            "meta": {**res, "source": str(src), "model": model.id if model else None, "kind": kind, "tools": tools},
        }
    return worker


def make_convert_worker(settings: SettingsStore):
    def worker(job: dict, jctx: JobContext) -> dict:
        s = settings.get()
        opts = dict(job.get("options") or {})
        opts.setdefault("hw", s.tools.use_gpu_encoder)
        if not opts.get("output_dir") and s.tools.convert_output_folder:
            opts["output_dir"] = s.tools.convert_output_folder
        res = converter.convert(Path(job["source"]), str(opts.get("target")), opts, jctx.cancel, _progress(jctx))
        return {**res, "platform": "convert"}
    return worker


def make_studio_worker(settings: SettingsStore):
    """Audio tools: separate the voice from the music, or clean up the sound."""
    def worker(job: dict, jctx: JobContext) -> dict:
        s = settings.get()
        opts = dict(job.get("options") or {})
        if not opts.get("output_dir") and s.tools.studio_output_folder:
            opts["output_dir"] = s.tools.studio_output_folder
        fr = is_french(s)
        separate = opts.get("op") == "separate"
        res = (audio_tools.separate if separate else audio_tools.clean)(
            Path(job["source"]), opts, jctx.cancel, _progress(jctx), french=fr)
        ext = Path(res["file_path"]).suffix.lstrip(".").upper()
        if separate:
            what = {"both": ("voix + instrumental", "vocals + instrumental"), "vocals": ("voix", "vocals"),
                    "instrumental": ("instrumental", "instrumental")}[opts.get("stems") or "both"]
        else:
            what = ("son nettoyé", "cleaned sound")
        res["format"] = f"{ext} · {what[0 if fr else 1]}"
        return {**res, "platform": "studio"}
    return worker


def make_subtitles_worker(settings: SettingsStore):
    """Subtitles: generate (speech recognition, correction, translation) or burn into the video."""
    def worker(job: dict, jctx: JobContext) -> dict:
        s = settings.get()
        fr = is_french(s)
        opts = dict(job.get("options") or {})
        pid = str(opts.get("project") or "")
        if opts.get("op") == "burn":
            project = subs_project.load(pid)
            folder = opts.get("output_dir") or s.subtitles.output_folder or None
            dst = subs_burn.output_path(project["video"], folder, fr)
            res = subs_burn.burn(project, dst, jctx.cancel, _progress(jctx), use_gpu=s.tools.use_gpu_encoder,
                                 french=fr)
            res["meta"]["project"] = pid
            return {**res, "platform": "subtitles"}
        project = subs_pipeline.generate(pid, opts, s, jctx.cancel, _progress(jctx), french=fr)
        n = len(project["cues"])
        src, dst = project["source_lang"], project["target_lang"]
        what = (f"{n} sous-titres" if fr else f"{n} subtitles") + (f" · {src} → {dst}" if dst != src else f" · {src}")
        # no file_path: the source video is never recorded as a result (nothing to delete from History)
        return {"file_path": None, "title": project["name"], "format": what, "platform": "subtitles",
                "duration": project["video"].get("duration"),
                "meta": {"project": pid, "cues": n, "source_lang": src, "target_lang": dst,
                         "elapsed": project.get("generated", {}).get("seconds"),
                         "notes": project.get("generated", {}).get("notes")}}
    return worker


def make_trim_worker(settings: SettingsStore):
    def worker(job: dict, jctx: JobContext) -> dict:
        s = settings.get()
        opts = dict(job.get("options") or {})
        fr = is_french(s)
        res = trimmer.trim(Path(job["source"]), opts.get("segments") or [], opts, jctx.cancel, _progress(jctx), french=fr)
        mode = res["meta"]["mode"]
        res["format"] = f"{Path(res['file_path']).suffix.lstrip('.').upper()} · " + (
            ("à l'image près" if fr else "frame-accurate") if mode == "precise" else ("sans perte" if fr else "lossless"))
        return {**res, "platform": "trim"}
    return worker
