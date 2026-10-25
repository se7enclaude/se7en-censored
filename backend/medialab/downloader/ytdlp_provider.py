"""yt-dlp based provider (the default engine for ~1800 sites)."""
from __future__ import annotations

import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from .. import components
from ..errors import Canceled, MediaLabError, classify
from ..logging_setup import get_logger
from ..media import ffmpeg
from ..settings import Settings
from . import formats as fmt
from .base import DownloadContext, DownloadResult, SourceProvider, media_info
from .cookies import ytdlp_cookie_options
from .naming import build_outtmpl
from .platforms import identify

log = get_logger("download")


class _YdlLogger:
    """Routes yt-dlp output to our logs and remembers warnings/errors."""

    def __init__(self, job_id: str | None = None):
        self.job_id = job_id
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def debug(self, msg: str) -> None:
        log.debug(msg, extra={"job_id": self.job_id})

    def info(self, msg: str) -> None:
        log.debug(msg, extra={"job_id": self.job_id})

    def warning(self, msg: str) -> None:
        self.warnings.append(re.sub(r"^WARNING:\s*", "", msg))
        log.warning(msg, extra={"job_id": self.job_id})

    def error(self, msg: str) -> None:
        self.errors.append(msg)
        log.error(msg, extra={"job_id": self.job_id})


def _friendly_warnings(raw: list[str]) -> list[str]:
    out: list[str] = []
    for w in raw:
        low = w.lower()
        if "ffmpeg" in low:
            continue
        if "javascript runtime" in low:
            w = "Install “Deno” in Settings › Components to get every YouTube format."
        if w not in out:
            out.append(w)
    return out[:5]


def _iso_date(d: str | None) -> str | None:
    if d and re.fullmatch(r"\d{8}", d):
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return None


def _best_thumb(info: dict) -> str | None:
    if info.get("thumbnail"):
        return info["thumbnail"]
    thumbs = info.get("thumbnails") or []
    thumbs = [t for t in thumbs if t.get("url")]
    if not thumbs:
        return None
    return max(thumbs, key=lambda t: ((t.get("preference") or 0), (t.get("width") or 0)))["url"]


