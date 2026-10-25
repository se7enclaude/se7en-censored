"""FastAPI application: REST + WebSocket API consumed by the UI (and integrations)."""
from __future__ import annotations

import asyncio
import mimetypes
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from . import APP_NAME, __version__, ai_assets, components, presets, system, thumbs
from .config import frontend_dist, is_frozen, is_portable, paths
from .db import Database
from .downloader import cookies as cookie_store
from .downloader import naming
from .downloader.registry import extract_urls, registry
from .errors import MediaLabError, classify
from .events import bus
from .hardware import monitor
from .jobs.manager import JobManager
from .jobs.workers import (is_french, make_convert_worker, make_download_worker, make_studio_worker,
                           make_subtitles_worker, make_trim_worker, make_upscale_worker)
from .logging_setup import get_logger, ring, set_debug
from .media import ffmpeg
from .plugins import load_plugins
from .security import TOKEN_HEADER, tokens_equal
from .settings import SettingsStore
from .tools import audio as audio_tools
from .tools import convert as converter
from .tools import duplicates, playback, stats, tasks
from .updater import updater
from .upscale import engine

log = get_logger("app")


class Services:
    def __init__(self, token: str, shell: bool = False):
        self.token = token
        self.shell = shell
        self.port: int | None = None
        self.settings = SettingsStore()
        self.db = Database()
        self.jobs = JobManager(self.db, self.settings)
        self.plugins: list[dict] = []
        s = self.settings.get()
        components.set_overrides(s.advanced.ffmpeg_path, s.advanced.realesrgan_path, s.advanced.aria2c_path)
        self.jobs.register_worker("download", make_download_worker(self.settings),
                                  lambda: self.settings.get().download.concurrent_downloads)
        self.jobs.register_worker("upscale", make_upscale_worker(self.settings, self.db),
                                  lambda: self.settings.get().ai.concurrent_jobs)
        self.jobs.register_worker("convert", make_convert_worker(self.settings),
                                  lambda: self.settings.get().tools.convert_concurrent)
        self.jobs.register_worker("trim", make_trim_worker(self.settings), lambda: 2)
        self.jobs.register_worker("studio", make_studio_worker(self.settings), lambda: 1)  # one GPU model at a time
        self.jobs.register_worker("subtitles", make_subtitles_worker(self.settings), lambda: 1)
        from .censor.api import make_worker as make_censor_worker
        self.jobs.register_worker("censor", make_censor_worker(self.settings), lambda: 1)  # une tâche à la fois (GPU)
        self.settings.on_change(self._on_settings)
        presets.seed(self.db)
        self._apply_retention()

    def _on_settings(self, s) -> None:
        set_debug(s.advanced.debug_mode)
        components.set_overrides(s.advanced.ffmpeg_path, s.advanced.realesrgan_path, s.advanced.aria2c_path)
        bus.publish("settings", s.model_dump())

    def _apply_retention(self) -> None:
        days = self.settings.get().privacy.history_retention_days
        if days:
            self.db.clear_history(older_than=time.time() - days * 86400)


# ------------------------------------------------------------------ bodies
class UrlBody(BaseModel):
    url: str


class TextBody(BaseModel):
    text: str


class DownloadItem(BaseModel):
    url: str
    title: str | None = None
    thumbnail: str | None = None
    platform: str | None = None
    platform_name: str | None = None
    provider: str | None = None
    entry_count: int | None = None
    options: dict[str, Any] = {}


class DownloadBody(BaseModel):
    items: list[DownloadItem]
    options: dict[str, Any] = {}
    preset_id: str | None = None
    priority: int = 0


class JobAction(BaseModel):
    value: Any = None


class BulkBody(BaseModel):
    action: str
    kind: str | None = None


class PresetBody(BaseModel):
    name: str
    platforms: list[str] = []
    options: dict[str, Any] = {}
    auto_apply: bool = True


class OpenBody(BaseModel):
    path: str
    reveal: bool = False


class PathsBody(BaseModel):
    paths: list[str]


class UpscaleBody(BaseModel):
    paths: list[str]
    options: dict[str, Any] = {}
    priority: int = 0


class PreviewBody(BaseModel):
    path: str
    options: dict[str, Any] = {}
    region: dict[str, float] | None = None
    at: float | None = None


class PickBody(BaseModel):
    kind: str = "media"
    initial: str = ""
    multiple: bool = True


class ConvertBody(BaseModel):
    paths: list[str]
    target: str
    options: dict[str, Any] = {}
    priority: int = 0


class TrimBody(BaseModel):
    path: str
    segments: list[dict[str, float]]
    options: dict[str, Any] = {}


class SubsCreateBody(BaseModel):
    path: str


class SubsPatchBody(BaseModel):
    name: str | None = None
    cues: list[dict] | None = None
    style: dict | None = None
    source_lang: str | None = None
    target_lang: str | None = None
    engine: str | None = None
    options: dict | None = None


class SubsGenerateBody(BaseModel):
    source_lang: str | None = None
    target_lang: str | None = None
    engine: str | None = None
    polish: bool = True
    glossary: str = ""


class SubsExportBody(BaseModel):
    format: str
    folder: str | None = None
    timestamps: bool = False


class SubsBurnBody(BaseModel):
    output_dir: str | None = None


class SubsImportBody(BaseModel):
    path: str


