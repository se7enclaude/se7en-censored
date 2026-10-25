"""Background removal (IS-Net, DirectML GPU / CPU).

The model predicts a 1024x1024 foreground mask that is resized to the image
with Lanczos (smooth edges). Output options: transparent PNG, a solid color, a
blurred version of the original background, or the mask itself.
"""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter

from .. import ai_assets
from ..errors import MediaLabError
from . import onnx_rt

SIZE = 1024


def available() -> tuple[bool, str]:
    if not ai_assets.path_of("isnet_general"):
        return False, "The background model is not installed (Settings › Components › AI modules)."
    return onnx_rt.available()


def mask(rgb: np.ndarray, device: str = "auto") -> Image.Image:
    """Foreground mask (L mode, 0 = background, 255 = subject) at the image size."""
    path = ai_assets.require("isnet_general")
    h, w = rgb.shape[:2]
    x = np.asarray(Image.fromarray(rgb).resize((SIZE, SIZE), Image.Resampling.BICUBIC)).astype(np.float32) / 255.0
    x = x - 0.5  # IS-Net normalisation: mean 0.5, std 1.0
    out = onnx_rt.run(path, {"input": np.ascontiguousarray(x.transpose(2, 0, 1)[None])}, device)[0][0, 0]
    lo, hi = float(out.min()), float(out.max())
    pred = (out - lo) / (hi - lo) if hi - lo > 1e-6 else np.zeros_like(out)
    m = Image.fromarray((pred * 255).astype(np.uint8)).resize((w, h), Image.Resampling.LANCZOS)
    return m


def remove(img: Image.Image, background: str = "transparent", color: str = "#ffffff", feather: int = 0,
           device: str = "auto") -> Image.Image:
    """``background``: transparent | color | blur | mask."""
    ok, why = available()
    if not ok:
        raise MediaLabError("background_unavailable", "Background removal is not available.", why,
                            extra={"component": "aipack"})
    rgb_img = img.convert("RGB")
    m = mask(np.asarray(rgb_img), device)
    if img.mode == "RGBA":  # keep areas that were already transparent
        m = Image.fromarray(np.minimum(np.asarray(m), np.asarray(img.getchannel("A"))))
    if feather:
        m = m.filter(ImageFilter.GaussianBlur(feather))
    if background == "mask":
        return m
    if background == "color":
        bg = Image.new("RGB", rgb_img.size, _hex(color))
        return Image.composite(rgb_img, bg, m)
    if background == "blur":
        radius = max(8, round(max(rgb_img.size) / 60))
        bg = rgb_img.filter(ImageFilter.GaussianBlur(radius))
        return Image.composite(rgb_img, bg, m)
    out = rgb_img.copy()
    out.putalpha(m)
    return out


def _hex(color: str) -> tuple[int, int, int]:
    c = (color or "#ffffff").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except (ValueError, IndexError):
        return 255, 255, 255
