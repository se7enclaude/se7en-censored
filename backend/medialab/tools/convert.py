"""Local file converter: video, audio, image and subtitle formats.

Video/audio/subtitles go through FFmpeg (hardware encoders when available),
images through Pillow. Rules:

* nothing is re-encoded when it does not need to be ("auto" quality): a
  container change with compatible codecs is an instant, lossless remux, and
  extracting the sound of a video keeps the original audio when possible;
* the output never overwrites anything (a " (n)" suffix is added) and is
  written under a temporary name first, so a canceled job leaves no half file;
* every option the UI shows maps to a real FFmpeg / Pillow parameter.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from ..errors import Canceled, MediaLabError
from ..logging_setup import get_logger
from ..media import ffmpeg
from ..media.images import flatten, open_image, upright
from ..downloader.formats import acodec_family, vcodec_family

log = get_logger("process")
Progress = Callable[[float, str, float | None], None]  # fraction, label, eta seconds


@dataclass(frozen=True)
class Target:
    id: str
    kind: str  # video | audio | image | subtitle
    ext: str
    label: str
    vcodec: str | None = None
    acodec: str | None = None
    lossless: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


TARGETS: dict[str, Target] = {t.id: t for t in [
    # ---- video
    Target("mp4", "video", "mp4", "MP4 · H.264", "h264", "aac", note="plays_everywhere"),
    Target("mp4-h265", "video", "mp4", "MP4 · H.265", "h265", "aac", note="half_size"),
    Target("mp4-av1", "video", "mp4", "MP4 · AV1", "av1", "aac", note="smallest"),
    Target("mkv", "video", "mkv", "MKV", "h264", "aac", note="keeps_everything"),
    Target("webm", "video", "webm", "WebM · VP9", "vp9", "opus", note="web"),
    Target("mov", "video", "mov", "MOV · H.264", "h264", "aac", note="apple"),
    Target("mov-prores", "video", "mov", "MOV · ProRes", "prores", "pcm_s16le", note="editing"),
    Target("avi", "video", "avi", "AVI", "mpeg4", "mp3", note="old_devices"),
    Target("gif", "video", "gif", "GIF", "gif", None, note="animated"),
    # ---- audio
    Target("mp3", "audio", "mp3", "MP3", acodec="mp3", note="plays_everywhere"),
    Target("m4a", "audio", "m4a", "AAC (M4A)", acodec="aac", note="apple"),
    Target("opus", "audio", "opus", "Opus", acodec="opus", note="smallest"),
    Target("ogg", "audio", "ogg", "OGG Vorbis", acodec="vorbis", note="web"),
    Target("flac", "audio", "flac", "FLAC", acodec="flac", lossless=True, note="lossless"),
    Target("wav", "audio", "wav", "WAV", acodec="pcm", lossless=True, note="editing"),
    Target("alac", "audio", "m4a", "ALAC (M4A)", acodec="alac", lossless=True, note="apple_lossless"),
    Target("aiff", "audio", "aiff", "AIFF", acodec="pcm_be", lossless=True, note="editing"),
    Target("wma", "audio", "wma", "WMA", acodec="wma", note="old_devices"),
    # ---- image
    Target("png", "image", "png", "PNG", lossless=True, note="lossless"),
    Target("jpg", "image", "jpg", "JPG", note="plays_everywhere"),
    Target("webp", "image", "webp", "WebP", note="web"),
    Target("avif", "image", "avif", "AVIF", note="smallest"),
    Target("heic", "image", "heic", "HEIC", note="apple"),
    Target("bmp", "image", "bmp", "BMP", lossless=True, note="old_devices"),
    Target("tiff", "image", "tiff", "TIFF", lossless=True, note="print"),
    Target("gif-image", "image", "gif", "GIF", note="animated"),
    Target("ico", "image", "ico", "ICO", note="icon"),
    Target("pdf", "image", "pdf", "PDF", note="document"),
    # ---- subtitles
    Target("srt", "subtitle", "srt", "SRT", note="plays_everywhere"),
    Target("vtt", "subtitle", "vtt", "WebVTT", note="web"),
    Target("ass", "subtitle", "ass", "ASS", note="styled"),
]}

# Which target kinds make sense for an input kind.
ALLOWED = {
    "video": ("video", "audio", "subtitle"),
    "audio": ("audio",),
    "image": ("image",),
    "animated": ("image", "video"),  # animated GIF / WebP
    "subtitle": ("subtitle",),
}
DEFAULT_ABR = {"mp3": 320, "aac": 256, "opus": 160, "vorbis": 192, "wma": 192, "mp3-in-avi": 192}
CRF = {  # quality level -> constant quality value per codec family
    "h264": {"high": 18, "normal": 23, "small": 28},
    "h265": {"high": 20, "normal": 25, "small": 30},
    "av1": {"high": 16, "normal": 22, "small": 30},  # encoder_args adds +10 for SVT/AOM
    "vp9": {"high": 18, "normal": 23, "small": 28},  # encoder_args adds +12
}


def catalog() -> dict[str, Any]:
    return {"targets": [t.to_dict() for t in TARGETS.values()], "allowed": ALLOWED}


def input_kind(path: Path, info: dict | None = None) -> str | None:
    kind = ffmpeg.file_kind(path)
    if kind == "image" and path.suffix.lower() in (".gif", ".webp"):
        try:
            with open_image(path) as im:
                if getattr(im, "n_frames", 1) > 1:
                    return "animated"
        except Exception:
            pass
    if kind is None and info:
        kind = info.get("kind")
    return kind


def output_path(src: Path, target: Target, opts: dict) -> Path:
    folder = Path(opts.get("output_dir") or src.parent)
    folder.mkdir(parents=True, exist_ok=True)
    stem = src.stem
    cand = folder / f"{stem}.{target.ext}"
    n = 1
    while cand.exists() or cand.resolve() == src.resolve():
        cand = folder / f"{stem} ({n}).{target.ext}"
        n += 1
    return cand


def _part_path(dst: Path) -> Path:
    return dst.with_name(f"{dst.stem}.se7en-part{dst.suffix}")


# ================================================================ entry point
def convert(src: Path, target_id: str, opts: dict, cancel: threading.Event, progress: Progress,
            dst: Path | None = None) -> dict:
    target = TARGETS.get(target_id)
    if not target:
        raise MediaLabError("unknown_target", f"Unknown output format “{target_id}”.")
    if not src.is_file():
        raise MediaLabError("source_missing", "The source file no longer exists.",
                            "It may have been moved or deleted.")
    info = None
    kind = ffmpeg.file_kind(src)
    if kind in ("video", "audio") or (kind is None and target.kind in ("video", "audio")):
        info = ffmpeg.probe(src)
    kind = input_kind(src, info)
    if kind is None:
        raise MediaLabError("unsupported_file", "This file type cannot be converted.",
                            "Video, audio, image and subtitle files are supported.")
    if target.kind not in ALLOWED.get(kind, ()):
        raise MediaLabError("incompatible_target", f"A {kind} file cannot be converted to {target.label}.",
                            "Choose a format of the same kind.")
    dst = dst or output_path(src, target, opts)
    part = _part_path(dst)
    t0 = time.perf_counter()
    try:
        if target.kind == "image":
            meta = _convert_image(src, part, target, opts, progress)
        elif target.kind == "subtitle":
            meta = _convert_subtitle(src, part, target, info, cancel, progress)
        else:
            if info is None:
                info = ffmpeg.probe(src)
            meta = _convert_av(src, part, target, opts, info, cancel, progress)
        if cancel.is_set():
            raise Canceled()
        os.replace(part, dst)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    size = dst.stat().st_size
    elapsed = time.perf_counter() - t0
    log.info("Converted %s -> %s (%s) in %.1fs", src.name, dst.name, target.label, elapsed)
    return {"file_path": str(dst), "size": size, "format": target.label, "title": dst.name,
            "resolution": meta.pop("resolution", None), "duration": (info or {}).get("duration"),
            "meta": {**meta, "source": str(src), "target": target.id, "elapsed": round(elapsed, 2),
                     "source_size": src.stat().st_size}}


# ================================================================ audio / video
def _needs_encode(opts: dict) -> bool:
    return bool(opts.get("quality") not in (None, "", "auto") or opts.get("target_size_mb")
                or _res(opts) or _fps(opts))


def _res(opts: dict) -> int | None:
    try:
        v = int(str(opts.get("resolution") or "0").rstrip("p"))
        return v or None
    except ValueError:
        return None


def _fps(opts: dict) -> float | None:
    try:
        v = float(opts.get("fps") or 0)
        return v or None
    except ValueError:
        return None


def _scale_filter(info: dict, max_h: int | None) -> str | None:
    w, h = info.get("width") or 0, info.get("height") or 0
    if not max_h or not h:
        return None
    short = min(w, h) if w else h
    if short <= max_h:
        return None  # the converter never upscales (that is the AI upscaler's job)
    if w and w < h:  # portrait: limit the short side too
        return f"scale={max_h}:-2:flags=lanczos"
    return f"scale=-2:{max_h}:flags=lanczos"


def _audio_args(target: Target, opts: dict, info: dict, for_video: bool) -> list[str]:
    """Codec arguments for the output sound (copy when allowed)."""
    codec = target.acodec
    src_codecs = [acodec_family(a.get("codec")) for a in info.get("audio", [])]
    br = int(opts.get("audio_bitrate") or 0) or None
    filters = []
    if opts.get("normalize"):
        filters.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    extra: list[str] = []
    if opts.get("sample_rate") not in (None, "", "keep", 0):
        extra += ["-ar", str(int(opts["sample_rate"]))]
    if opts.get("channels") not in (None, "", "keep", 0):
        extra += ["-ac", str(int(opts["channels"]))]
    copy_ok = (not filters and not extra and not br and not opts.get("target_size_mb")
               and src_codecs and all(c == codec for c in src_codecs) and codec in ("aac", "opus", "mp3", "flac", "vorbis"))
    if copy_ok:
        return ["-c:a", "copy"]
    args: list[str] = []
    if codec == "mp3":
        args = ["-c:a", "libmp3lame", "-b:a", f"{br or (DEFAULT_ABR['mp3-in-avi'] if for_video else DEFAULT_ABR['mp3'])}k"]
    elif codec == "aac":
        args = ["-c:a", "aac", "-b:a", f"{br or DEFAULT_ABR['aac']}k"]
    elif codec == "opus":
        args = ["-c:a", "libopus", "-b:a", f"{br or DEFAULT_ABR['opus']}k", "-vbr", "on"]
    elif codec == "vorbis":
        args = ["-c:a", "libvorbis", "-b:a", f"{br or DEFAULT_ABR['vorbis']}k"]
    elif codec == "wma":
        args = ["-c:a", "wmav2", "-b:a", f"{br or DEFAULT_ABR['wma']}k"]
    elif codec == "flac":
        args = ["-c:a", "flac", "-compression_level", "8"]
    elif codec == "alac":
        args = ["-c:a", "alac"]
    elif codec == "pcm":
        args = ["-c:a", "pcm_s24le" if str(opts.get("bit_depth")) == "24" else "pcm_s16le"]
    elif codec == "pcm_be":
        args = ["-c:a", "pcm_s24be" if str(opts.get("bit_depth")) == "24" else "pcm_s16be"]
    elif codec == "pcm_s16le":
        args = ["-c:a", "pcm_s16le"]
    if filters:
        args += ["-af", ",".join(filters)]
    return args + extra


def _video_bitrate_args(encoder: str, kbps: int) -> list[str]:
    rate = [f"-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.5)}k", "-bufsize", f"{kbps * 2}k"]
    if encoder.endswith("_nvenc"):
        return ["-c:v", encoder, "-preset", "p5", "-rc", "vbr", *rate, "-pix_fmt", "yuv420p"]
    if encoder.endswith("_qsv"):
        return ["-c:v", encoder, "-preset", "medium", *rate, "-pix_fmt", "nv12"]
    if encoder.endswith("_amf"):
        return ["-c:v", encoder, "-rc", "vbr_peak", *rate, "-pix_fmt", "yuv420p"]
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", "medium", *rate, "-pix_fmt", "yuv420p"]
    if encoder == "libx265":
        return ["-c:v", "libx265", "-preset", "medium", "-tag:v", "hvc1", *rate, "-pix_fmt", "yuv420p"]
    if encoder == "libvpx-vp9":
        return ["-c:v", "libvpx-vp9", "-row-mt", "1", "-cpu-used", "2", *rate, "-pix_fmt", "yuv420p"]
    return ["-c:v", encoder, *rate, "-pix_fmt", "yuv420p"]


def _encoder_for(codec: str, use_hw: bool) -> str:
    if codec == "vp9":
        return "libvpx-vp9"
    if codec not in ("h264", "h265", "av1"):
        return codec
    if use_hw:
        enc = ffmpeg.best_encoder(codec)  # hardware first, CPU otherwise
        if enc:
            return enc
    available = ffmpeg.working_encoders()
    for enc in {"h264": ("libx264",), "h265": ("libx265",), "av1": ("libsvtav1", "libaom-av1")}[codec]:
        if enc in available:
            return enc
    raise MediaLabError("encoder_missing", f"No {codec.upper()} encoder is available in FFmpeg.",
                        "Reinstall FFmpeg from Settings › Components.")


def _convert_av(src: Path, dst: Path, target: Target, opts: dict, info: dict, cancel: threading.Event,
                progress: Progress) -> dict:
    duration = info.get("duration") or 0
    label = f"Converting to {target.label}"
    if target.kind == "video":
        if not info.get("has_video"):
            raise MediaLabError("no_video_stream", "This file has no picture to convert to video.",
                                "Choose an audio format instead.")
        args, meta = _video_args(src, target, opts, info)
    else:
        if not info.get("has_audio"):
            raise MediaLabError("no_audio_stream", "This file contains no sound.",
                                "Choose a video or image format instead.")
        args, meta = _audio_only_args(src, target, opts, info)
    size_goal = float(opts.get("target_size_mb") or 0)
    for attempt in range(3):
        try:
            _run(args + [str(dst)], duration, cancel, progress,
                 label if not meta.get("remux") else "Changing container (lossless)")
        except MediaLabError as e:
            if e.code != "encoder_failed" or not meta.get("gpu"):
                raise
            # Hardware encoders refuse some inputs (tiny frames, odd formats): redo on the CPU.
            log.warning("GPU encoder failed (%s), converting with the CPU encoder", meta.get("encoder"))
            opts = {**opts, "hw": False}
            args, meta = (_video_args if target.kind == "video" else _audio_only_args)(src, target, opts, info)
            _run(args + [str(dst)], duration, cancel, progress, label)
        if not size_goal or dst.stat().st_size <= size_goal * 1_000_000 or attempt == 2:
            break
        # Over budget (rare with VBR): aim lower and redo.
        ratio = (size_goal * 1_000_000) / dst.stat().st_size * 0.95
        log.info("Target size missed (%.1f MB), retrying with %.0f%% bitrate", dst.stat().st_size / 1e6, ratio * 100)
        args, meta = (_video_args if target.kind == "video" else _audio_only_args)(src, target, {**opts, "_bitrate_scale": (opts.get("_bitrate_scale") or 1) * ratio}, info)
        opts = {**opts, "_bitrate_scale": (opts.get("_bitrate_scale") or 1) * ratio}
    return meta


def _run(args: list[str], duration: float, cancel: threading.Event, progress: Progress, label: str) -> None:
    def on_progress(frac: float, state: dict) -> None:
        eta = None
        try:
            speed = float(str(state.get("speed", "0")).strip().rstrip("x") or 0)
            done = int(state.get("out_time_us") or 0) / 1e6
            if speed > 0 and 0 < frac < 1:
                eta = (done / frac - done) / speed
        except ValueError:
            pass
        progress(frac, label, eta)

    progress(0.0, label, None)
    ffmpeg.run(args, duration=duration or None, cancel=cancel, label="convert", on_progress=on_progress)


def _video_args(src: Path, target: Target, opts: dict, info: dict) -> tuple[list[str], dict]:
    args = ["-i", str(src)]
    meta: dict[str, Any] = {}
    w, h = info.get("width"), info.get("height")
    if target.id == "gif":
        fps = _fps(opts) or 15
        width = int(opts.get("gif_width") or 480)
        width = min(width, w or width)
        chain = (f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];"
                 f"[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
        args += ["-filter_complex", f"[0:V:0]{chain}", "-an", "-loop", "0", "-f", "gif"]
        meta["resolution"] = f"{width}×{round(width * h / w) if w and h else '?'}"
        return args, meta

    src_v = vcodec_family(info.get("vcodec"))
    src_a = [acodec_family(a.get("codec")) for a in info.get("audio", [])]
    remove_audio = bool(opts.get("remove_audio"))
    # Lossless container change when the codecs already fit.
    fits = {
        "mp4": src_v in ("h264", "h265", "av1", "vp9") and all(a in ("aac", "mp3", "opus", "flac", "ac3", "eac3") for a in src_a),
        "mov": src_v in ("h264", "h265") and all(a in ("aac", "mp3", "ac3", "eac3") for a in src_a),
        "mkv": True,
        "webm": src_v in ("vp9", "vp8", "av1") and all(a in ("opus", "vorbis") for a in src_a),
    }
    container = target.ext
    same_family = target.id in ("mp4", "mov", "webm", "mkv") and (target.id == "mkv" or src_v == target.vcodec or
                                                                     (target.id in ("mp4",) and src_v in ("h264",)))
    if not _needs_encode(opts) and fits.get(container) and same_family and not opts.get("normalize"):
        args += ["-map", "0:V?", "-map", "0:a?"] if not remove_audio else ["-map", "0:V?"]
        if container == "mkv":
            args += ["-map", "0:s?"]
        args += ["-c", "copy", "-map_metadata", "0"]
        if container in ("mp4", "mov"):
            args += ["-movflags", "+faststart"]
            if src_v == "h265":
                args += ["-tag:v", "hvc1"]
        if remove_audio:
            args += ["-an"]
        meta.update(remux=True, resolution=f"{w}×{h}" if w else None, vcodec=src_v)
        return args, meta

    codec = target.vcodec or "h264"
    if target.id == "mkv":
        codec = "h264"
    use_hw = opts.get("hw", True) is not False
    quality = opts.get("quality") if opts.get("quality") in ("high", "normal", "small") else "high"
    chain = []
    if info.get("is_hdr") and codec in ("h264", "mpeg4", "prores", "vp9"):
        from ..upscale.filters import TONEMAP
        chain.append(TONEMAP)
    scale = _scale_filter(info, _res(opts))
    if scale:
        chain.append(scale)
    if _fps(opts):
        chain.append(f"fps={_fps(opts)}")
    if not scale and ((w or 0) % 2 or (h or 0) % 2) and codec not in ("prores",):
        chain.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")  # 4:2:0 encoders need even dimensions
    args += ["-map", "0:V:0"]
    if not remove_audio:
        args += ["-map", "0:a?"]
    if container == "mkv":
        args += ["-map", "0:s?", "-c:s", "copy"]
    if codec == "prores":
        args += ["-c:v", "prores_ks", "-profile:v", "3", "-vendor", "apl0", "-pix_fmt", "yuv422p10le"]
        meta["vcodec"] = "prores"
    elif codec == "mpeg4":
        args += ["-c:v", "mpeg4", "-q:v", {"high": "2", "normal": "4", "small": "7"}[quality], "-tag:v", "XVID",
                 "-pix_fmt", "yuv420p"]
        meta["vcodec"] = "mpeg4"
    else:
        # NVENC/QSV/AMF refuse very small frames (stickers, tiny GIFs): use the CPU for those.
        tiny = min(w or 0, h or 0) < 160
        enc = _encoder_for(codec, use_hw and not tiny)
        size_goal = float(opts.get("target_size_mb") or 0)
        if size_goal and info.get("duration"):
            a_kbps = 0 if remove_audio or not info.get("has_audio") else int(opts.get("audio_bitrate") or 128) * max(1, len(src_a))
            total_kbps = size_goal * 8000 / info["duration"]  # MB = 1,000,000 bytes (the strictest limit)
            v_kbps = int(max(80, (total_kbps - a_kbps) * 0.94 * float(opts.get("_bitrate_scale") or 1)))
            if total_kbps - a_kbps < 80:
                raise MediaLabError("target_too_small", f"{size_goal:g} MB is too small for this video.",
                                    f"Choose at least {int((a_kbps + 150) * info['duration'] / 8000) + 1} MB, or a lower resolution.")
            args += _video_bitrate_args(enc, v_kbps)
            meta["video_kbps"] = v_kbps
        else:
            args += ffmpeg.encoder_args(enc, CRF.get(codec, CRF["h264"])[quality], fast=not ffmpeg.is_hw(enc) and quality != "high")
        if codec == "h265" and container in ("mp4", "mov") and "-tag:v" not in args:
            args += ["-tag:v", "hvc1"]
        meta.update(vcodec=codec, encoder=enc, gpu=ffmpeg.is_hw(enc))
    if chain:
        args += ["-vf", ",".join(chain)]
    if remove_audio:
        args += ["-an"]
    elif info.get("has_audio"):
        a_opts = dict(opts)
        if opts.get("target_size_mb") and not opts.get("audio_bitrate"):
            a_opts["audio_bitrate"] = 128
        args += _audio_args(target, a_opts, info, for_video=True)
    args += ["-map_metadata", "0"]
    if container in ("mp4", "mov"):
        args += ["-movflags", "+faststart"]
    out_h = _res(opts) if scale else h
    out_w = (round(w * out_h / h / 2) * 2 if w and h and out_h else w)
    if scale and w and h and w < h:
        out_w, out_h = _res(opts), round(h * _res(opts) / w / 2) * 2
    meta["resolution"] = f"{out_w}×{out_h}" if out_w and out_h else None
    return args, meta


def _audio_only_args(src: Path, target: Target, opts: dict, info: dict) -> tuple[list[str], dict]:
    args = ["-i", str(src), "-map", "0:a:0"]
    cover = info.get("cover_index")
    keep_cover = cover is not None and target.id in ("mp3", "m4a", "flac", "alac") and not opts.get("strip_metadata")
    if keep_cover:
        args += ["-map", f"0:{cover}", "-c:v", "copy", "-disposition:v:0", "attached_pic"]
    else:
        args += ["-vn"]
    a_opts = dict(opts)
    if opts.get("target_size_mb") and info.get("duration") and target.acodec in ("mp3", "aac", "opus", "vorbis", "wma"):
        kbps = int(float(opts["target_size_mb"]) * 8000 / info["duration"] * 0.96 * float(opts.get("_bitrate_scale") or 1))
        if kbps < 24:
            raise MediaLabError("target_too_small", f"{opts['target_size_mb']} MB is too small for this recording.",
                                f"Choose at least {int(24 * info['duration'] / 8000) + 1} MB.")
        a_opts["audio_bitrate"] = min(320, kbps)
    codec_args = _audio_args(target, a_opts, info, for_video=False)
    args += codec_args
    if target.id == "mp3":
        args += ["-id3v2_version", "3"]
    if not opts.get("strip_metadata"):
        args += ["-map_metadata", "0"]
    else:
        args += ["-map_metadata", "-1"]
    if target.ext == "m4a":
        args += ["-movflags", "+faststart"]
    return args, {"remux": codec_args == ["-c:a", "copy"], "acodec": target.acodec}


# ================================================================ subtitles
def _convert_subtitle(src: Path, dst: Path, target: Target, info: dict | None, cancel: threading.Event,
                      progress: Progress) -> dict:
    args = ["-i", str(src)]
    if ffmpeg.file_kind(src) == "video":
        subs = (info or {}).get("subtitles") or []
        text = [s for s in subs if s.get("codec") in ("subrip", "ass", "ssa", "webvtt", "mov_text", "text")]
        if not text:
            raise MediaLabError("no_subtitles", "This video has no text subtitles to extract.",
                                "Picture-based subtitles (DVD/Blu-ray) cannot be converted to text.")
        args += ["-map", f"0:{text[0]['index']}"]
    progress(0.1, f"Converting to {target.label}", None)
    ffmpeg.run(args + ["-f", {"srt": "srt", "vtt": "webvtt", "ass": "ass"}[target.id], str(dst)],
               cancel=cancel, label="subtitles")
    return {}


# ================================================================ images
def _convert_image(src: Path, dst: Path, target: Target, opts: dict, progress: Progress) -> dict:
    from PIL import Image

    progress(0.1, f"Converting to {target.label}", None)
    q = int(opts.get("image_quality") or 90)
    q = max(1, min(100, q))
    max_side = int(opts.get("max_size") or 0)
    strip = bool(opts.get("strip_metadata"))
    with open_image(src) as im:
        icc = None if strip else im.info.get("icc_profile")
        exif = None if strip else im.info.get("exif")
        frames = getattr(im, "n_frames", 1)
        if frames > 1 and target.id in ("gif-image", "webp"):
            out = _save_animation(im, dst, target, q, max_side)
            return {"resolution": out, "frames": frames}
        im.seek(0)
        img = upright(im)
        img.load()
    if max_side and max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    kw: dict[str, Any] = {}
    if icc:
        kw["icc_profile"] = icc
    tid = target.id
    if tid == "jpg":
        if exif:
            kw["exif"] = _reset_orientation(exif)
        flatten(img).save(dst, "JPEG", quality=q, optimize=True, progressive=True,
                          subsampling=0 if q >= 90 else 2, **kw)
    elif tid == "png":
        img = img if img.mode in ("RGB", "RGBA", "L", "LA", "P", "I;16") else img.convert("RGBA")
        img.save(dst, "PNG", optimize=True, **kw)
    elif tid == "webp":
        img = img.convert("RGBA" if _has_alpha(img) else "RGB")
        img.save(dst, "WEBP", quality=q, method=5, lossless=q >= 100, **kw)
    elif tid == "avif":
        img = img.convert("RGBA" if _has_alpha(img) else "RGB")
        img.save(dst, "AVIF", quality=q, speed=6, **kw)
    elif tid == "heic":
        try:
            import pillow_heif  # noqa: F401
        except Exception as e:
            raise MediaLabError("heic_unavailable", "HEIC export is not available in this build.") from e
        img = img.convert("RGBA" if _has_alpha(img) else "RGB")
        img.save(dst, "HEIF", quality=q)
    elif tid == "bmp":
        flatten(img).save(dst, "BMP")
    elif tid == "tiff":
        img = img.convert("RGBA" if _has_alpha(img) else "RGB")
        img.save(dst, "TIFF", compression="tiff_lzw", **kw)
    elif tid == "gif-image":
        base = img.convert("RGBA")
        alpha = base.getchannel("A")
        pal = base.convert("RGB").quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG)
        if alpha.getextrema()[0] < 255:  # keep transparency on index 255
            mask = alpha.point(lambda a: 255 if a < 128 else 0)
            pal.paste(255, mask=mask)
            pal.save(dst, "GIF", transparency=255)
        else:
            pal.save(dst, "GIF")
    elif tid == "ico":
        side = max(img.size)
        square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        square.paste(img.convert("RGBA"), ((side - img.width) // 2, (side - img.height) // 2))
        sizes = [(s, s) for s in (16, 24, 32, 48, 64, 128, 256) if s <= max(side, 16)]
        square.save(dst, "ICO", sizes=sizes)
    elif tid == "pdf":
        flatten(img).save(dst, "PDF", resolution=float(opts.get("dpi") or 150))
    else:
        raise MediaLabError("unknown_target", f"Unsupported image format {target.label}.")
    progress(1.0, "Done", None)
    return {"resolution": f"{img.width}×{img.height}"}


def _has_alpha(img) -> bool:
    return img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info)


def _reset_orientation(exif: bytes) -> bytes:
    """Pixels were rotated upright: orientation tag must say "normal" or viewers rotate twice."""
    try:
        from PIL import Image
        e = Image.Exif()
        e.load(exif)
        e[0x0112] = 1
        return e.tobytes()
    except Exception:
        return exif


def _save_animation(im, dst: Path, target: Target, q: int, max_side: int) -> str:
    from PIL import Image, ImageSequence

    frames, durations = [], []
    for fr in ImageSequence.Iterator(im):
        f = fr.convert("RGBA")
        if max_side and max(f.size) > max_side:
            f.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        frames.append(f)
        durations.append(fr.info.get("duration", im.info.get("duration", 80)))
    loop = im.info.get("loop", 0)
    if target.id == "webp":
        frames[0].save(dst, "WEBP", save_all=True, append_images=frames[1:], duration=durations, loop=loop,
                       quality=q, method=4)
    else:
        frames[0].save(dst, "GIF", save_all=True, append_images=frames[1:], duration=durations, loop=loop,
                       disposal=2, optimize=False)
    return f"{frames[0].width}×{frames[0].height}"
