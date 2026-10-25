"""Upscale engine: backends, capabilities, image pipeline and previews."""
from __future__ import annotations

import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageOps

from .. import components
from ..config import paths
from ..errors import Canceled, MediaLabError
from ..hardware import monitor
from ..logging_setup import get_logger
from ..media import ffmpeg
from ..settings import Settings
from . import background, colorize, face, filters, interpolate, onnx_rt
from .backends.base import BackendParams, UpscaleBackend
from .backends.classic import ClassicBackend
from .backends.cugan import CuganBackend
from .backends.ncnn import NcnnBackend
from .backends.onnx_backend import OnnxBackend
from .backends.torch_backend import TorchBackend
from .models import ModelInfo, categories_summary, get_model, list_models
from .planning import describe, ncnn_tile_for_vram, plan, reduce_tile, target_size

log = get_logger("process")
Image.MAX_IMAGE_PIXELS = 400_000_000  # allow large upscales, still guard against decompression bombs

BACKENDS: dict[str, UpscaleBackend] = {"ncnn": NcnnBackend(), "cugan": CuganBackend(), "onnx": OnnxBackend(),
                                       "classic": ClassicBackend(), "torch": TorchBackend()}


def register_backend(backend: UpscaleBackend) -> None:
    """Extension point for plugins (TensorRT, DirectML, a VSR engine...)."""
    BACKENDS[backend.id] = backend


# ---------------------------------------------------------------- capabilities
def capabilities() -> dict[str, Any]:
    backends = []
    for b in BACKENDS.values():
        try:
            ok, info = b.available()
        except Exception as e:  # never break the UI because a backend probe failed
            ok, info = False, str(e)
        backends.append({"id": b.id, "name": b.name, "available": ok, "info": info})
    avail = {b["id"]: b["available"] for b in backends}
    models = []
    for m in list_models():
        d = m.to_dict()
        d["usable"] = m.installed and avail.get(m.backend, False)
        models.append(d)
    face_ok, face_info = face.available()
    bg_ok, bg_info = background.available()
    col_ok, col_info = colorize.available()
    rife_ok, rife_info = interpolate.available()
    has_ffmpeg = bool(components.find_binary("ffmpeg"))
    return {
        "backends": backends,
        "models": models,
        "categories": categories_summary(),
        "vulkan_devices": BACKENDS["ncnn"].devices() if avail.get("ncnn") else [],
        "enhancements": {
            "denoise": {"available": True},
            "sharpen": {"available": True},
            "detail": {"available": True, "note": "Adds fine grain to counter over-smoothing; does not invent detail."},
            "color": {"available": True, "note": "Gentle saturation/contrast restore (heuristic, not AI)."},
            "face": {"available": face_ok, "note": face_info, "models": face.models()},
            "background": {"available": bg_ok, "note": bg_info},
            "colorize": {"available": col_ok, "note": col_info},
            "interpolate": {"available": rife_ok and has_ffmpeg, "note": rife_info},
            "deblur": {"available": False, "note": "No deblur model is installed. Add a deblur model to the PyTorch backend."},
            "hdr": {"available": has_ffmpeg, "note": "HDR sources are tone-mapped to SDR before AI (models work on SDR)."},
            "temporal": {"available": has_ffmpeg, "note": "Temporal smoothing + deflicker to reduce frame-to-frame shimmer."},
        },
        "video": {"available": has_ffmpeg, "encoders": sorted(ffmpeg.working_encoders()) if has_ffmpeg else []},
        "onnx": {"available": onnx_rt.available()[0], "info": onnx_rt.available()[1]},
    }


def resolve_model(model_id: str) -> tuple[ModelInfo, UpscaleBackend]:
    model = get_model(model_id)
    if not model:
        raise MediaLabError("model_missing", f"Unknown model “{model_id}”.", "Choose another model.")
    if not model.installed:
        comp = {"onnx": "aipack", "cugan": "realcugan"}.get(model.backend, "realesrgan")
        raise MediaLabError("model_missing", f"The model “{model.name}” is not installed.",
                            "Install it from Settings › Components (one click).", extra={"component": comp})
    backend = BACKENDS.get(model.backend)
    if not backend:
        raise MediaLabError("backend_missing", f"Backend {model.backend} is not available.")
    ok, why = backend.available()
    if not ok:
        raise MediaLabError("backend_unavailable", f"{backend.name} cannot run on this machine.", why)
    return model, backend


