"""Enhancement filters (non-AI), applied only when the user enables them.

Levels: 0 = off, 1 = low, 2 = medium, 3 = high.

* denoise  – before AI (so noise is not amplified). Video: hqdn3d (spatio-
             temporal); images: nlmeans via FFmpeg, median filter fallback.
* sharpen  – after AI. Video: AMD CAS; images: unsharp mask.
* detail   – after AI: subtle fine grain that counters the "plastic" look of
             GAN upscalers. It does not invent real detail and says so.
* color    – after AI: gentle saturation/contrast restoration (heuristic).
* temporal – video only: temporal-only smoothing + deflicker to reduce the
             frame-to-frame shimmer of per-frame super-resolution models.
* hdr      – HDR (PQ/HLG) sources are tone-mapped to SDR before AI because
             the models operate on 8-bit SDR frames.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

from ..components import CREATE_NO_WINDOW, find_binary

DENOISE_VIDEO = {1: "hqdn3d=1.5:1.5:3:3", 2: "hqdn3d=3:2.5:6:5", 3: "hqdn3d=5:4:9:8"}
DENOISE_IMAGE = {1: "nlmeans=s=1.5:p=7:r=9", 2: "nlmeans=s=3:p=7:r=11", 3: "nlmeans=s=5:p=7:r=15"}
SHARPEN_VIDEO = {1: "cas=strength=0.35", 2: "cas=strength=0.6", 3: "cas=strength=0.85"}
DETAIL_VIDEO = {1: "noise=alls=2:allf=t", 2: "noise=alls=4:allf=t", 3: "noise=alls=6:allf=t"}
COLOR_VIDEO = {1: "eq=saturation=1.06:contrast=1.02", 2: "eq=saturation=1.12:contrast=1.04",
               3: "eq=saturation=1.2:contrast=1.06"}
TEMPORAL = "hqdn3d=0.01:0.01:3:3,deflicker=size=5:mode=pm"
TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv")


def lvl(opts: dict, key: str) -> int:
    try:
        return max(0, min(3, int(opts.get(key) or 0)))
    except (TypeError, ValueError):
        return 0


def video_pre_chain(opts: dict, is_hdr: bool) -> list[str]:
    chain = []
    if is_hdr and opts.get("hdr", "tonemap") == "tonemap":
        chain.append(TONEMAP)
    if lvl(opts, "denoise"):
        chain.append(DENOISE_VIDEO[lvl(opts, "denoise")])
    chain.append("format=rgb24")
    return chain


def video_post_chain(opts: dict, out_w: int, out_h: int, cur_w: int, cur_h: int) -> list[str]:
    chain = []
    if (cur_w, cur_h) != (out_w, out_h):
        chain.append(f"scale={out_w}:{out_h}:flags=lanczos")
    if opts.get("temporal", True):
        chain.append(TEMPORAL)
    if lvl(opts, "color"):
        chain.append(COLOR_VIDEO[lvl(opts, "color")])
    if lvl(opts, "sharpen"):
        chain.append(SHARPEN_VIDEO[lvl(opts, "sharpen")])
    if lvl(opts, "detail"):
        chain.append(DETAIL_VIDEO[lvl(opts, "detail")])
    return chain


def image_denoise(src: Path, dst: Path, level: int) -> Path:
    if not level:
        return src
    ff = find_binary("ffmpeg")
    if ff:
        r = subprocess.run([ff, "-hide_banner", "-v", "error", "-y", "-i", str(src), "-vf", DENOISE_IMAGE[level],
                            str(dst)], capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=600)
        if r.returncode == 0 and dst.exists():
            return dst
    with Image.open(src) as im:
        im.filter(ImageFilter.MedianFilter(3 if level < 3 else 5)).save(dst)
    return dst


def image_post(im: Image.Image, opts: dict) -> Image.Image:
    alpha = im.getchannel("A") if im.mode == "RGBA" else None
    rgb = im.convert("RGB")
    c = lvl(opts, "color")
    if c:
        rgb = ImageEnhance.Color(rgb).enhance({1: 1.06, 2: 1.12, 3: 1.2}[c])
        rgb = ImageEnhance.Contrast(rgb).enhance({1: 1.02, 2: 1.04, 3: 1.06}[c])
    s = lvl(opts, "sharpen")
    if s:
        rgb = rgb.filter(ImageFilter.UnsharpMask(radius=2, percent={1: 50, 2: 90, 3: 140}[s], threshold=2))
    d = lvl(opts, "detail")
    if d:
        arr = np.asarray(rgb).astype(np.float32)
        rng = np.random.default_rng(1234)  # deterministic: same result for preview and render
        noise = rng.normal(0, {1: 1.5, 2: 2.5, 3: 4.0}[d], arr.shape[:2])[..., None]
        rgb = Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8))
    if alpha is not None:
        rgb.putalpha(alpha)
    return rgb