class KeyBody(BaseModel):
    key: str = ""


class StudioBody(BaseModel):
    paths: list[str]
    options: dict[str, Any] = {}


class ScanBody(BaseModel):
    folders: list[str]
    options: dict[str, Any] = {}


MEDIA_TYPES = {
    ".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/mp4", ".webm": "video/webm", ".ogv": "video/ogg",
    ".mkv": "video/x-matroska", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac", ".wav": "audio/wav",
    ".weba": "audio/webm", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jfif": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp", ".avif": "image/avif", ".ico": "image/x-icon",
    ".svg": "image/svg+xml", ".tif": "image/tiff", ".tiff": "image/tiff",
}


# ------------------------------------------------------------------ app
def create_app(services: Services) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        bus.bind_loop(asyncio.get_running_loop())
        monitor().start()
        services.jobs.start()
        bus.add_listener(_on_event)
        log.info("%s %s started (portable=%s, frozen=%s, data=%s)", APP_NAME, __version__, is_portable(),
                 is_frozen(), paths().data)
        threading.Thread(target=_warmup, daemon=True).start()
        updater.note_launch()
        if services.shell or is_frozen():
            updater.start_background(_auto_update)
        yield
        updater.stop()
        playback.stop_all()
        services.jobs.shutdown()
        monitor().stop()

    def _auto_update() -> bool:
        return services.settings.get().general.auto_update

    def _on_event(ev: dict) -> None:
        if ev["type"] == "notify" and services.shell:
            system.flash_taskbar()
        if ev["type"] == "update" and ev["data"].get("state") == "available" and _auto_update():
            # Download in the background; it is installed on the next restart (or right away on demand).
            threading.Thread(target=updater.auto_download, daemon=True).start()

    def _warmup() -> None:
        # Pre-load heavy things so the first analyze/upscale feels instant.
        try:
            registry.detect("https://www.youtube.com/watch?v=aqz-KE-bpKQ")
            if components.find_binary("ffmpeg"):
                ffmpeg.working_encoders()
            engine.BACKENDS["ncnn"].devices()
        except Exception as e:
            log.debug("warmup: %s", e)

    app = FastAPI(title=APP_NAME, version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.services = services
    services.plugins = load_plugins(app)

    # -------------------------------------------------------- security
    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost", "testserver"):
            return PlainTextResponse("Forbidden host", status_code=403)
        if request.url.path.startswith("/api/") and request.url.path != "/api/health":
            token = request.headers.get(TOKEN_HEADER) or request.query_params.get("token")
            if not tokens_equal(token, services.token):
                return JSONResponse({"code": "unauthorized", "message": "Missing or invalid session token"}, 401)
        return await call_next(request)

    @app.exception_handler(MediaLabError)
    async def ml_error(_request: Request, exc: MediaLabError):
        return JSONResponse(exc.to_dict(), status_code=422 if exc.code not in ("not_found",) else 404)

    @app.exception_handler(ValidationError)
    async def val_error(_request: Request, exc: ValidationError):
        return JSONResponse({"code": "invalid", "message": "Invalid value", "detail": exc.errors()[:5]}, 422)

    S = services

    # -------------------------------------------------------- system
    @app.get("/api/health")
    async def health():
        return {"ok": True, "app": APP_NAME, "version": __version__}

    @app.get("/api/system/info")
    async def info():
        return {"app": APP_NAME, "version": __version__, "portable": is_portable(), "frozen": is_frozen(),
                "shell": S.shell, "data_dir": str(paths().data), "logs_dir": str(paths().logs),
                "plugins": S.plugins, "providers": [{"id": p.id, "name": p.name} for p in registry.providers]}

    @app.get("/api/system/hardware")
    async def hardware():
        return await run_in_threadpool(monitor().snapshot)

    @app.get("/api/system/clipboard")
    async def clipboard():
        text = await run_in_threadpool(system.clipboard_text)
        return {"urls": extract_urls(text)[:100]}

    @app.post("/api/system/open")
    async def open_path(body: OpenBody):
        await run_in_threadpool(system.open_path, body.path, body.reveal)
        return {"ok": True}

    @app.post("/api/shell/open")
    async def shell_open(body: PathsBody):
        """Files given to a second se7en.exe (Explorer "Open with"): shown in the running window."""
        paths = [x for x in body.paths if Path(x).is_file()]
        if paths:
            bus.publish("files_dropped", {"paths": paths, "open": True})
            if system.window is not None:
                try:
                    system.window.restore()
                except Exception:
                    pass
        return {"ok": True, "count": len(paths)}

    @app.post("/api/system/pick-folder")
    async def pick_folder(body: PickBody):
        return {"path": await run_in_threadpool(system.pick_folder, body.initial)}

    @app.post("/api/system/pick-files")
    async def pick_files(body: PickBody):
        return {"paths": await run_in_threadpool(system.pick_files, body.kind, body.initial, body.multiple)}

    @app.post("/api/system/open-data-folder")
    async def open_data(what: str = "data"):
        target = {"logs": paths().logs, "models": paths().models, "plugins": paths().plugins}.get(what, paths().data)
        target.mkdir(parents=True, exist_ok=True)
        await run_in_threadpool(system.open_path, str(target))
        return {"ok": True}

    @app.post("/api/system/clear-cache")
    async def clear_cache():
        def run():
            freed = 0
            for d in (paths().cache / "previews", paths().thumbnails, paths().temp / "img", paths().temp / "uploads"):
                if d.exists():
                    freed += sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                    shutil.rmtree(d, ignore_errors=True)
                    d.mkdir(parents=True, exist_ok=True)
            return freed
        return {"freed": await run_in_threadpool(run)}

    # -------------------------------------------------------- updates
    @app.get("/api/update")
    async def update_state():
        return updater.snapshot()

    @app.post("/api/update/check")
    async def update_check():
        return await run_in_threadpool(updater.check, True)

    @app.post("/api/update/seen")
    async def update_seen():
        return updater.seen()

    @app.post("/api/update/download")
    async def update_download():
        threading.Thread(target=updater.download, daemon=True).start()
        return updater.snapshot()

    @app.post("/api/update/install")
    async def update_install():
        running = services.jobs.stats()["running"]
        state = await run_in_threadpool(updater.install)
        system.request_quit(delay=1.0)  # the installer / swap script waits for se7en to close
        return {**state, "paused_jobs": running}

    # -------------------------------------------------------- components
    @app.get("/api/components")
    async def comps():
        return await run_in_threadpool(components.status)

    @app.post("/api/components/{cid}/install")
    async def install_comp(cid: str):
        def run():
            try:
                components.install(cid)
                if cid == "ffmpeg":
                    ffmpeg.reset_encoders()  # re-detect with the new binary
                if cid == "realesrgan":
                    engine.BACKENDS["ncnn"]._devices = None
            except MediaLabError:
                pass  # already published as a component event
        threading.Thread(target=run, daemon=True).start()
        return {"started": True}

    # -------------------------------------------------------- settings
    @app.get("/api/settings")
    async def get_settings():
        return S.settings.get().model_dump()

    @app.patch("/api/settings")
    async def patch_settings(patch: dict[str, Any]):
        try:
            return S.settings.update(patch).model_dump()
        except ValidationError as e:
            raise MediaLabError("invalid_setting", "One of the values is not valid.", str(e.errors()[0].get("msg")))

    @app.post("/api/settings/reset")
    async def reset_settings(section: str | None = None):
        return S.settings.reset(section).model_dump()

    @app.get("/api/naming/preview")
    async def naming_preview(template: str = "{title}", folders: str = "{platform}"):
        return {"preview": naming.preview(template, folders)}

    # -------------------------------------------------------- analyze & download
    @app.post("/api/detect")
    async def detect(body: UrlBody):
        return await run_in_threadpool(registry.detect, body.url.strip())

    @app.post("/api/extract-urls")
    async def extract(body: TextBody):
        urls = extract_urls(body.text)[:1000]
        return {"urls": [registry.detect(u) for u in urls]}

    @app.post("/api/analyze")
    async def analyze(body: UrlBody):
        url = body.url.strip()
        if not url.startswith(("http://", "https://")):
            raise MediaLabError("invalid_url", "This is not a web link.", "Paste a link starting with https://")
        t0 = time.perf_counter()
        info = await run_in_threadpool(registry.analyze, url, S.settings.get())
        info["analyze_seconds"] = round(time.perf_counter() - t0, 2)
        preset = presets.match(S.db, info.get("platform")) if S.settings.get().download.auto_apply_presets else None
        info["preset"] = preset
        if S.settings.get().download.warn_duplicates and info.get("kind") != "playlist":
            prev = S.db.find_history_by_source(url, info.get("webpage_url") or "", info.get("url") or "")
            info["already"] = [{"id": h["id"], "created_at": h["created_at"], "file_path": h.get("file_path"),
                                "format": h.get("format"), "resolution": h.get("resolution"),
                                "exists": bool(h.get("file_path")) and Path(h["file_path"]).exists()} for h in prev]
        if info.get("thumbnail"):
            info["thumbnail_key"] = await run_in_threadpool(thumbs.cache_remote, info["thumbnail"])
        return info

    @app.post("/api/analyze-page")
    async def analyze_page(body: UrlBody):
        """Every video a web page shows or links to (the "all the videos of the page" button)."""
        from .downloader.page_scan import analyze_page as scan_page
        url = body.url.strip()
        if not url.startswith(("http://", "https://")):
            raise MediaLabError("invalid_url", "This is not a web link.", "Paste a link starting with https://")
        return await run_in_threadpool(scan_page, url)

    @app.post("/api/scan-site")
    async def scan_site(body: UrlBody):
        """Deep scan: the page in a real browser, its frames, the pages and folders of the same site."""
        from .downloader import page_scan
        url = body.url.strip()
        if not url.startswith(("http://", "https://")):
            raise MediaLabError("invalid_url", "This is not a web link.", "Paste a link starting with https://")
        temp = paths().temp
        return tasks.start("site_scan", lambda t: page_scan.scan_site(url, t, temp_root=temp)).to_dict()

    @app.post("/api/downloads")
    async def add_downloads(body: DownloadBody):
        s = S.settings.get()
        preset = S.db.get_preset(body.preset_id) if body.preset_id else None
        created = []
        for item in body.items:
            base = {"mode": s.download.default_mode, "quality": s.download.default_quality,
                    "container": s.download.default_container, "vcodec": s.download.default_video_codec,
                    "audio_format": s.download.default_audio_format, "audio_bitrate": s.download.default_audio_bitrate}
            auto = preset or (presets.match(S.db, item.platform) if s.download.auto_apply_presets else None)
            opts = {**base, **(presets.clean_options(auto["options"]) if auto else {}), **body.options, **item.options}
            opts.update({"provider": item.provider or "ytdlp", "platform": item.platform,
                         "platform_name": item.platform_name, "title": item.title, "entry_count": item.entry_count})
            if auto:
                opts["preset"] = auto["name"]
            thumb = await run_in_threadpool(thumbs.cache_remote, item.thumbnail) if item.thumbnail else None
            created.append(S.jobs.add("download", item.url, opts, title=item.title, platform=item.platform,
                                      thumbnail=thumb, priority=body.priority))
        return {"jobs": created}

    # -------------------------------------------------------- jobs
    @app.get("/api/jobs")
    async def jobs_list():
        return {"jobs": S.jobs.list(), "stats": S.jobs.stats()}

    @app.get("/api/jobs/{job_id}")
    async def job_get(job_id: str):
        return S.jobs.get(job_id)

    @app.post("/api/jobs/{job_id}/{action}")
    async def job_action(job_id: str, action: str, body: JobAction | None = None):
        v = body.value if body else None
        if action == "pause":
            return S.jobs.pause(job_id)
        if action == "resume":
            return S.jobs.resume(job_id)
        if action == "cancel":
            return S.jobs.cancel(job_id)
        if action == "retry":
            return S.jobs.retry(job_id)
        if action == "remove":
            S.jobs.remove(job_id)
            return {"ok": True}
        if action == "priority":
            return S.jobs.set_priority(job_id, int(v or 0))
        if action == "move":
            return S.jobs.move(job_id, str(v or "up"))
        raise HTTPException(404, "Unknown action")

    @app.post("/api/jobs-bulk")
    async def jobs_bulk(body: BulkBody):
        return {"count": S.jobs.bulk(body.action, body.kind)}

    # -------------------------------------------------------- history
    @app.get("/api/history")
    async def history(q: str = "", kind: str = "", platform: str = "", limit: int = 300, offset: int = 0):
        items = S.db.list_history(q, kind, platform, limit, offset)
        for it in items:
            fp = it.get("file_path")
            it["exists"] = bool(fp) and Path(fp).exists()
        return {"items": items}

    @app.delete("/api/history/{item_id}")
    async def history_delete(item_id: str, delete_file: bool = False):
        item = S.db.get_history(item_id)
        if item and delete_file and item.get("file_path"):
            p = Path(item["file_path"])
            if p.is_file():
                await run_in_threadpool(_send_to_recycle_bin, p)
        S.db.delete_history(item_id)
        return {"ok": True}

    @app.delete("/api/history")
    async def history_clear():
        return {"deleted": S.db.clear_history()}

    @app.post("/api/history/{item_id}/again")
    async def history_again(item_id: str):
        item = S.db.get_history(item_id)
        if not item:
            raise MediaLabError("not_found", "This history entry no longer exists.")
        opts = item.get("options") or {}
        if item["kind"] == "download":
            job = S.jobs.add("download", item["source"], opts, title=item.get("title"),
                             platform=item.get("platform"), thumbnail=item.get("thumbnail"))
        else:
            opts.pop("_dst", None)
            if item["kind"] != "download" and not Path(item["source"]).exists():
                raise MediaLabError("source_missing", "The original file no longer exists.",
                                    "It may have been moved or deleted.")
            job = S.jobs.add(item["kind"], item["source"], opts, title=item.get("title"),
                             thumbnail=item.get("thumbnail"), platform=item.get("platform"))
        return job

    # -------------------------------------------------------- presets
    @app.get("/api/presets")
    async def presets_list():
        return {"presets": S.db.list_presets()}

    @app.post("/api/presets")
    async def presets_create(body: PresetBody):
        return S.db.upsert_preset({**body.model_dump(), "options": presets.clean_options(body.options)})

    @app.put("/api/presets/{preset_id}")
    async def presets_update(preset_id: str, body: PresetBody):
        old = S.db.get_preset(preset_id) or {}
        return S.db.upsert_preset({**old, **body.model_dump(), "id": preset_id,
                                   "options": presets.clean_options(body.options)})

    @app.delete("/api/presets/{preset_id}")
    async def presets_delete(preset_id: str):
        S.db.delete_preset(preset_id)
        return {"ok": True}

    # -------------------------------------------------------- cookies
    @app.get("/api/cookies")
    async def cookies_info():
        return cookie_store.cookies_file_info()

    @app.post("/api/cookies")
    async def cookies_import(file: UploadFile = File(...)):
        content = await file.read()
        if len(content) > 5 * 1024 * 1024:
            raise MediaLabError("too_large", "This cookies file is too large.")
        return await run_in_threadpool(cookie_store.import_cookies_file, content)

    @app.delete("/api/cookies")
    async def cookies_delete():
        cookie_store.delete_cookies_file()
        return {"ok": True}

    # -------------------------------------------------------- upscale
    @app.get("/api/upscale/capabilities")
    async def caps():
        return await run_in_threadpool(engine.capabilities)

    @app.post("/api/upscale/probe")
    async def probe(body: PathsBody):
        def run():
            out = []
            for p in body.paths[:500]:
                path = Path(p)
                kind = ffmpeg.media_kind(path)
                entry: dict[str, Any] = {"path": str(path), "name": path.name, "kind": kind, "ok": False}
                try:
                    if not path.is_file():
                        raise MediaLabError("file_missing", "File not found.")
                    if kind not in ("image", "video"):
                        raise MediaLabError("unsupported_file", "Unsupported file type.",
                                            "Images: PNG, JPG, WebP, BMP, TIFF. Videos: MP4, MKV, WebM, MOV, AVI.")
                    entry["size"] = path.stat().st_size
                    if kind == "image":
                        from PIL import Image
                        with Image.open(path) as im:
                            entry.update(width=im.width, height=im.height)
                    else:
                        i = ffmpeg.probe(path)
                        entry.update({k: i.get(k) for k in ("width", "height", "fps", "duration", "frames",
                                                              "vcodec", "is_hdr", "has_audio")})
                    entry["thumbnail"] = thumbs.cache_local(path)
                    entry["ok"] = True
                except MediaLabError as e:
                    entry["error"] = e.to_dict()
                except Exception as e:
                    entry["error"] = classify(e).to_dict()
                out.append(entry)
            return out
        return {"files": await run_in_threadpool(run)}

    @app.post("/api/upscale/preview")
    async def preview(body: PreviewBody):
        src = Path(body.path)
        if not src.is_file():
            raise MediaLabError("file_missing", "File not found.")
        return await run_in_threadpool(engine.preview, src, body.options, S.settings.get(),
                                       region=body.region, at=body.at)

    @app.get("/api/preview/{pid}/{name}")
    async def preview_file(pid: str, name: str):
        if not pid.isalnum() or name not in ("original.png", "result.png"):
            raise HTTPException(404)
        p = paths().cache / "previews" / pid / name
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="image/png", headers={"Cache-Control": "max-age=3600"})

    @app.post("/api/upscale/jobs")
    async def upscale_jobs(body: UpscaleBody):
        s = S.settings.get()
        created = []
        for p in body.paths:
            path = Path(p)
            if not path.is_file():
                continue
            opts = {"model": s.ai.default_model, "scale": s.ai.default_scale, **body.options}
            thumb = await run_in_threadpool(thumbs.cache_local, path)
            created.append(S.jobs.add("upscale", str(path), opts, title=path.name, thumbnail=thumb,
                                      platform="upscale", priority=body.priority))
        return {"jobs": created}

    @app.post("/api/upload")
    async def upload(files: list[UploadFile] = File(...)):
        """Browser-mode fallback for drag & drop (the desktop shell passes real paths)."""
        target = paths().temp / "uploads" / uuid.uuid4().hex[:8]
        target.mkdir(parents=True, exist_ok=True)
        saved = []
        for f in files:
            name = naming.sanitize_component(Path(f.filename or "file").name, 150)
            dest = target / name
            with open(dest, "wb") as out:
                while chunk := await f.read(1 << 20):
                    out.write(chunk)
            saved.append(str(dest))
        return {"paths": saved}

    # -------------------------------------------------------- media & thumbs
    @app.get("/api/thumb/{key}")
    async def thumb(key: str):
        p = thumbs.path_for(key)
        if not p:
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/media")
    async def media(path: str):
        p = Path(path)
        mime = MEDIA_TYPES.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or ""
        if not p.is_file() or not mime.startswith(("image/", "video/", "audio/")):
            raise HTTPException(404)
        return FileResponse(p, media_type=mime, headers={"Cache-Control": "no-store"})

    # -------------------------------------------------------- converter
    @app.get("/api/convert/catalog")
    async def convert_catalog():
        return converter.catalog()

    @app.post("/api/files/probe")
    async def files_probe(body: PathsBody):
        """Kind, size, duration, resolution and codecs of local files (converter, cutter, player)."""
        def run():
            out = []
            for p in body.paths[:500]:
                path = Path(p)
                entry: dict[str, Any] = {"path": str(path), "name": path.name, "ok": False}
                try:
                    if not path.is_file():
                        raise MediaLabError("file_missing", "File not found.")
                    entry["size"] = path.stat().st_size
                    kind = ffmpeg.file_kind(path)
                    info = ffmpeg.probe(path) if kind in ("video", "audio", None) else None
                    kind = converter.input_kind(path, info)
                    if kind is None:
                        raise MediaLabError("unsupported_file", "Unsupported file type.")
                    entry["kind"] = kind
                    if info:
                        entry.update({k: info.get(k) for k in ("duration", "width", "height", "fps", "vcodec",
                                                              "has_audio", "has_video", "is_hdr")})
                        entry["acodec"] = (info.get("audio") or [{}])[0].get("codec")
                        entry["subtitles"] = len(info.get("subtitles") or [])
                    elif kind in ("image", "animated"):
                        from .media.images import open_image
                        with open_image(path) as im:
                            entry.update(width=im.width, height=im.height, frames=getattr(im, "n_frames", 1))
                    if kind != "audio" and kind != "subtitle":
                        entry["thumbnail"] = thumbs.cache_local(path)
                    elif kind == "audio" and info and info.get("cover_index") is not None:
                        entry["thumbnail"] = thumbs.cache_local(path, at=0)
                    entry["ok"] = True
                except MediaLabError as e:
                    entry["error"] = e.to_dict()
                except Exception as e:
                    entry["error"] = classify(e).to_dict()
                out.append(entry)
            return out
        return {"files": await run_in_threadpool(run)}

    @app.post("/api/convert/jobs")
    async def convert_jobs(body: ConvertBody):
        if body.target not in converter.TARGETS:
            raise MediaLabError("unknown_target", f"Unknown output format “{body.target}”.")
        created = []
        for p in body.paths:
            path = Path(p)
            if not path.is_file():
                continue
            opts = {**body.options, "target": body.target}
            thumb = await run_in_threadpool(thumbs.cache_local, path)
            created.append(S.jobs.add("convert", str(path), opts, title=path.name, thumbnail=thumb,
                                      platform="convert", priority=body.priority))
        return {"jobs": created}

    # -------------------------------------------------------- cutter
    @app.post("/api/trim/jobs")
    async def trim_jobs(body: TrimBody):
        path = Path(body.path)
        if not path.is_file():
            raise MediaLabError("file_missing", "File not found.")
        if not body.segments:
            raise MediaLabError("no_segment", "Nothing to export: select at least one part.")
        thumb = await run_in_threadpool(thumbs.cache_local, path)
        job = S.jobs.add("trim", str(path), {**body.options, "segments": body.segments}, title=path.name,
                         thumbnail=thumb, platform="trim")
        return {"jobs": [job]}

    # -------------------------------------------------------- subtitles
    from .subtitles import formats as subs_formats
    from .subtitles import languages as subs_languages
    from .subtitles import pipeline as subs_pipeline
    from .subtitles import project as subs_project
    from .subtitles import secret as subs_secret
    from .subtitles import styles as subs_styles

    @app.get("/api/subtitles/catalog")
    async def subs_catalog():
        s = S.settings.get().subtitles
        whisper_ok = ai_assets.path_of("whisper_turbo") is not None and components.is_installed("whisper")
        local_ok = ai_assets.path_of("ministral_8b") is not None and components.is_installed("llama")
        return {
            "languages": subs_languages.catalog(), "presets": subs_styles.presets(),
            "fonts": subs_styles.available_fonts(), "animations": subs_styles.ANIMATIONS,
            "defaults": subs_styles.DEFAULT,
            "engines": {
                "whisper": {"installed": whisper_ok, "size": ai_assets.ASSETS["whisper_turbo"].size},
                "local": {"installed": local_ok, "size": ai_assets.ASSETS["ministral_8b"].size,
                          "model": "Ministral 3 8B"},
                "claude": {**subs_secret.status(), "model": s.claude_model},
            },
            "settings": s.model_dump(),
        }

    @app.get("/api/subtitles/projects")
    async def subs_list():
        return await run_in_threadpool(subs_project.listing)

    @app.post("/api/subtitles/projects")
    async def subs_create(body: SubsCreateBody):
        path = Path(body.path)
        if ffmpeg.file_kind(path) not in ("video", "audio"):
            raise MediaLabError("unsupported_file", "Choose a video (or audio) file.",
                                "MP4, MOV, MKV, AVI, WebM and every format FFmpeg reads are supported.")
        preset = S.settings.get().subtitles.default_preset
        project = await run_in_threadpool(subs_project.create, path, preset)
        project["thumbnail"] = await run_in_threadpool(thumbs.cache_local, path)
        subs_project.save(project)
        return project

    @app.get("/api/subtitles/projects/{pid}")
    async def subs_get(pid: str):
        return subs_project.load(pid)

    @app.put("/api/subtitles/projects/{pid}")
    async def subs_put(pid: str, body: SubsPatchBody):
        return await run_in_threadpool(subs_project.update, pid, body.model_dump(exclude_none=True))

    @app.delete("/api/subtitles/projects/{pid}")
    async def subs_delete(pid: str):
        await run_in_threadpool(subs_project.delete, pid)
        return {"ok": True}

    @app.post("/api/subtitles/projects/{pid}/detect")
    async def subs_detect(pid: str):
        subs_project.load(pid)
        fr = is_french(S.settings.get())

        def run(task):
            return subs_pipeline.detect(pid, task.cancel,
                                        lambda f, label, _eta: task.update(f * 100, label), fr)

        return tasks.start("subtitles-detect", run).to_dict()

    @app.post("/api/subtitles/projects/{pid}/generate")
    async def subs_generate(pid: str, body: SubsGenerateBody):
        project = subs_project.load(pid)
        if body.engine == "claude" and not subs_secret.get_key():
            raise MediaLabError("claude_key_missing", "No Claude API key is configured.",
                                "Paste your Anthropic API key in Settings › Subtitles, or choose the local model.")
        st = S.settings.get().subtitles
        opts = {**body.model_dump(), "op": "generate", "project": pid,
                "rules": {"max_chars": st.max_chars, "max_cps": st.max_cps}}
        job = S.jobs.add("subtitles", project["video"]["path"], opts, title=project["name"],
                         thumbnail=project.get("thumbnail"), platform="subtitles")
        return {"jobs": [job]}

    @app.post("/api/subtitles/projects/{pid}/burn")
    async def subs_burn_job(pid: str, body: SubsBurnBody):
        project = subs_project.load(pid)
        if not project.get("cues"):
            raise MediaLabError("no_subtitles", "There are no subtitles to burn in.",
                                "Generate or add subtitles first.")
        opts = {"op": "burn", "project": pid, **({"output_dir": body.output_dir} if body.output_dir else {})}
        job = S.jobs.add("subtitles", project["video"]["path"], opts, title=project["name"],
                         thumbnail=project.get("thumbnail"), platform="subtitles")
        return {"jobs": [job]}

    @app.post("/api/subtitles/projects/{pid}/export")
    async def subs_export(pid: str, body: SubsExportBody):
        project = subs_project.load(pid)
        cues = project.get("cues") or []
        if not cues:
            raise MediaLabError("no_subtitles", "There are no subtitles to export.",
                                "Generate or add subtitles first.")
        fmt = body.format.lower()
        lang = project.get("target_lang") or project.get("source_lang") or ""
        video = project["video"]
        if fmt == "srt":
            content = subs_formats.to_srt(cues)
        elif fmt == "vtt":
            content = subs_formats.to_vtt(cues, lang)
        elif fmt == "ass":
            content = subs_formats.to_ass(cues, project.get("style"), video.get("width") or 1920,
                                          video.get("height") or 1080, lang)
        elif fmt == "txt":
            content = subs_formats.to_txt(cues, body.timestamps)
        else:
            raise MediaLabError("bad_format", f"Unknown subtitle format “{body.format}”.")
        src = Path(video["path"])
        folder = Path(body.folder or S.settings.get().subtitles.output_folder or src.parent)
        folder.mkdir(parents=True, exist_ok=True)
        suffix = f".{lang}" if lang and fmt in ("srt", "vtt", "ass") else ""
        dst = folder / f"{src.stem}{suffix}.{fmt}"
        n = 1
        while dst.exists():
            dst = folder / f"{src.stem}{suffix} ({n}).{fmt}"
            n += 1
        await run_in_threadpool(subs_formats.write, dst, content)
        return {"path": str(dst), "size": dst.stat().st_size}

    @app.post("/api/subtitles/projects/{pid}/import")
    async def subs_import(pid: str, body: SubsImportBody):
        path = Path(body.path)
        if not path.is_file():
            raise MediaLabError("file_missing", "File not found.")
        raw = path.read_bytes()
        utf16 = raw[:2] in (b"\xff\xfe", b"\xfe\xff")
        text = raw.decode("utf-16") if utf16 else raw.decode("utf-8-sig", errors="replace")
        cues = subs_formats.parse(text)
        if not cues:
            raise MediaLabError("bad_subtitles", "No subtitles found in this file.",
                                "SRT, WebVTT and ASS files are supported.")
        return subs_project.update(pid, {"cues": cues})

    @app.put("/api/subtitles/claude-key")
    async def subs_set_key(body: KeyBody):
        key = body.key.strip()
        if key and not key.startswith("sk-ant-"):
            raise MediaLabError("claude_key_invalid", "This does not look like an Anthropic API key.",
                                "Keys start with sk-ant-. Create one at console.anthropic.com.")
        await run_in_threadpool(subs_secret.set_key, key)
        return subs_secret.status()

    @app.post("/api/subtitles/claude-key/test")
    async def subs_test_key():
        from .subtitles import llm as subs_llm
        model = S.settings.get().subtitles.claude_model

        def run():
            client = subs_llm.ClaudeLLM(subs_secret.get_key(), model)
            out = client.lines("Return each numbered line unchanged.", ["Bonjour."], [], "")
            return {"ok": bool(out), "model": model}

        return await run_in_threadpool(run)

    # -------------------------------------------------------- audio tools
    @app.get("/api/studio/info")
    async def studio_info():
        from .upscale import onnx_rt
        ok, engine_name = onnx_rt.available()
        models = {a: {"installed": ai_assets.path_of(a) is not None, "size": ai_assets.ASSETS[a].size}
                  for a in (audio_tools.SEPARATION_MODEL, audio_tools.DENOISE_MODEL)}
        return {"engine": engine_name if ok else None, "gpu": onnx_rt.uses_gpu(), "models": models}

    @app.post("/api/studio/jobs")
    async def studio_jobs(body: StudioBody):
        op = body.options.get("op")
        if op not in ("separate", "clean"):
            raise MediaLabError("bad_option", "Choose “separate” or “clean”.")
        created = []
        for p in body.paths:
            path = Path(p)
            if not path.is_file() or ffmpeg.file_kind(path) not in ("audio", "video"):
                continue
            thumb = await run_in_threadpool(thumbs.cache_local, path)
            created.append(S.jobs.add("studio", str(path), dict(body.options), title=path.name, thumbnail=thumb,
                                      platform="studio"))
        if not created:
            raise MediaLabError("unsupported_file", "No audio or video file to process.",
                                "Add music, voice recordings or videos with sound.")
        return {"jobs": created}

    # -------------------------------------------------------- player
    @app.get("/api/media/plan")
    async def media_plan(path: str):
        return await run_in_threadpool(playback.plan, Path(path))

    @app.get("/api/media/stream")
    async def media_stream(path: str, mode: str = "transcode", t: float = 0.0, sid: str = "main"):
        p = Path(path)
        if not p.is_file():
            raise HTTPException(404)
        gen, mime = await run_in_threadpool(playback.open_stream, p, mode, max(0.0, t), sid[:32])
        return StreamingResponse(gen, media_type=mime, headers={"Cache-Control": "no-store"})

    @app.post("/api/media/stop")
    async def media_stop(sid: str = "main"):
        playback.stop_stream(sid[:32])
        return {"ok": True}

    @app.get("/api/media/waveform")
    async def media_waveform(path: str, buckets: int = 1600):
        p = Path(path)
        if not p.is_file():
            raise MediaLabError("file_missing", "File not found.")
        return await run_in_threadpool(playback.waveform, p, buckets)

    @app.get("/api/media/filmstrip")
    async def media_filmstrip(path: str, count: int = 12, height: int = 90):
        p = Path(path)
        if not p.is_file():
            raise MediaLabError("file_missing", "File not found.")
        return await run_in_threadpool(playback.filmstrip, p, count, height)

    @app.get("/api/media/library")
    async def media_library(kind: str = "", limit: int = 400):
        """Played-back candidates: files from the history that still exist."""
        def run():
            out = []
            for it in S.db.list_history(kind=kind if kind in ("download", "upscale", "convert", "trim", "studio", "subtitles") else "", limit=limit):
                fp = it.get("file_path")
                if not fp or not Path(fp).is_file():
                    continue
                fk = ffmpeg.file_kind(fp)
                if fk not in ("video", "audio", "image"):
                    continue
                out.append({"path": fp, "name": Path(fp).name, "title": it.get("title"), "kind": fk,
                            "thumbnail": it.get("thumbnail"), "duration": it.get("duration"),
                            "size": it.get("size"), "created_at": it.get("created_at"), "source_kind": it.get("kind")})
            return out
        return {"items": await run_in_threadpool(run)}

    # -------------------------------------------------------- duplicates
    @app.post("/api/duplicates/scan")
    async def dupes_scan(body: ScanBody):
        folders = [f for f in body.folders if f and Path(f).is_dir()]
        if not folders:
            raise MediaLabError("no_folder", "Choose at least one existing folder to scan.")
        task = tasks.start("duplicates", lambda t: duplicates.scan(S.db, folders, body.options, t))
        return task.to_dict()

    @app.get("/api/tasks/{task_id}")
    async def task_get(task_id: str):
        return tasks.get(task_id).to_dict()

    @app.post("/api/tasks/{task_id}/cancel")
    async def task_cancel(task_id: str):
        tasks.cancel(task_id)
        return {"ok": True}

    @app.post("/api/files/trash")
    async def files_trash(body: PathsBody):
        """Move files to the Recycle Bin (recoverable). Used by the duplicate finder."""
        def run():
            done, failed = [], []
            for p in body.paths[:2000]:
                path = Path(p)
                try:
                    if path.is_file():
                        _send_to_recycle_bin(path)
                        done.append(str(path))
                except MediaLabError:
                    failed.append(str(path))
            return {"trashed": done, "failed": failed}
        res = await run_in_threadpool(run)
        bus.publish("history_changed", None)
        return res

    # -------------------------------------------------------- statistics
    @app.get("/api/stats")
    async def stats_get(days: int = 30):
        return await run_in_threadpool(stats.compute, S.db, days)

    # -------------------------------------------------------- logs
    @app.get("/api/logs")
    async def logs(since: int = 0, level: str = "debug", category: str = ""):
        return {"entries": ring.snapshot(since, level, category or None)[-2000:]}

    @app.delete("/api/logs")
    async def logs_clear():
        ring.clear()
        return {"ok": True}

    @app.get("/api/logs/export")
    async def logs_export():
        p = paths().logs / "medialab.log"
        if not p.exists():
            return Response("", media_type="text/plain")
        return FileResponse(p, media_type="text/plain", filename="medialab.log")

    # -------------------------------------------------------- events
    @app.websocket("/api/ws")
    async def ws(websocket: WebSocket):
        host = (websocket.headers.get("host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost", "testserver") or \
                not tokens_equal(websocket.query_params.get("token"), S.token):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        q = bus.subscribe()

        async def pump() -> None:
            await websocket.send_json({"type": "hello", "data": {"version": __version__}})
            while True:
                await websocket.send_json(await q.get())

        async def watch_disconnect() -> None:
            # Detect a closed tab/window right away instead of on the next failed send.
            while (await websocket.receive())["type"] != "websocket.disconnect":
                pass

        tasks = [asyncio.create_task(pump()), asyncio.create_task(watch_disconnect())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            bus.unsubscribe(q)

    # -------------------------------------------------------- se7en Censored
    from .censor import api as censor_api
    censor_api.register(app, S)

    # -------------------------------------------------------- frontend
    dist = frontend_dist()
    if (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}")
        async def spa(full_path: str):
            f = dist / full_path
            if full_path and f.is_file() and dist in f.resolve().parents:
                return FileResponse(f)
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})
    else:
        @app.get("/")
        async def no_ui():
            return PlainTextResponse("se7en API is running. Build the UI with: cd frontend && npm run build")

    return app


def _send_to_recycle_bin(p: Path) -> None:
    """Delete to the Windows Recycle Bin (recoverable) instead of a hard delete."""
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]

    op = SHFILEOPSTRUCTW(None, 3, str(p) + "\0", None, 0x0040 | 0x0010 | 0x0400 | 0x0004, False, None, None)
    if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) != 0:
        raise MediaLabError("delete_failed", "The file could not be moved to the Recycle Bin.")