def resolve_params(settings: Settings, model: ModelInfo, strategy: dict | None = None) -> BackendParams:
    ai = settings.ai
    tile = ai.tile_size
    if not tile and model.backend in ("ncnn",):
        free = monitor().primary_free_vram_mb()
        heavy = "animevideo" not in (model.ncnn_name or "") and "anime" not in (model.ncnn_name or "")
        tile = ncnn_tile_for_vram(free, limit_mb=ai.vram_limit_mb, heavy=heavy)
    level = int((strategy or {}).get("reduce_tile") or 0)
    if level:
        tile = reduce_tile(tile or 256, level)
    return BackendParams(tile=tile, gpu=ai.gpu_id, threads=ai.threads, vram_limit_mb=ai.vram_limit_mb)


def with_oom_retry(fn: Callable[[BackendParams], None], params: BackendParams, label: str) -> BackendParams:
    """Run ``fn``; on VRAM exhaustion halve the tile (up to 4 times)."""
    for level in range(5):
        try:
            fn(params)
            return params
        except MediaLabError as e:
            if e.code not in ("gpu_out_of_memory", "upscaler_crashed", "upscaler_incomplete", "upscaler_no_output") or level == 4:
                raise
            new_tile = reduce_tile(params.tile or 256, 1)
            if new_tile == params.tile and new_tile <= 32:
                raise
            log.warning("%s: %s — retrying with tile %d", label, e.message, new_tile)
            params = BackendParams(**{**params.__dict__, "tile": new_tile})
    return params


def native_scales(model: ModelInfo, backend: UpscaleBackend, params: BackendParams) -> tuple[int, ...]:
    if isinstance(backend, TorchBackend):
        return (backend.native_scale(model, params),)
    return model.scales


def output_size(w: int, h: int, opts: dict) -> tuple[int, int]:
    if opts.get("width") or opts.get("height"):
        return target_size(w, h, width=opts.get("width"), height=opts.get("height"),
                           keep_aspect=opts.get("keep_aspect", True))
    return target_size(w, h, scale=float(opts.get("scale") or 4))


# ---------------------------------------------------------------- images
def _normalize(src: Path, work: Path) -> tuple[Path, dict]:
    """Decode any image into an 8-bit RGB(A) PNG the backends accept."""
    with Image.open(src) as im:
        meta = {"icc_profile": im.info.get("icc_profile"), "format": im.format, "exif": im.info.get("exif")}
        im = ImageOps.exif_transpose(im)
        if getattr(im, "n_frames", 1) > 1:
            im.seek(0)
        has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
        im = im.convert("RGBA" if has_alpha else "RGB")
        out = work / "input.png"
        im.save(out, compress_level=1)
        meta["size"] = im.size
    return out, meta


def wants_upscale(opts: dict) -> bool:
    """False for "AI tools only" jobs (faces, colorize, background, interpolation at the same size)."""
    if opts.get("width") or opts.get("height"):
        return True
    try:
        return float(opts.get("scale") or 4) > 1.0001
    except (TypeError, ValueError):
        return True


def ai_operations(opts: dict) -> list[str]:
    ops = []
    if opts.get("face"):
        ops.append("face")
    if opts.get("colorize"):
        ops.append("colorize")
    if opts.get("remove_bg"):
        ops.append("background")
    return ops


def apply_operations(im: Image.Image, opts: dict, on_progress: Callable[[float, str], None] | None = None,
                     base: float = 0.9, span: float = 0.05) -> Image.Image:
    """Faces -> colors -> background, in that order (restored B&W faces are then colorized)."""
    import numpy as np
    ops = ai_operations(opts)
    device = "cpu" if str(opts.get("gpu")) == "cpu" else "auto"
    for i, op in enumerate(ops):
        if on_progress:
            on_progress(base + span * i / max(1, len(ops)), {"face": "Enhancing faces", "colorize": "Colorizing",
                                                              "background": "Removing the background"}[op])
        alpha = im.getchannel("A") if im.mode == "RGBA" else None
        if op == "face":
            rgb = face.enhance(np.asarray(im.convert("RGB")), float(opts.get("face_strength", 0.8)),
                               str(opts.get("face_model") or "gfpgan"), float(opts.get("face_fidelity", 0.6)), device)
            im = Image.fromarray(rgb)
            if alpha is not None:
                im.putalpha(alpha)
        elif op == "colorize":
            im = colorize.colorize(im, int(opts.get("colorize_size") or 256), float(opts.get("colorize_saturation") or 1.0),
                                   device)
        elif op == "background":
            im = background.remove(im, str(opts.get("bg_mode") or "transparent"), str(opts.get("bg_color") or "#ffffff"),
                                   int(opts.get("bg_feather") or 0), device)
    return im


