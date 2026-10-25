"""Real-CUGAN (anime) through realcugan-ncnn-vulkan — same Vulkan runtime as Real-ESRGAN."""
from __future__ import annotations

from pathlib import Path

from ... import components
from ...errors import MediaLabError
from ..models import ModelInfo
from .base import BackendParams
from .ncnn import NcnnBackend


class CuganBackend(NcnnBackend):
    id = "cugan"
    name = "Real-CUGAN ncnn-Vulkan"

    def exe(self) -> str | None:
        return components.find_binary("realcugan-ncnn-vulkan")

    def available(self) -> tuple[bool, str]:
        if not self.exe():
            return False, "Real-CUGAN is not installed (Settings › Components)."
        devs = self.devices()
        if not devs and components.find_binary("realesrgan-ncnn-vulkan"):
            return False, "No Vulkan-capable GPU was found."
        return True, ", ".join(d["name"] for d in devs) or "Vulkan"

    def devices(self) -> list[dict]:
        # Same Vulkan runtime and devices as Real-ESRGAN (its probe is reused).
        from ..engine import BACKENDS
        return BACKENDS["ncnn"].devices()

    def _cmd(self, model: ModelInfo, src: Path, dst: Path, scale: int, p: BackendParams, fmt: str) -> list[str]:
        exe = self.exe()
        if not exe or not model.ncnn_dir:
            raise MediaLabError("model_missing", "Real-CUGAN is not installed.",
                                "Install it from Settings › Components.", extra={"component": "realcugan"})
        # -1 = conservative: keeps the original drawing, only removes compression noise it is sure about.
        cmd = [exe, "-i", str(src), "-o", str(dst), "-n", "-1", "-s", str(scale), "-m", model.ncnn_dir, "-f", fmt]
        if p.tile:
            cmd += ["-t", str(p.tile)]
        gpu = self.resolve_gpu(p.gpu)
        if gpu is not None:
            cmd += ["-g", gpu]
        if p.threads:
            cmd += ["-j", p.threads]
        return cmd
