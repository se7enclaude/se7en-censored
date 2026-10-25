"""Super-resolution models in ONNX format (UltraSharp, Nomos, HAT, SPAN...) on DirectML.

Images are processed in overlapping tiles so any resolution fits in video
memory; the overlap is cropped away, which avoids visible seams. Models with a
fixed input size (HAT: 256x256) get padded edge tiles. The alpha channel, when
present, is resized with Lanczos (the models are RGB only).
"""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
from PIL import Image

from ... import ai_assets
from ...errors import Canceled, MediaLabError
from ...hardware import monitor
from ...logging_setup import get_logger
from .. import onnx_rt
from ..models import ModelInfo
from .base import BackendParams, ProgressFn, UpscaleBackend

log = get_logger("gpu")
PAD = 16


def default_tile(model: ModelInfo, free_vram_mb: int | None) -> int:
    light = "span" in model.id or "clear_reality" in model.id or "x2" in model.id
    if free_vram_mb is None:
        return 384 if light else 256
    budget = free_vram_mb - 1024
    for need, tile in ((10000, 512), (5000, 384), (2500, 256), (1200, 192), (0, 128)):
        if budget >= need:
            return int(tile * (1.5 if light else 1))
    return 128


class OnnxBackend(UpscaleBackend):
    id = "onnx"
    name = "ONNX (DirectML)"

    def available(self) -> tuple[bool, str]:
        return onnx_rt.available()

    def _path(self, model: ModelInfo) -> Path:
        return ai_assets.require(model.asset or "")

    def _fixed(self, path: Path, device: str) -> int | None:
        shape = onnx_rt.input_meta(path, device)[0].shape
        return shape[2] if isinstance(shape[2], int) else None

    def upscale_array(self, model: ModelInfo, rgb: np.ndarray, scale: int, params: BackendParams,
                      cancel: threading.Event, on_progress: ProgressFn | None = None) -> np.ndarray:
        path = self._path(model)
        device = "cpu" if params.gpu == "cpu" else "auto"
        name = onnx_rt.input_meta(path, device)[0].name
        fixed = self._fixed(path, device)
        h, w = rgb.shape[:2]
        tile = params.tile or default_tile(model, monitor().primary_free_vram_mb())
        if fixed:
            tile = fixed - 2 * PAD
        tile = max(64, tile)
        out = np.zeros((h * scale, w * scale, 3), dtype=np.uint8)
        ys = list(range(0, h, tile))
        xs = list(range(0, w, tile))
        total = len(ys) * len(xs)
        k = 0
        for y0 in ys:
            for x0 in xs:
                if cancel.is_set():
                    raise Canceled()
                y1, x1 = min(h, y0 + tile), min(w, x0 + tile)
                py0, px0 = max(0, y0 - PAD), max(0, x0 - PAD)
                py1, px1 = min(h, y1 + PAD), min(w, x1 + PAD)
                crop = rgb[py0:py1, px0:px1]
                ch, cw = crop.shape[:2]
                if fixed and (ch < fixed or cw < fixed):
                    crop = np.pad(crop, ((0, fixed - ch), (0, fixed - cw), (0, 0)), mode="reflect" if min(ch, cw) > 1 else "edge")
                res = onnx_rt.from_nchw(onnx_rt.run(path, {name: onnx_rt.to_nchw(crop)}, device)[0])
                res = res[: ch * scale, : cw * scale]
                oy, ox = (y0 - py0) * scale, (x0 - px0) * scale
                out[y0 * scale: y1 * scale, x0 * scale: x1 * scale] = res[oy: oy + (y1 - y0) * scale,
                                                                              ox: ox + (x1 - x0) * scale]
                k += 1
                if on_progress:
                    on_progress(k / total)
        return out

    def upscale_file(self, model: ModelInfo, src: Path, dst: Path, scale: int, params: BackendParams,
                     cancel: threading.Event, on_progress: ProgressFn | None = None) -> None:
        with Image.open(src) as im:
            im.load()
            alpha = im.getchannel("A") if im.mode == "RGBA" else None
            rgb = np.asarray(im.convert("RGB"))
        out = self.upscale_array(model, rgb, scale, params, cancel, on_progress)
        result = Image.fromarray(out)
        if alpha is not None:
            result.putalpha(alpha.resize(result.size, Image.Resampling.LANCZOS))
        fmt = {".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}.get(dst.suffix.lower(), "PNG")
        if fmt == "JPEG":
            result = result.convert("RGB")
        result.save(dst, fmt, **({"compress_level": 1} if fmt == "PNG" else {"quality": 95}))
        if not dst.exists():
            raise MediaLabError("upscaler_no_output", "The AI model produced no output.", "Retry.", True)

    def upscale_dir(self, model: ModelInfo, in_dir: Path, out_dir: Path, scale: int, params: BackendParams,
                    cancel: threading.Event, on_progress: ProgressFn | None = None) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        files = sorted(in_dir.glob("*.png"))
        for i, f in enumerate(files):
            if cancel.is_set():
                raise Canceled()
            target = out_dir / f.name
            if not target.exists():
                tmp = out_dir / (f.stem + ".partial")  # not *.png: never mistaken for a finished frame
                self.upscale_file(model, f, tmp, scale, params, cancel)
                tmp.replace(target)
            if on_progress:
                on_progress((i + 1) / len(files))