def process_image(src: Path, dst: Path, opts: dict, settings: Settings, cancel: threading.Event,
                  on_progress: Callable[[float, str], None], strategy: dict | None = None,
                  skip_denoise: bool = False) -> dict:
    upscale = wants_upscale(opts)
    if upscale:
        model, backend = resolve_model(opts.get("model") or settings.ai.default_model)
    else:
        model, backend = get_model("classic-lanczos"), BACKENDS["classic"]
    params = resolve_params(settings, model, strategy)
    work = paths().temp / "img" / uuid.uuid4().hex[:10]
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    try:
        on_progress(0.02, "Preparing")
        cur, meta = _normalize(src, work)
        w, h = meta["size"]
        out_w, out_h = output_size(w, h, opts) if upscale else (w, h)
        steps = plan(w, h, out_w, out_h, native_scales(model, backend, params)) if upscale else []
        log.info("Image %s: %dx%d → %dx%d via %s [%s, tile %s]", src.name, w, h, out_w, out_h,
                 describe(steps), model.name, params.tile or "auto")
        if not skip_denoise and filters.lvl(opts, "denoise"):
            on_progress(0.05, "Denoising")
            cur = filters.image_denoise(cur, work / "denoised.png", filters.lvl(opts, "denoise"))
        ai_steps = [s for s in steps if s.kind == "ai"]
        for i, step in enumerate(steps):
            if cancel.is_set():
                raise Canceled()
            nxt = work / f"step{i}.png"
            if step.kind == "ai":
                k = ai_steps.index(step)
                base, span = 0.08 + 0.8 * k / len(ai_steps), 0.8 / len(ai_steps)
                label = f"Upscaling ×{step.scale:g} with {model.name}"
                on_progress(base, label)
                src_i = cur

                def run(p: BackendParams, src_i=src_i, nxt=nxt, step=step, base=base, span=span, label=label):
                    backend.upscale_file(model, src_i, nxt, int(step.scale), p, cancel,
                                         lambda f: on_progress(base + span * f, label))

                params = with_oom_retry(run, params, "image upscale")
            else:
                on_progress(0.9, "Resizing to target resolution")
                with Image.open(cur) as im:
                    im.load()
                    im.resize((step.width, step.height), Image.Resampling.LANCZOS).save(nxt, compress_level=1)
            cur = nxt
        with Image.open(cur) as im:
            im.load()
            result = apply_operations(im, opts, on_progress, 0.88 if upscale else 0.1, 0.07 if upscale else 0.8)
            on_progress(0.96, "Finishing")
            result = filters.image_post(result, opts)
            fmt = dst.suffix.lstrip(".").lower()
            if fmt not in ("png", "jpg", "jpeg", "webp"):
                fmt = opts.get("image_format") or settings.ai.image_format
            _save(result, dst, fmt, meta)
        elapsed = time.perf_counter() - t0
        log.info("Image done in %.1fs → %s", elapsed, dst)
        on_progress(1.0, "Done")
        return {"width": out_w, "height": out_h, "source_width": w, "source_height": h,
                "plan": describe(steps) or " + ".join(ai_operations(opts)), "steps": [s.to_dict() for s in steps],
                "elapsed": elapsed, "operations": ai_operations(opts),
                "tile": params.tile}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _save(im: Image.Image, dst: Path, fmt: str, meta: dict) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    kw: dict[str, Any] = {}
    if meta.get("icc_profile"):
        kw["icc_profile"] = meta["icc_profile"]
    fmt = (fmt or "png").lower()
    if fmt in ("jpg", "jpeg"):
        im.convert("RGB").save(dst, "JPEG", quality=95, subsampling=0, **kw)
    elif fmt == "webp":
        im.save(dst, "WEBP", quality=95, method=4, **kw)
    else:
        im.save(dst, "PNG", compress_level=6, **kw)


