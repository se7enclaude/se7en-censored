"""Hardware detection and live monitoring (GPU / VRAM / usage / temperature)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import Any

import psutil

from .components import CREATE_NO_WINDOW
from .events import bus
from .logging_setup import get_logger

log = get_logger("gpu")

try:
    import pynvml  # provided by nvidia-ml-py
except Exception:  # pragma: no cover
    pynvml = None


class HardwareMonitor:
    def __init__(self) -> None:
        self._nvml = False
        self._handles: list = []
        self._other_gpus: list[dict] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.latest: dict[str, Any] = {}
        self._init_nvml()
        threading.Thread(target=self._detect_other_gpus, daemon=True).start()

    def _init_nvml(self) -> None:
        if pynvml is None:
            return
        try:
            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            self._handles = [pynvml.nvmlDeviceGetHandleByIndex(i) for i in range(count)]
            self._nvml = True
            log.info("NVML initialised: %d NVIDIA GPU(s)", count)
        except Exception as e:
            log.info("No NVIDIA driver/NVML available (%s); falling back to generic detection", e)

    def _detect_other_gpus(self) -> None:
        """Non-NVIDIA adapters (AMD/Intel) via CIM; names + VRAM only."""
        if sys.platform != "win32":
            return
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion | ConvertTo-Json"],
                capture_output=True, text=True, timeout=20, creationflags=CREATE_NO_WINDOW,
            )
            data = json.loads(out.stdout or "[]")
            if isinstance(data, dict):
                data = [data]
            self._other_gpus = [
                {"name": d.get("Name"), "vram_total_mb": (d.get("AdapterRAM") or 0) // (1 << 20),
                 "driver": d.get("DriverVersion")}
                for d in data if d.get("Name") and "nvidia" not in d.get("Name", "").lower()
                and "basic" not in d.get("Name", "").lower()
            ]
        except Exception as e:
            log.debug("CIM GPU detection failed: %s", e)

    # ------------------------------------------------------------------
    def gpus(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if self._nvml:
            for i, h in enumerate(self._handles):
                info: dict[str, Any] = {"index": i, "vendor": "nvidia", "cuda": True}
                try:
                    name = pynvml.nvmlDeviceGetName(h)
                    info["name"] = name.decode() if isinstance(name, bytes) else name
                    mem = pynvml.nvmlDeviceGetMemoryInfo(h)
                    info["vram_total_mb"] = mem.total // (1 << 20)
                    info["vram_used_mb"] = mem.used // (1 << 20)
                    info["vram_free_mb"] = mem.free // (1 << 20)
                except Exception:
                    pass
                for key, fn in (
                    ("utilization", lambda: pynvml.nvmlDeviceGetUtilizationRates(h).gpu),
                    ("temperature", lambda: pynvml.nvmlDeviceGetTemperature(h, 0)),
                    ("power_w", lambda: pynvml.nvmlDeviceGetPowerUsage(h) / 1000.0),
                    ("encoder_utilization", lambda: pynvml.nvmlDeviceGetEncoderUtilization(h)[0]),
                ):
                    try:
                        info[key] = fn()
                    except Exception:
                        info[key] = None
                out.append(info)
        for j, g in enumerate(self._other_gpus):
            out.append({"index": len(out), "vendor": _vendor(g["name"]), "cuda": False,
                        "name": g["name"], "vram_total_mb": g["vram_total_mb"],
                        "vram_used_mb": None, "vram_free_mb": None,
                        "utilization": None, "temperature": None})
        return out

    def snapshot(self) -> dict[str, Any]:
        vm = psutil.virtual_memory()
        snap = {
            "cpu": {"name": _cpu_name(), "cores": psutil.cpu_count(logical=False),
                    "threads": psutil.cpu_count(), "usage": psutil.cpu_percent(interval=None)},
            "ram": {"total_mb": vm.total // (1 << 20), "used_mb": vm.used // (1 << 20)},
            "gpus": self.gpus(),
            "accelerator": "cuda" if self._nvml and self._handles else ("vulkan" if self._other_gpus else "cpu"),
        }
        self.latest = snap
        return snap

    def primary_free_vram_mb(self) -> int | None:
        gpus = self.gpus()
        if not gpus:
            return None
        g = gpus[0]
        return g.get("vram_free_mb") or g.get("vram_total_mb")

    # ------------------------------------------------------------------
    def start(self, interval: float = 2.0) -> None:
        psutil.cpu_percent(interval=None)
        if self._thread:
            return

        def loop() -> None:
            while not self._stop.wait(interval):
                try:
                    bus.publish("hardware", self.snapshot())
                except Exception as e:
                    log.debug("hardware poll failed: %s", e)

        self._thread = threading.Thread(target=loop, name="hw-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def _vendor(name: str) -> str:
    n = (name or "").lower()
    if "amd" in n or "radeon" in n:
        return "amd"
    if "intel" in n or "arc" in n:
        return "intel"
    return "other"


_cpu_cached: str | None = None


def _cpu_name() -> str:
    global _cpu_cached
    if _cpu_cached is None:
        name = ""
        if sys.platform == "win32":
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                    r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                    name = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
            except OSError:
                pass
        _cpu_cached = name or os.environ.get("PROCESSOR_IDENTIFIER", "CPU")
    return _cpu_cached


_monitor: HardwareMonitor | None = None


def monitor() -> HardwareMonitor:
    global _monitor
    if _monitor is None:
        _monitor = HardwareMonitor()
    return _monitor
