"""Upscaling backend interface.

A backend turns images into upscaled images at one of a model's *native*
scales. Planning (multi-pass, exact sizes), filters and video handling live in
the pipelines, so adding a backend (TensorRT, DirectML, a new VSR engine...)
only requires implementing these three methods.
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..models import ModelInfo


@dataclass
class BackendParams:
    tile: int = 0
    gpu: str = "auto"
    threads: str = "1:2:2"
    fp16: bool = True
    vram_limit_mb: int = 0


ProgressFn = Callable[[float], None]  # fraction 0..1 of the current call


class UpscaleBackend(ABC):
    id = "base"
    name = "Base"

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """(usable, human explanation)."""

    @abstractmethod
    def upscale_file(self, model: ModelInfo, src: Path, dst: Path, scale: int, params: BackendParams,
                     cancel: threading.Event, on_progress: ProgressFn | None = None) -> None:
        ...

    @abstractmethod
    def upscale_dir(self, model: ModelInfo, in_dir: Path, out_dir: Path, scale: int, params: BackendParams,
                    cancel: threading.Event, on_progress: ProgressFn | None = None) -> None:
        """Upscale every PNG of ``in_dir`` into ``out_dir`` keeping file names."""

    def devices(self) -> list[dict]:
        return []