class YtDlpProvider(SourceProvider):
    id = "ytdlp"
    name = "yt-dlp"
    priority = 10

    def __init__(self) -> None:
        self._ies = None
        self._ies_lock = threading.Lock()
        self._detect_cache: dict[str, dict | None] = {}

    # ------------------------------------------------------------ detection
    def _extractors(self):
        with self._ies_lock:
            if self._ies is None:
                from yt_dlp.extractor import gen_extractor_classes
                self._ies = [ie for ie in gen_extractor_classes() if ie.ie_key() != "Generic"]
            return self._ies

    def can_handle(self, url: str) -> bool:
        return url.startswith(("http://", "https://"))

    def detect(self, url: str) -> dict | None:
        if url in self._detect_cache:
            return self._detect_cache[url]
        result = None
        for ie in self._extractors():
            try:
                if ie.suitable(url):
                    key, name = identify(ie.ie_key(), url)
                    result = {"platform": key, "platform_name": name, "extractor": ie.ie_key(),
                              "supported": ie.working() if hasattr(ie, "working") else True}
                    break
            except Exception:
                continue
        self._detect_cache[url] = result
        return result

    # ------------------------------------------------------------ options
    def _base_opts(self, settings: Settings, logger: _YdlLogger) -> dict[str, Any]:
        d = settings.download
        opts: dict[str, Any] = {
            "logger": logger,
            "quiet": True,
            "no_warnings": False,
            "noprogress": True,
            "socket_timeout": 20,
            "retries": d.max_retries,
            "fragment_retries": d.max_retries,
            "extractor_retries": 3,
            "file_access_retries": 5,
            "windowsfilenames": True,
            "trim_file_name": 180,
            "noplaylist": True,
            "concurrent_fragment_downloads": d.concurrent_fragments,
            "continuedl": True,
            "overwrites": False,
            "allow_unplayable_formats": False,  # never touch DRM formats
            "color": {"stdout": "never", "stderr": "never"},
        }
        ff = components.ffmpeg_dir()
        if ff:
            opts["ffmpeg_location"] = ff
        rt = components.js_runtime()
        if rt:
            opts["js_runtimes"] = {rt[0]: {"path": rt[1]}}
        if d.rate_limit_kbps:
            opts["ratelimit"] = d.rate_limit_kbps * 1024
        return opts

    # ------------------------------------------------------------ analyze
    def analyze(self, url: str, settings: Settings) -> dict[str, Any]:
        import yt_dlp

        logger = _YdlLogger()
        opts = self._base_opts(settings, logger)
        opts.update({"skip_download": True, "extract_flat": "in_playlist",
                     "playlistend": settings.download.playlist_limit})
        t0 = time.perf_counter()
        with ytdlp_cookie_options(settings) as cookie_opts:
            opts.update(cookie_opts)
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    info = ydl.sanitize_info(info)
            except yt_dlp.utils.DownloadError as e:
                raise classify(_error_text(e, logger)) from e
        log.info("Analyzed %s in %.2fs", url, time.perf_counter() - t0)
        return self._to_media_info(url, info, settings, logger)

    def _to_media_info(self, url: str, info: dict, settings: Settings, logger: _YdlLogger) -> dict:
        platform, platform_name = identify(info.get("extractor_key") or info.get("ie_key"), url)
        common = dict(
            url=url, webpage_url=info.get("webpage_url") or url, provider=self.id,
            platform=platform, platform_name=platform_name, id=info.get("id"),
            title=info.get("title") or info.get("id"), uploader=info.get("uploader") or info.get("channel"),
            uploader_url=info.get("uploader_url") or info.get("channel_url"),
            thumbnail=_best_thumb(info), upload_date=_iso_date(info.get("upload_date")),
            description=(info.get("description") or "")[:5000], tags=(info.get("tags") or [])[:30],
            view_count=info.get("view_count"), like_count=info.get("like_count"),
            warnings=_friendly_warnings(logger.warnings),
        )
        if info.get("_type") in ("playlist", "multi_video") or "entries" in info:
            entries = []
            for e in info.get("entries") or []:
                if not e:
                    continue
                eurl = e.get("webpage_url") or e.get("url")
                if eurl and not eurl.startswith("http"):
                    eurl = e.get("original_url") or eurl
                entries.append({"url": eurl, "title": e.get("title") or e.get("id"), "id": e.get("id"),
                                "duration": e.get("duration"), "thumbnail": _best_thumb(e),
                                "uploader": e.get("uploader") or e.get("channel")})
            return media_info(**common, kind="playlist", entries=entries,
                              entry_count=info.get("playlist_count") or len(entries))

        summary = fmt.summarize(info)
        if info.get("is_live") or info.get("live_status") == "is_live":
            common["warnings"].append("Live stream: the download records from now until the stream ends.")
        kind = "video" if summary["has_video"] else ("audio" if summary["has_audio"] else "video")
        rec = fmt.smart_recommendation(summary, container=settings.download.default_container,
                                       avg_speed_bps=_speed_stats.average())
        best_q = summary["qualities"][0] if summary["qualities"] else {}
        return media_info(
            **common, kind=kind, duration=info.get("duration"),
            width=info.get("width") or best_q.get("width"), height=info.get("height") or best_q.get("height"),
            fps=info.get("fps"), ext=info.get("ext"), is_live=bool(info.get("is_live")),
            qualities=summary["qualities"], audio=summary["audio"], audio_codecs=summary["audio_codecs"],
            video_codecs=summary["video_codecs"], has_video=summary["has_video"],
            has_audio=summary["has_audio"], max_height=summary["max_height"], recommended=rec,
            size=best_q.get("size"),
        )

    # ------------------------------------------------------------ download
    def fallback_strategies(self, error_code: str, attempt: int) -> dict[str, Any] | None:
        plans: dict[str, list[dict[str, Any]]] = {
            "format_unavailable": [{"relax_format": True, "label": "Trying the closest available format"}],
            "no_video_stream": [{"relax_format": True, "fresh": True, "label": "Retrying with another video format"},
                                {"relax_format": True, "fresh": True, "force_ipv4": True,
                                 "label": "Retrying with another video format over IPv4"}],
            "no_audio_stream": [{"relax_format": True, "fresh": True, "label": "Retrying with another audio format"}],
            "forbidden": [{"fresh": True, "concurrent_fragments": 1, "label": "Re-analyzing with fresh media links"},
                          {"fresh": True, "force_ipv4": True, "label": "Retrying over IPv4"}],
            "extractor_broken": [{"fresh": True, "label": "Retrying with a fresh extraction"}],
            "network": [{"label": "Resuming after network error"},
                        {"force_ipv4": True, "concurrent_fragments": 2, "label": "Retrying over IPv4, fewer connections"}],
            "server_error": [{"label": "Retrying after server error"}, {"concurrent_fragments": 1, "label": "Retrying with a single connection"}],
            "unknown": [{"label": "Retrying"}],
        }
        plan = plans.get(error_code, [])
        return plan[attempt] if attempt < len(plan) else None

    def download(self, url: str, options: dict[str, Any], ctx: DownloadContext) -> DownloadResult:
        import yt_dlp

        s = ctx.settings
        d = s.download
        strategy = ctx.strategy or {}
        mode = options.get("mode", d.default_mode)
        container = options.get("container", d.default_container)
        vcodec = options.get("vcodec", d.default_video_codec)
        audio_format = options.get("audio_format", d.default_audio_format)
        audio_bitrate = options.get("audio_bitrate") or None
        quality = str(options.get("quality", d.default_quality))
        out_root = Path(options.get("output_dir") or d.default_folder).expanduser()
        out_root.mkdir(parents=True, exist_ok=True)
        platform = options.get("platform")
        self._ensure_tools(ctx, platform)

        logger = _YdlLogger(ctx.job_id)
        t_start = time.monotonic()
        opts = self._base_opts(s, logger)
        if options.get("referer"):  # a video found inside a web page: the server may want to know which one
            opts["http_headers"] = {"Referer": options["referer"]}
        if options.get("user_agent"):  # session ouverte dans une fenêtre de l'appli : même navigateur qu'elle
            opts.setdefault("http_headers", {})["User-Agent"] = options["user_agent"]
        if options.get("impersonate"):  # empreinte réseau d'un vrai navigateur (curl_cffi), exigée par Cloudflare
            from yt_dlp.networking.impersonate import ImpersonateTarget
            opts["impersonate"] = ImpersonateTarget.from_str(options["impersonate"])
        if strategy.get("concurrent_fragments"):
            opts["concurrent_fragment_downloads"] = strategy["concurrent_fragments"]
        if strategy.get("force_ipv4"):
            opts["source_address"] = "0.0.0.0"
        if strategy.get("fresh"):
            opts["cachedir"] = False

        outtmpl = build_outtmpl(
            folder_structure=options.get("folder_structure", d.folder_structure),
            naming_template=options.get("naming_template", d.naming_template),
            platform=platform, platform_display=options.get("platform_name"),
            platform_overrides=d.platform_folders, audio_only=mode == "audio", image_only=mode == "image",
        )
        # Stage everything in a per-job folder: partial files stay resumable, and an
        # existing file at the destination is never overwritten nor mistaken for this one.
        job_root = Path(ctx.temp_dir)
        if job_root.anchor.lower() != out_root.anchor.lower():
            job_root = out_root / ".medialab-parts" / ctx.job_id  # same drive = instant publish
        stage, temp_dir = job_root / "out", job_root / "parts"
        stage.mkdir(parents=True, exist_ok=True)
        temp_dir.mkdir(parents=True, exist_ok=True)
        opts.update({
            "paths": {"home": str(stage), "temp": str(temp_dir)},
            "outtmpl": {"default": outtmpl, "thumbnail": outtmpl.rsplit(".", 1)[0] + ".%(ext)s"},
        })

        if mode == "image":
            return self._download_image(url, options, ctx, opts, stage, out_root, job_root, logger)

        # ---- format selection on the *fresh* format list
        holder: dict[str, Any] = {}

        def selector(sel_ctx):
            fmts = sel_ctx.get("formats") or []
            spec = None
            if not strategy.get("relax_format"):
                try:
                    sel = fmt.select(fmts, mode=mode, quality=quality, container=container, vcodec=vcodec,
                                     audio_format=audio_format, audio_bitrate=audio_bitrate)
                    holder.update(sel)
                    spec = sel["spec"]
                except LookupError:
                    spec = None
            if spec is None:
                h = "" if quality in ("best", "") else f"[height<={int(quality)}]"
                spec = "ba/b" if mode == "audio" else (f"bv*{h}+ba/b{h}/bv*+ba/b" if mode == "av" else f"bv*{h}/b{h}/bv*")
                holder["spec"] = spec
            log.info("Format selected: %s", spec, extra={"job_id": ctx.job_id})
            yield from holder["ydl"].build_format_selector(spec)(sel_ctx)

        opts["format"] = selector
        pps: list[dict[str, Any]] = []
        embed_thumb = options.get("embed_thumbnail", d.embed_thumbnail)
        if mode == "audio":
            if audio_format != "best":
                target = {"aac": "aac", "m4a": "m4a"}.get(audio_format, audio_format)
                pps.append({"key": "FFmpegExtractAudio", "preferredcodec": target,
                            "preferredquality": str(audio_bitrate or d.default_audio_bitrate)
                            if audio_format in ("mp3", "aac", "m4a", "opus") else None,
                            "nopostoverwrites": False})
            else:
                pps.append({"key": "FFmpegExtractAudio", "preferredcodec": "best"})
            if audio_format == "wav":
                embed_thumb = False
        else:
            opts["merge_output_format"] = container
            pps.append({"key": "FFmpegVideoRemuxer", "preferedformat": container})
            if container == "webm":
                embed_thumb = False
        if options.get("write_subtitles", d.write_subtitles) and mode != "audio":
            opts.update({"writesubtitles": True, "subtitleslangs": [x.strip() for x in d.subtitle_langs.split(",") if x.strip()]})
            pps.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})
        if options.get("embed_metadata", d.embed_metadata) or options.get("embed_chapters", d.embed_chapters):
            pps.append({"key": "FFmpegMetadata", "add_metadata": options.get("embed_metadata", d.embed_metadata),
                        "add_chapters": options.get("embed_chapters", d.embed_chapters), "add_infojson": False})
        if embed_thumb and components.find_binary("ffmpeg"):
            opts["writethumbnail"] = True
            pps.insert(0, {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"})
            pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
        if options.get("write_description", d.write_description):
            opts["writedescription"] = True
        opts["postprocessors"] = pps

        if d.use_aria2c and components.find_binary("aria2c"):
            opts["external_downloader"] = {"http": components.find_binary("aria2c")}
            opts["external_downloader_args"] = {"aria2c": ["-x16", "-s16", "-k1M", "--file-allocation=none"]}

        # ---- progress
        parts: dict[str, tuple[int, int]] = {}
        final_files: list[str] = []
        last_emit = [0.0]
        is_aria = "external_downloader" in opts

        def expected_sizes() -> dict[str, int]:
            out = {}
            for key in ("video", "audio"):
                f = holder.get(key)
                if f:
                    size, _ = fmt.estimate_size(f, holder.get("duration"))
                    if size:
                        out[str(f.get("format_id"))] = size
            return out

        def hook(dd: dict) -> None:
            if ctx.cancel.is_set():
                raise Canceled()
            info_d = dd.get("info_dict") or {}
            holder.setdefault("duration", info_d.get("duration"))
            fid = str(info_d.get("format_id") or dd.get("filename"))
            status = dd.get("status")
            if status == "downloading":
                total = dd.get("total_bytes") or dd.get("total_bytes_estimate") or 0
                parts[fid] = (int(dd.get("downloaded_bytes") or 0), int(total))
            elif status == "finished":
                done = int(dd.get("total_bytes") or dd.get("downloaded_bytes") or parts.get(fid, (0, 0))[0])
                parts[fid] = (done, done)
            now = time.monotonic()
            if status == "downloading" and now - last_emit[0] < 0.25:
                return
            last_emit[0] = now
            exp = expected_sizes()
            keys = set(parts) | set(exp)
            downloaded = sum(parts.get(k, (0, 0))[0] for k in keys)
            total = sum(max(parts.get(k, (0, 0))[1], exp.get(k, 0)) for k in keys)
            speed = dd.get("speed") or 0
            if speed:
                _speed_stats.add(speed)
            eta = (total - downloaded) / speed if speed and total > downloaded else dd.get("eta")
            frag_count = dd.get("fragment_count")
            connections = 16 if is_aria else (opts["concurrent_fragment_downloads"] if frag_count else 1)
            ctx.progress({
                "stage": "downloading",
                "downloaded": downloaded, "total": total or None,
                "percent": (downloaded / total * 100) if total else None,
                "speed": speed, "eta": eta, "connections": connections,
                "fragment": [dd.get("fragment_index"), frag_count] if frag_count else None,
                "part": "audio" if (holder.get("audio") and fid == str(holder["audio"].get("format_id"))) else "video",
            })

        pp_labels = {"Merger": "Merging video and audio", "FFmpegVideoRemuxer": "Remuxing (no re-encode)",
                     "FFmpegExtractAudio": "Extracting audio", "FFmpegMetadata": "Writing metadata",
                     "EmbedThumbnail": "Embedding thumbnail", "FFmpegEmbedSubtitle": "Embedding subtitles",
                     "FFmpegThumbnailsConvertor": "Converting thumbnail", "MoveFiles": "Finalizing"}

        def pp_hook(dd: dict) -> None:
            if ctx.cancel.is_set():
                raise Canceled()
            if dd.get("status") == "started":
                name = dd.get("postprocessor")
                ctx.progress({"stage": "processing", "label": pp_labels.get(name, name), "percent": None})

        opts["progress_hooks"] = [hook]
        opts["postprocessor_hooks"] = [pp_hook]
        opts["post_hooks"] = [lambda path: final_files.append(path)]

        with ytdlp_cookie_options(s) as cookie_opts:
            opts.update(cookie_opts)
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    holder["ydl"] = ydl
                    info = ydl.extract_info(url, download=True)
            except Canceled:
                raise
            except yt_dlp.utils.DownloadCancelled as e:
                raise Canceled() from e
            except yt_dlp.utils.DownloadError as e:
                if _has_cause(e, Canceled) or ctx.cancel.is_set():
                    raise Canceled() from e
                raise classify(_error_text(e, logger)) from e
            except MediaLabError:
                raise
            except OSError as e:
                raise classify(e) from e

        if info is None:
            raise classify(_error_text(None, logger))
        path = final_files[-1] if final_files else _requested_path(info)
        if not path or not os.path.exists(path):
            raise MediaLabError("output_missing", "The download finished but the output file is missing.",
                                "An antivirus may have quarantined it. Retry the download.", True)

        # ---- check the real file and fix it when needed (never trust the format list alone)
        path, final = self._finalize(path, mode, container, vcodec, holder, ctx)

        path = _publish(stage, out_root, path)
        shutil.rmtree(job_root, ignore_errors=True)
        _cleanup_dir(job_root.parent)
        size = os.path.getsize(path)
        height = holder.get("height") or info.get("height")
        vf = holder.get("video") or {}
        af = holder.get("audio") or {}
        if mode == "audio":
            fmt_label = f"{Path(path).suffix.lstrip('.').upper()}"
            abr = af.get("abr")
            if audio_format not in ("best", "wav", "flac"):
                fmt_label += f" {audio_bitrate or d.default_audio_bitrate} kbps"
            elif abr:
                fmt_label += f" {int(abr)} kbps"
        else:
            codec = final.get("vcodec") or fmt.vcodec_family(vf.get("vcodec"))
            height = final.get("height") or height
            fmt_label = f"{Path(path).suffix.lstrip('.').upper()} · {(codec or '').upper()}".strip(" ·")
        pkey, pname = identify(info.get("extractor_key"), url)
        return DownloadResult(
            file_path=path, title=info.get("title"), platform=pkey, thumbnail=_best_thumb(info),
            resolution=None if mode == "audio" else (f"{height}p" if height else None),
            format=fmt_label, size=size, duration=info.get("duration"),
            meta={"uploader": info.get("uploader") or info.get("channel"), "upload_date": _iso_date(info.get("upload_date")),
                  "webpage_url": info.get("webpage_url"), "platform_name": pname, "format_id": info.get("format_id"),
                  "warnings": logger.warnings[-5:], "reencoded": bool(final.get("reencoded")),
                  "vcodec": final.get("vcodec"), "acodec": final.get("acodec"),
                  "elapsed": round(time.monotonic() - t_start, 1)},
        )

    # ------------------------------------------------------------ tools
    def _ensure_tools(self, ctx: DownloadContext, platform: str | None) -> None:
        """FFmpeg is mandatory (it joins the picture and the sound): install it on the spot if missing."""
        def show(name: str):
            def cb(done: int, total: int) -> None:
                ctx.progress({"stage": "downloading", "label": f"Installing {name} (one time only)",
                              "percent": done / total * 100 if total else None, "speed": 0})
            return cb

        if not components.is_installed("ffmpeg"):
            if not components.ensure("ffmpeg", show("FFmpeg")):
                raise MediaLabError("ffmpeg_missing", "FFmpeg is required to assemble the video and its sound.",
                                    "Install it from Settings › Components, then retry.", False,
                                    extra={"component": "ffmpeg"})
            ffmpeg.reset_encoders()
        if platform == "youtube" and not components.js_runtime():
            components.ensure("deno", show("Deno"))  # best effort: complete format list, fewer 403

    # ------------------------------------------------------------ image mode
    def _download_image(self, url: str, options: dict[str, Any], ctx: DownloadContext, opts: dict[str, Any],
                        stage: Path, out_root: Path, job_root: Path, logger: _YdlLogger) -> DownloadResult:
        """Image mode: the post's picture (thumbnail / cover) in its highest resolution."""
        import yt_dlp
        from PIL import Image

        img_fmt = str(options.get("image_format") or "jpg").lower()
        if img_fmt not in ("jpg", "png", "webp"):
            img_fmt = "jpg"
        opts.pop("format", None)
        opts.update({"skip_download": True, "writethumbnail": True,
                     "postprocessors": [{"key": "FFmpegThumbnailsConvertor", "format": img_fmt, "when": "before_dl"}]})
        ctx.progress({"stage": "downloading", "label": "Fetching the image", "percent": None})
        with ytdlp_cookie_options(ctx.settings) as cookie_opts:
            opts.update(cookie_opts)
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True) or {}
            except yt_dlp.utils.DownloadError as e:
                raise classify(_error_text(e, logger)) from e
        images = [f for f in stage.rglob("*") if f.is_file() and f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")]
        if not images:
            raise MediaLabError("no_image", "This link has no image to download.",
                                "Choose Video or Audio instead.", False)
        main = max(images, key=lambda f: f.stat().st_size)
        with Image.open(main) as im:
            w, h = im.size
        path = _publish(stage, out_root, str(main))
        shutil.rmtree(job_root, ignore_errors=True)
        _cleanup_dir(job_root.parent)
        pkey, pname = identify(info.get("extractor_key"), url)
        return DownloadResult(
            file_path=path, title=info.get("title"), platform=pkey, thumbnail=_best_thumb(info),
            resolution=f"{w}×{h}", format=Path(path).suffix.lstrip(".").upper(), size=os.path.getsize(path),
            meta={"platform_name": pname, "webpage_url": info.get("webpage_url"), "kind": "image",
                  "uploader": info.get("uploader")},
        )

    # ------------------------------------------------------------ post steps
    def _finalize(self, path: str, mode: str, container: str, vcodec: str, holder: dict,
                  ctx: DownloadContext) -> tuple[str, dict]:
        """Guarantee the file contains exactly what was asked for, by probing the real file.

        video+audio -> a picture AND a sound track; video -> a picture, no sound;
        audio -> sound, no picture (cover art allowed); "compat" -> H.264/AAC that
        every player reads (Windows' player cannot show AV1 without an extension).
        """
        info = ffmpeg.probe(path)
        has_v, has_a = info.get("has_video"), info.get("has_audio")
        acodecs = [fmt.acodec_family(a.get("codec")) for a in info.get("audio", [])]
        if mode == "audio":
            if not has_a:
                raise MediaLabError("no_audio_stream", "The downloaded file contains no sound.",
                                    "se7en retries with another audio format.", True)
            if has_v:
                path = self._drop_video(path, info, ctx)
            return path, {"acodec": acodecs[0] if acodecs else None}
        if not has_v:
            log.warning("Downloaded file has no video stream (format %s)", holder.get("spec"),
                        extra={"job_id": ctx.job_id})
            raise MediaLabError("no_video_stream", "The platform only sent the sound for this quality.",
                                "se7en retries automatically with another video format.", True)
        expects_audio = bool(holder.get("audio")) or bool(holder.get("video") and fmt.has_audio(holder["video"]))
        if mode == "av" and not has_a and expects_audio:
            raise MediaLabError("no_audio_stream", "The video was downloaded without its sound.",
                                "se7en retries automatically with another audio format.", True)
        if mode == "video" and has_a:
            path = self._strip_audio(path, ctx)
            info = ffmpeg.probe(path)
            acodecs = []
        fam = fmt.vcodec_family(info.get("vcodec"))
        target = None
        if vcodec == "compat" and container == "mp4" and fam not in fmt.COMPAT_VCODECS:
            target = "h264"
        elif vcodec not in ("auto", "compat", "", None) and fam != vcodec:
            target = vcodec
        audio_bad = vcodec == "compat" and container == "mp4" and any(a not in fmt.COMPAT_ACODECS for a in acodecs)
        out = {"vcodec": fam, "acodec": acodecs[0] if acodecs else None, "height": info.get("height"),
               "width": info.get("width")}
        if target:
            path = self._reencode(path, target, container, ctx, "compat" if vcodec == "compat" else "requested codec",
                                  info, aac_audio=vcodec == "compat")
            out.update(vcodec=target, reencoded=True, acodec="aac" if audio_bad else out["acodec"])
        elif audio_bad:
            path = self._reencode_audio(path, info, ctx)
            out.update(acodec="aac", reencoded=True)
        return path, out

    def _strip_audio(self, path: str, ctx: DownloadContext) -> str:
        ctx.progress({"stage": "processing", "label": "Removing audio track (no re-encode)", "percent": None})
        src = Path(path)
        tmp = src.with_name(src.stem + ".noaudio" + src.suffix)
        ffmpeg.run(["-i", str(src), "-map", "0", "-map", "-0:a", "-c", "copy", str(tmp)],
                   cancel=ctx.cancel, label="strip audio")
        os.replace(tmp, src)
        return str(src)

    def _drop_video(self, path: str, info: dict, ctx: DownloadContext) -> str:
        ctx.progress({"stage": "processing", "label": "Keeping the sound only", "percent": None})
        src = Path(path)
        tmp = src.with_name(src.stem + ".audio" + src.suffix)
        args = ["-i", str(src), "-map", "0:a"]
        if info.get("cover_index") is not None:
            args += ["-map", f"0:{info['cover_index']}", "-disposition:v:0", "attached_pic"]
        ffmpeg.run([*args, "-c", "copy", str(tmp)], cancel=ctx.cancel, label="drop video")
        os.replace(tmp, src)
        return str(src)

    def _reencode_audio(self, path: str, info: dict, ctx: DownloadContext) -> str:
        label = "Converting the sound to AAC (plays everywhere)"
        ctx.progress({"stage": "processing", "label": label, "percent": None})
        src = Path(path)
        tmp = src.with_name(src.stem + ".aac" + src.suffix)
        ffmpeg.run(["-i", str(src), "-map", "0", "-c", "copy", "-c:a", "aac", "-b:a", "256k",
                    "-movflags", "+faststart", str(tmp)], duration=info.get("duration"), cancel=ctx.cancel,
                   label="audio to aac",
                   on_progress=lambda f, _s: ctx.progress({"stage": "processing", "label": label, "percent": f * 100}))
        os.replace(tmp, src)
        return str(src)

    def _reencode(self, path: str, vcodec: str, container: str, ctx: DownloadContext, reason: str | None,
                  info: dict, aac_audio: bool = False) -> str:
        encoder = ffmpeg.best_encoder(vcodec)
        if not encoder:
            log.warning("No encoder for %s; keeping the original codec", vcodec, extra={"job_id": ctx.job_id})
            return path
        where = "GPU" if ffmpeg.is_hw(encoder) else "CPU"
        label = (f"Converting to H.264 so it plays everywhere ({where})" if reason == "compat"
                 else f"Re-encoding to {vcodec.upper()} ({where})")
        log.info("%s with %s — reason: %s", label, encoder, reason, extra={"job_id": ctx.job_id})
        src = Path(path)
        tmp = src.with_name(src.stem + ".reencode" + src.suffix)
        allowed_a = fmt.COMPAT_ACODECS if aac_audio else fmt.CONTAINER_ACODECS.get(container, ())
        a_ok = all(fmt.acodec_family(a.get("codec")) in allowed_a for a in info.get("audio", []))
        chain = []
        if info.get("is_hdr") and vcodec == "h264":
            from ..upscale.filters import TONEMAP
            chain.append(TONEMAP)  # H.264 players are SDR: tone-map instead of showing washed-out colors
        cover = info.get("cover_index") if container in ("mp4", "mkv") else None
        args = ["-i", str(src), "-map", "0:V:0", "-map", "0:a?"]
        if cover is not None:
            args += ["-map", f"0:{cover}"]
        # CPU encoders favour speed (4K on a laptop); hardware encoders are fast anyway.
        args += ffmpeg.encoder_args(encoder, 20 if vcodec == "h264" else 22, fast=not ffmpeg.is_hw(encoder))
        if chain:
            args += ["-filter:v:0", ",".join(chain)]
        if cover is not None:
            args += ["-c:v:1", "copy", "-disposition:v:1", "attached_pic"]
        args += ["-c:a", "copy" if a_ok else ("libopus" if container == "webm" else "aac")]
        if not a_ok and container != "webm":
            args += ["-b:a", "256k"]
        args += ["-map_metadata", "0", "-movflags", "+faststart", str(tmp)]
        def run(a: list[str]) -> None:
            ffmpeg.run(a, duration=info.get("duration"), cancel=ctx.cancel, label="re-encode",
                       on_progress=lambda f, st: ctx.progress({"stage": "processing", "label": label, "percent": f * 100,
                                                                "speed": 0, "eta": _encode_eta(f, st)}))

        try:
            run(args)
        except MediaLabError as e:
            cpu = {"h264": "libx264", "h265": "libx265", "av1": "libsvtav1", "vp9": "libvpx-vp9"}.get(vcodec)
            if e.code != "encoder_failed" or not ffmpeg.is_hw(encoder) or cpu not in ffmpeg.working_encoders():
                raise
            log.warning("GPU encoder %s failed; retrying on the CPU with %s", encoder, cpu, extra={"job_id": ctx.job_id})
            start = args.index("-c:v")
            end = start + len(ffmpeg.encoder_args(encoder, 20 if vcodec == "h264" else 22, fast=False))
            run(args[:start] + ffmpeg.encoder_args(cpu, 20 if vcodec == "h264" else 22, fast=True) + args[end:])
        os.replace(tmp, src)
        return str(src)


def _encode_eta(frac: float, state: dict) -> float | None:
    """Remaining seconds of an FFmpeg job from its progress (``speed=2.5x``)."""
    try:
        speed = float(str(state.get("speed", "0")).strip().rstrip("x") or 0)
        done_s = int(state.get("out_time_us") or 0) / 1_000_000
    except ValueError:
        return None
    if speed <= 0 or frac <= 0 or frac >= 1:
        return None
    return (done_s / frac - done_s) / speed


def _requested_path(info: dict) -> str | None:
    for r in info.get("requested_downloads") or []:
        if r.get("filepath"):
            return r["filepath"]
    return info.get("filepath") or info.get("_filename")


def _has_cause(exc: BaseException, kind: type) -> bool:
    seen = set()
    cur: BaseException | None = exc
    while cur and id(cur) not in seen:
        if isinstance(cur, kind):
            return True
        seen.add(id(cur))
        exc_info = getattr(cur, "exc_info", None)
        if exc_info and len(exc_info) > 1 and isinstance(exc_info[1], kind):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _error_text(e: BaseException | None, logger: _YdlLogger) -> str:
    parts = []
    if e is not None:
        parts.append(str(e))
    parts.extend(logger.errors[-3:])
    return "\n".join(p for p in parts if p) or "Unknown download error"


def _unique(target: Path) -> Path:
    if not target.exists():
        return target
    n = 1
    while True:
        cand = target.with_name(f"{target.stem} ({n}){target.suffix}")
        if not cand.exists():
            return cand
        n += 1


def _publish(stage: Path, dest_root: Path, main: str) -> str:
    """Move staged files to the destination tree; returns the final main file path."""
    main_p = Path(main).resolve()
    moved_main: Path | None = None
    suffix_for_main = ""
    files = sorted((f for f in stage.rglob("*") if f.is_file()), key=lambda f: f.resolve() != main_p)
    for f in files:
        rel = f.relative_to(stage)
        target = dest_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if f.resolve() == main_p:
            final = _unique(target)
            suffix_for_main = final.stem[len(target.stem):]  # e.g. " (1)"
            moved_main = final
        elif f.name.startswith(main_p.stem):  # side files (subtitles, description) follow the main numbering
            final = _unique(target.with_name(main_p.stem + suffix_for_main + f.name[len(main_p.stem):]))
        else:
            final = _unique(target)
        shutil.move(str(f), str(final))
    if moved_main is None:
        raise MediaLabError("output_missing", "The downloaded file could not be moved to its folder.",
                            "Check that the destination folder is writable.", True)
    return str(moved_main)


def _cleanup_dir(p: Path) -> None:
    try:
        if p.name == ".medialab-parts" and p.exists() and not any(p.iterdir()):
            p.rmdir()
    except OSError:
        pass


class _SpeedStats:
    """Rolling average of observed download speeds (feeds Smart Quality)."""

    def __init__(self) -> None:
        self._samples: list[float] = []
        self._lock = threading.Lock()

    def add(self, v: float) -> None:
        with self._lock:
            self._samples.append(v)
            del self._samples[:-200]

    def average(self) -> float | None:
        with self._lock:
            if len(self._samples) < 10:
                return None
            s = sorted(self._samples)
            return s[len(s) // 2]  # median resists spikes


_speed_stats = _SpeedStats()
