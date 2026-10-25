"""Colorization of black & white photos and videos (DeOldify, DirectML GPU / CPU).

The model predicts colors at a reduced "render size"; only the color
(chroma) is kept and put back on the original full-resolution luminance, so
the output keeps every detail of the source. A larger render size gives more
stable, subtler colors; a smaller one more saturated colors.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

from .. import ai_assets
from ..errors import MediaLabError
from . import onnx_rt


def available() -> tuple[bool, str]:
    if not ai_assets.path_of("deoldify_artistic"):
        return False, "The colorization model is not installed (Settings › Components › AI modules)."
    return onnx_rt.available()


def colorize(img: Image.Image, render_size: int = 256, saturation: float = 1.0, device: str = "auto") -> Image.Image:
    ok, why = available()
    if not ok:
        raise MediaLabError("colorize_unavailable", "Colorization is not available.", why,
                            extra={"component": "aipack"})
    path = ai_assets.require("deoldify_artistic")
    size = max(128, min(1024, int(render_size) // 16 * 16))
    alpha = img.getchannel("A") if img.mode == "RGBA" else None
    gray = img.convert("L")
    x = np.asarray(gray.resize((size, size), Image.Resampling.BICUBIC).convert("RGB")).astype(np.float32)
    y = onnx_rt.run(path, {"input": np.ascontiguousarray(x.transpose(2, 0, 1)[None])}, device)[0][0]
    color = Image.fromarray(np.clip(y.transpose(1, 2, 0), 0, 255).astype(np.uint8)).resize(img.size, Image.Resampling.BICUBIC)
    _, cb, cr = color.convert("YCbCr").split()
    if saturation != 1.0:  # scale chroma around neutral grey
        s = max(0.0, min(2.0, float(saturation)))
        cb = cb.point(lambda v: int(max(0, min(255, 128 + (v - 128) * s))))
        cr = cr.point(lambda v: int(max(0, min(255, 128 + (v - 128) * s))))
    out = Image.merge("YCbCr", (gray, cb, cr)).convert("RGB")
    if alpha is not None:
        out.putalpha(alpha)
    return out
