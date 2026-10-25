"""Real-ESRGAN through realesrgan-ncnn-vulkan (any Vulkan GPU, no PyTorch)."""
from __future__ import annotations

import collections
import re
import subprocess
import threading
import time
from pathlib import Path

from ... import components
from ...components import CREATE_NO_WINDOW
from ...config import paths
from ...errors import Canceled, MediaLabError
from ...logging_setup import get_logger
from ..models import ModelInfo
from .base import BackendParams, ProgressFn, UpscaleBackend

log = get_logger("gpu")
_DEVICE_RE = re.compile(r"^\[(\d+)\s+(.+?)\]\s+queue", re.M)
_OOM_MARKERS = ("vkallocatememory failed", "vkqueuesubmit failed", "out of memory", "device lost",
                "vk_error_out_of_device_memory", "vkcreatebuffer failed", "vkmapmemory failed")


class NcnnBackend(UpscaleBackend):
    id = "ncnn"
    name = "Real-ESRGAN ncnn-Vulkan"

    def __init__(self) -> None:
        self._devices: list[dict] | None = None
        self._lock = threading.Lock()

    def exe(self) -> str | None:
        return components.find_binary("realesrgan-ncnn-vulkan")

    def available(self) -> tuple[bool, str]:
        if not self.exe():
            return False, "Real-ESRGAN is not installed (Settings › Components)."
        devs = self.devices()
        if not devs:
            return False, "No Vulkan-capable GPU was found; use Classic or the PyTorch backend (CPU)."
        return True, ", ".join(d["name"] for d in devs)

    # ------------------------------------------------------------ devices
    def devices(self) -> list[dict]:
        with self._lock:
            if self._devices is not None:
                return self._devices
            exe = self.exe()
            if not exe:
                return []
            probe = paths().temp / "ncnn_probe.png"
            out = paths().temp / "ncnn_probe_out.png"
            try:
                from PIL import Image
                Image.new("RGB", (16, 16), (40, 40, 40)).save(probe)
                r = subprocess.run([exe, "-i", str(probe), "-o", str(out), "-n", "realesr-animevideov3", "-s", "2"],
                                   capture_output=True, text=True, timeout=60, creationflags=CREATE_NO_WINDOW,
                                   cwd=str(Path(exe).parent), errors="replace")
                text = (r.stderr or "") + (r.stdout or "")
                self._devices = [{"index": int(i), "name": n.strip()} for i, n in _DEVICE_RE.findall(text)]
                log.info("Vulkan devices: %s", self._devices or "none")
            except (OSError, subprocess.SubprocessError) as e:
                log.warning("Vulkan device probe failed: %s", e)
                self._devices = []
            finally:
                probe.unlink(missing_ok=True)
                out.unlink(missing_ok=True)
            return self._devices

    def resolve_gpu(self, gpu: str) -> str | None:
        """'auto' prefers a discrete GPU (NVIDIA/AMD/Arc) over an integrated one."""
        if gpu not in ("auto", "", None):
            return None if gpu == "cpu" else str(gpu)
        devs = self.devices()
        if len(devs) <= 1:
            return None
        rank = lambda d: (("nvidia" in d["name"].lower()) * 3 + ("radeon" in d["name"].lower() and
                          "graphics" not in d["name"].lower()) * 2 + (" arc" in d["name"].lower()) * 2)
        return str(max(devs, key=rank)["index"])

    # ------------------------------------------------------------ run
    def _cmd(self, model: ModelInfo, src: Path, dst: Path, scale: int, p: BackendParams, fmt: str) -> list[str]:
        exe = self.exe() or components.require("realesrgan-ncnn-vulkan")  # require() raises if missing
        if not model.ncnn_dir:
            raise MediaLabError("model_missing", f"The model “{model.name}” is not installed.",
                                "Reinstall Real-ESRGAN from Settings › Components.")
        cmd = [exe, "-i", str(src), "-o", str(dst), "-n", model.ncnn_name or model.id, "-s", str(scale),
               "-m", model.ncnn_dir, "-f", fmt]
        if p.tile:
            cmd += ["-t", str(p.tile)]
        gpu = self.resolve_gpu(p.gpu)
        if gpu is not None:
            cmd += ["-g", gpu]
        if p.threads:
            cmd += ["-j", p.threads]
        return cmd

    def _run(self, cmd: list[str], cancel: threading.Event, on_line=None, poll=None) -> None:
        log.debug("ncnn: %s", subprocess.list2cmdline(cmd))
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                errors="replace", creationflags=CREATE_NO_WINDOW, cwd=str(Path(cmd[0]).parent))
        tail: collections.deque[str] = collections.deque(maxlen=60)
        done = threading.Event()

        def reader() -> None:
            assert proc.stdout
            for line in proc.stdout:
                line = line.strip()
                if line:
                    tail.append(line)
                    if on_line:
                        on_line(line)
            done.set()

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        try:
            while proc.poll() is None:
                if cancel.is_set():
                    proc.kill()
                    raise Canceled()
                if poll:
                    poll()
                time.sleep(0.25)
        finally:
            if proc.poll() is None:
                proc.kill()
            t.join(timeout=3)
        if poll:
            poll()
        text = "\n".join(tail)
        low = text.lower()
        if any(m in low for m in _OOM_MARKERS):
            raise MediaLabError("gpu_out_of_memory", "The GPU ran out of video memory.",
                                "Media Lab retries automatically with smaller tiles.", True, text[-2000:])
        if proc.returncode != 0:
            if "invalid gpu device" in low:
                raise MediaLabError("gpu_invalid", "The selected GPU is not available.",
                                    "Choose “Auto” in Settings › AI › GPU.", False, text[-2000:])
            raise MediaLabError("upscaler_crashed", f"Real-ESRGAN stopped unexpectedly (code {proc.returncode}).",
                                "Retrying with smaller tiles usually fixes this.", True, text[-2000:])

    def upscale_file(self, model: ModelInfo, src: Path, dst: Path, scale: int, params: BackendParams,
                     cancel: threading.Event, on_progress: ProgressFn | None = None) -> None:
        fmt = "png" if dst.suffix.lower() == ".png" else ("webp" if dst.suffix.lower() == ".webp" else "jpg")

        def on_line(line: str) -> None:
            m = re.match(r"^(\d+(?:[.,]\d+)?)%$", line)
            if m and on_progress:
                on_progress(float(m.group(1).replace(",", ".")) / 100)

        self._run(self._cmd(model, src, dst, scale, params, fmt), cancel, on_line=on_line)
        if not dst.exists() or dst.stat().st_size == 0:
            raise MediaLabError("upscaler_no_output", "Real-ESRGAN produced no output.",
                                "Retry; if it persists, try another model or smaller tiles.", True)

    def upscale_dir(self, model: ModelInfo, in_dir: Path, out_dir: Path, scale: int, params: BackendParams,
                    cancel: threading.Event, on_progress: ProgressFn | None = None) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        total = len(list(in_dir.glob("*.png")))
        if total == 0:
            return

        def poll() -> None:
            if on_progress:
                n = sum(1 for _ in out_dir.glob("*.png"))
                on_progress(min(1.0, n / total))

        self._run(self._cmd(model, in_dir, out_dir, scale, params, "png"), cancel, poll=poll)
        produced = sum(1 for _ in out_dir.glob("*.png"))
        if produced < total:
            raise MediaLabError("upscaler_incomplete", f"Only {produced}/{total} frames were upscaled.",
                                "Media Lab retries automatically with smaller tiles.", True)