def output_path_for(src: Path, opts: dict, settings: Settings, ext: str) -> Path:
    folder = Path(opts.get("output_dir") or settings.ai.output_folder or src.parent)
    folder.mkdir(parents=True, exist_ok=True)
    suffix = opts.get("suffix", settings.ai.output_suffix)
    base = folder / f"{src.stem}{suffix}"
    cand = base.with_suffix(f".{ext}")
    n = 1
    while cand.exists() or cand.resolve() == src.resolve():
        cand = base.with_name(f"{base.name} ({n})").with_suffix(f".{ext}")
        n += 1
    return cand


# ---------------------------------------------------------------- previews
def preview(src: Path, opts: dict, settings: Settings, *, region: dict | None = None,
            at: float | None = None, cancel: threading.Event | None = None) -> dict:
    cancel = cancel or threading.Event()
    pid = uuid.uuid4().hex[:12]
    pdir = paths().cache / "previews" / pid
    pdir.mkdir(parents=True, exist_ok=True)
    _prune_previews()
    kind = ffmpeg.media_kind(src)
    frame = src
    skip_denoise = False
    info: dict[str, Any] = {}
    if kind == "video":
        info = ffmpeg.probe(src)
        t = at if at is not None else min(max(1.0, (info.get("duration") or 0) * 0.1), (info.get("duration") or 1) - 0.1)
        frame = pdir / "frame.png"
        chain = filters.video_pre_chain(opts, bool(info.get("is_hdr")))
        ffmpeg.run(["-ss", f"{max(0, t):.3f}", "-i", str(src), "-frames:v", "1", "-an", "-vf", ",".join(chain),
                    str(frame)], label="preview frame")
        skip_denoise = True
        info["at"] = t
    whole = bool(opts.get("remove_bg") or opts.get("colorize"))  # need the whole picture to be meaningful
    with Image.open(frame) as im:
        im = ImageOps.exif_transpose(im)
        w, h = im.size
        if whole:
            x0, y0, x1, y1 = 0, 0, w, h
        elif region:
            x0, y0 = int(region["x"] * w), int(region["y"] * h)
            x1, y1 = int((region["x"] + region["w"]) * w), int((region["y"] + region["h"]) * h)
        elif w * h > 1_000_000:
            side = 512
            x0, y0 = max(0, w // 2 - side // 2), max(0, h // 2 - side // 2)
            x1, y1 = min(w, x0 + side), min(h, y0 + side)
        else:
            x0, y0, x1, y1 = 0, 0, w, h
        crop = im.crop((x0, y0, x1, y1))
        if whole and max(crop.size) > 1024:
            crop.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        crop_path = pdir / "crop.png"
        crop.save(crop_path)
    out_w, out_h = output_size(w, h, opts) if wants_upscale(opts) else (w, h)
    factor = out_w / w
    if whole and crop.size != (x1 - x0, y1 - y0):
        factor = 1.0  # preview of the tools at screen size; the render keeps the full resolution
    crop_opts = {**opts, "width": None, "height": None, "scale": factor}
    result_path = pdir / "result.png"
    t0 = time.perf_counter()
    res = process_image(crop_path, result_path, {**crop_opts, "image_format": "png"}, settings, cancel,
                        lambda f, s: None, skip_denoise=skip_denoise)
    elapsed = time.perf_counter() - t0
    with Image.open(result_path) as r, Image.open(crop_path) as c:
        c.resize(r.size, Image.Resampling.LANCZOS).save(pdir / "original.png")
        rw, rh = r.size
    # Rough full-render estimate from the crop speed.
    crop_px = max(1, (x1 - x0) * (y1 - y0))
    est_frame = elapsed * (w * h) / crop_px
    frames = info.get("frames") or 1
    return {
        "id": pid, "original": f"/api/preview/{pid}/original.png", "result": f"/api/preview/{pid}/result.png",
        "width": rw, "height": rh, "region": {"x": x0 / w, "y": y0 / h, "w": (x1 - x0) / w, "h": (y1 - y0) / h},
        "source": {"width": w, "height": h}, "output": {"width": out_w, "height": out_h},
        "plan": res["plan"], "elapsed": elapsed, "estimate_seconds": est_frame * frames,
        "kind": kind, "at": info.get("at"),
    }


def _prune_previews(keep: int = 20) -> None:
    root = paths().cache / "previews"
    dirs = sorted((d for d in root.glob("*") if d.is_dir()), key=lambda d: d.stat().st_mtime, reverse=True)
    for d in dirs[keep:]:
        shutil.rmtree(d, ignore_errors=True)

