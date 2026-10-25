"""Frame interpolation with RIFE v4.6 (rife-ncnn-vulkan): smoother motion, higher frame rate.

RIFE v4 can produce any number of frames between two images, so the output
frame rate can be an exact target (e.g. 24 → 60 fps), not only 2x / 4x.
"""
from __future__ import annotations

import collections
import subprocess
import threading
import time
from fractions import Fraction
from pathlib import Path

from .. import components
from ..components import CREATE_NO_WINDOW
from ..errors import Canceled, MediaLabError
from ..logging_setup import get_logger

log = get_logger("gpu")
MODEL = "rife-v4.6"


def exe() -> str | None:
    return components.find_binary("rife-ncnn-vulkan")


def model_dir() -> Path | None:
    e = exe()
    if e and (Path(e).parent / MODEL).is_dir():
        return Path(e).parent / MODEL
    return None


def available() -> tuple[bool, str]:
    if not exe() or not model_dir():
        return False, "RIFE is not installed (Settings › Components)."
    return True, "RIFE v4.6 (Vulkan)"


def output_fps(src_fps: Fraction, mode: str | int | float) -> Fraction:
    """``mode``: 2 / 3 / 4 (multiplier) or "60" / "120" (exact target frame rate)."""
    m = str(mode)
    if m in ("60", "120", "50", "48"):
        target = Fraction(int(m))
        return target if target > src_fps else src_fps
    try:
        k = int(float(m))
    except ValueError:
        k = 2
    return src_fps * max(1, min(8, k))


def interpolate_dir(in_dir: Path, out_dir: Path, target_frames: int, gpu: str | None, cancel: threading.Event,
                    on_progress=None) -> int:
    """Interpolate the PNG sequence of ``in_dir`` into ``target_frames`` frames in ``out_dir``."""
    e, m = exe(), model_dir()
    if not e or not m:
        raise MediaLabError("component_missing", "RIFE is not installed.", "Install it from Settings › Components.",
                            extra={"component": "rife"})
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [e, "-i", str(in_dir), "-o", str(out_dir), "-m", str(m), "-n", str(int(target_frames)),
           "-f", "%08d.png", "-j", "1:2:2"]
    if gpu not in (None, "", "auto"):
        cmd += ["-g", "-1" if gpu == "cpu" else str(gpu)]
    log.debug("rife: %s", subprocess.list2cmdline(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace",
                            creationflags=CREATE_NO_WINDOW, cwd=str(Path(e).parent))
    tail: collections.deque[str] = collections.deque(maxlen=40)

    def reader() -> None:
        assert proc.stdout
        for line in proc.stdout:
            if line.strip():
                tail.append(line.strip())

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        while proc.poll() is None:
            if cancel.is_set():
                proc.kill()
                raise Canceled()
            if on_progress:
                on_progress(min(1.0, sum(1 for _ in out_dir.glob("*.png")) / max(1, target_frames)))
            time.sleep(0.3)
    finally:
        if proc.poll() is None:
            proc.kill()
        t.join(timeout=3)
    produced = sum(1 for _ in out_dir.glob("*.png"))
    text = "\n".join(tail)
    if proc.returncode != 0 or produced < target_frames:
        low = text.lower()
        if "vkallocatememory" in low or "out of memory" in low:
            raise MediaLabError("gpu_out_of_memory", "The GPU ran out of video memory during interpolation.",
                                "Lower the output resolution or close other GPU programs.", True, text[-1500:])
        raise MediaLabError("interpolation_failed", f"RIFE produced {produced}/{target_frames} frames.",
                            "Retry; if it persists, update the graphics driver.", True, text[-1500:])
    return produced
