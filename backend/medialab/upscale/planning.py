"""Scale planning and VRAM-aware parameters (pure functions, unit-tested)."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Step:
    kind: str  # "ai" | "resize"
    scale: float
    width: int
    height: int

    def to_dict(self) -> dict:
        return {"kind": self.kind, "scale": self.scale, "width": self.width, "height": self.height}


def even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def target_size(w: int, h: int, *, scale: float | None = None, width: int | None = None,
                height: int | None = None, keep_aspect: bool = True) -> tuple[int, int]:
    """Final output size for a scale factor or a custom resolution."""
    if width or height:
        if keep_aspect:
            f = min((width or 10**9) / w, (height or 10**9) / h)
            return even(w * f), even(h * f)
        return even(width or w * (height / h)), even(height or h * (width / w))
    s = scale or 2
    return even(w * s), even(h * s)


def plan(w: int, h: int, out_w: int, out_h: int, native_scales: tuple[int, ...], *,
         max_second_pass_pixels: int = 40_000_000, allow_second_pass: bool = True) -> list[Step]:
    """AI passes + final resize to reach (out_w, out_h).

    * factor ≤ a native scale: one AI pass with the smallest native scale ≥
      factor, then downscale (supersampling) to the exact size.
    * factor > max native: AI pass at max native; a second AI pass only if
      the intermediate stays reasonable, otherwise a high-quality resize.
    """
    factor = max(out_w / w, out_h / h)
    natives = sorted(set(native_scales)) or [4]
    steps: list[Step] = []
    if factor <= 1.0001:
        return [Step("resize", factor, out_w, out_h)]
    cw, ch = w, h
    remaining = factor
    for pass_no in range(2):
        pick = next((s for s in natives if s >= remaining - 1e-6), natives[-1])
        if pass_no == 1:
            if not allow_second_pass or cw * ch * pick * pick > max_second_pass_pixels or remaining < 1.25:
                break
        cw, ch = cw * pick, ch * pick
        steps.append(Step("ai", pick, cw, ch))
        remaining = factor / (cw / w)
        if remaining <= 1.0001:
            break
    if (cw, ch) != (out_w, out_h):
        steps.append(Step("resize", out_w / cw, out_w, out_h))
    return steps


def describe(steps: list[Step]) -> str:
    parts = []
    for s in steps:
        if s.kind == "ai":
            parts.append(f"AI ×{s.scale:g}")
        else:
            parts.append(f"{'down' if s.scale < 1 else 'up'}scale to {s.width}×{s.height} (Lanczos)")
    return " → ".join(parts)


# ------------------------------------------------------------------- VRAM
def ncnn_tile_for_vram(free_mb: int | None, *, limit_mb: int = 0, heavy: bool = True) -> int:
    """Tile size for realesrgan-ncnn-vulkan from free VRAM.

    Real-ESRGAN x4plus (RRDB, 'heavy') needs far more memory per tile than the
    compact anime-video model. Values are conservative; on allocation failure
    the pipeline halves the tile and retries automatically.
    """
    if free_mb is None:
        return 0  # let ncnn decide from the Vulkan heap budget
    budget = free_mb - 1024  # keep headroom for the desktop/other apps
    if limit_mb:
        budget = min(budget, limit_mb)
    table = [(12000, 512), (8000, 400), (4000, 256), (2000, 200), (1000, 128), (500, 64)] if heavy else \
        [(8000, 768), (4000, 512), (2000, 384), (1000, 256), (500, 128)]
    for need, tile in table:
        if budget >= need:
            return tile
    return 32


def torch_tile_for_vram(free_mb: int | None, *, limit_mb: int = 0, fp16: bool = True) -> int:
    if free_mb is None:
        return 256
    budget = free_mb - 1024
    if limit_mb:
        budget = min(budget, limit_mb)
    base = [(14000, 1024), (10000, 768), (6000, 512), (3000, 384), (1500, 256)]
    for need, tile in base:
        if budget * (1.0 if fp16 else 0.55) >= need:
            return tile
    return 128


def reduce_tile(tile: int, level: int) -> int:
    """Tile to use after ``level`` out-of-memory failures."""
    if tile == 0:
        tile = 256
    return max(32, int(tile / (2 ** level)) // 16 * 16 or 32)


def segment_frames(fps: float, seconds: int, frame_w: int, frame_h: int, scale: float,
                   free_disk_mb: int | None = None) -> int:
    """Frames per segment, bounded so a segment's PNGs fit on disk comfortably."""
    n = max(1, int(round(fps * seconds)))
    if free_disk_mb:
        # ~3 bytes/pixel PNG worst case for the upscaled frames + source frames
        per_frame_mb = (frame_w * frame_h * 3 * (1 + scale * scale)) / (1 << 20)
        max_frames = int((free_disk_mb * 0.25) / max(per_frame_mb, 0.01))
        n = max(1, min(n, max_frames))
    return n


def human_eta(seconds: float | None) -> str:
    if seconds is None or math.isinf(seconds):
        return "—"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"
