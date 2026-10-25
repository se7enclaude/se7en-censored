"""Optional PyTorch backend (CUDA / CPU) through spandrel.

Loads any super-resolution architecture spandrel supports (ESRGAN/Real-ESRGAN,
SwinIR, HAT, DAT, OmniSR, SPAN, ...). Install with:

    pip install -r backend/requirements-torch.txt

Inference is tiled with overlap; tile size comes from free VRAM, FP16 is used
on CUDA when the architecture supports it, and CUDA out-of-memory errors are
caught and retried with smaller tiles instead of crashing.
"""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
from PIL import Image

from ...errors import Canceled, MediaLabError
from ...logging_setup import get_logger
from ..models import ModelInfo
from ..planning import torch_tile_for_vram
from .base import BackendParams, ProgressFn, UpscaleBackend

log = get_logger("gpu")


def _torch():
    try:
        import torch  # noqa: F401
        import spandrel  # noqa: F401
        return torch
    except Exception:
        return None


class TorchBackend(UpscaleBackend):
    id = "torch"
    name = "PyTorch (spandrel)"

    def __init__(self) -> None:
        self._cache: dict[str, tuple] = {}
        self._lock = threading.Lock()

    def available(self) -> tuple[bool, str]:
        torch = _torch()
        if torch is None:
            return False, "PyTorch backend not installed (see README › Optional PyTorch backend)."
        if torch.cuda.is_available():
            return True, f"CUDA {torch.version.cuda} · {torch.cuda.get_device_name(0)}"
        return True, "CPU only (slow)"

    def _device(self, params: BackendParams):
        torch = _torch()
        if params.gpu == "cpu" or not torch.cuda.is_available():
            return torch.device("cpu")
        idx = int(params.gpu) if str(params.gpu).isdigit() else 0
        return torch.device(f"cuda:{idx}")

    def _load(self, model: ModelInfo, params: BackendParams):
        torch = _torch()
        if torch is None:
            raise MediaLabError("backend_missing", "The PyTorch backend is not installed.",
                                "See README › Optional PyTorch backend.")
        device = self._device(params)
        key = f"{model.path}|{device}"
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            from spandrel import ModelLoader
            try:
                desc = ModelLoader(device=device).load_from_file(model.path)
            except Exception as e:
                raise MediaLabError("model_load_failed", f"Could not load “{model.name}”.",
                                    "The file may be corrupted or its architecture unsupported by spandrel.",
                                    False, str(e)) from e
            desc.model.eval()
            half = params.fp16 and device.type == "cuda" and getattr(desc, "supports_half", False)
            if half:
                desc.model.half()
            self._cache = {key: (desc, device, half)}  # keep one model resident
            log.info("Loaded %s (%s, x%s, fp16=%s) on %s", model.name, desc.architecture.name, desc.scale, half, device)
            return self._cache[key]

    def _infer(self, model: ModelInfo, rgb: np.ndarray, params: BackendParams, cancel: threading.Event,
               on_progress: ProgressFn | None) -> np.ndarray:
        torch = _torch()
        desc, device, half = self._load(model, params)
        scale = desc.scale
        h, w, _ = rgb.shape
        free = None
        if device.type == "cuda":
            free = torch.cuda.mem_get_info(device)[0] // (1 << 20)
        tile = params.tile or torch_tile_for_vram(free, limit_mb=params.vram_limit_mb, fp16=half)
        for _attempt in range(5):
            try:
                return self._tiled(desc, device, half, rgb, tile, scale, cancel, on_progress)
            except torch.cuda.OutOfMemoryError:
                torch.cuda.empty_cache()
                tile = max(64, tile // 2)
                log.warning("CUDA out of memory — retrying with tile %d", tile)
        raise MediaLabError("gpu_out_of_memory", "The GPU ran out of memory even with small tiles.",
                            "Lower the VRAM limit, close other GPU apps, or use a lighter model.", True)

    def _tiled(self, desc, device, half, rgb, tile, scale, cancel, on_progress):
        torch = _torch()
        h, w, _ = rgb.shape
        pad = 16
        out = np.zeros((h * scale, w * scale, 3), dtype=np.uint8)
        dtype = torch.float16 if half else torch.float32
        ys = list(range(0, h, tile))
        xs = list(range(0, w, tile))
        total = len(ys) * len(xs)
        n = 0
        with torch.inference_mode():
            for y in ys:
                for x in xs:
                    if cancel.is_set():
                        raise Canceled()
                    y0, x0 = max(0, y - pad), max(0, x - pad)
                    y1, x1 = min(h, y + tile + pad), min(w, x + tile + pad)
                    patch = torch.from_numpy(np.ascontiguousarray(rgb[y0:y1, x0:x1])).to(device)
                    t = patch.permute(2, 0, 1).unsqueeze(0).to(dtype) / 255.0
                    res = desc(t).clamp_(0, 1)
                    res = (res[0].permute(1, 2, 0).float() * 255.0).round().byte().cpu().numpy()
                    oy, ox = (y - y0) * scale, (x - x0) * scale
                    th, tw = min(tile, h - y) * scale, min(tile, w - x) * scale
                    out[y * scale:y * scale + th, x * scale:x * scale + tw] = res[oy:oy + th, ox:ox + tw]
                    n += 1
                    if on_progress:
                        on_progress(n / total)
        return out

    def upscale_file(self, model: ModelInfo, src: Path, dst: Path, scale: int, params: BackendParams,
                     cancel: threading.Event, on_progress: ProgressFn | None = None) -> None:
        with Image.open(src) as im:
            im.load()
            alpha = im.getchannel("A") if im.mode in ("RGBA", "LA") else None
            rgb = np.asarray(im.convert("RGB"))
        out = Image.fromarray(self._infer(model, rgb, params, cancel, on_progress))
        if alpha is not None:
            out.putalpha(alpha.resize(out.size, Image.Resampling.LANCZOS))
        out.save(dst)

    def upscale_dir(self, model: ModelInfo, in_dir: Path, out_dir: Path, scale: int, params: BackendParams,
                    cancel: threading.Event, on_progress: ProgressFn | None = None) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        files = sorted(in_dir.glob("*.png"))
        for i, f in enumerate(files):
            target = out_dir / f.name
            if target.exists():
                continue
            tmp = target.with_suffix(".tmp.png")
            self.upscale_file(model, f, tmp, scale, params, cancel)
            tmp.replace(target)
            if on_progress:
                on_progress((i + 1) / len(files))

    def native_scale(self, model: ModelInfo, params: BackendParams) -> int:
        desc, _, _ = self._load(model, params)
        return int(desc.scale)
