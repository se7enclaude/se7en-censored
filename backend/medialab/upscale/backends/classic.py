"""Classic resampling (Lanczos). Always available; adds no detail."""
from __future__ import annotations

import threading
from pathlib import Path

from PIL import Image

from ...errors import Canceled
from ..models import ModelInfo
from .base import BackendParams, ProgressFn, UpscaleBackend


class ClassicBackend(UpscaleBackend):
    id = "classic"
    name = "Classic (Lanczos)"

    def available(self) -> tuple[bool, str]:
        return True, "CPU"

    def upscale_file(self, model: ModelInfo, src: Path, dst: Path, scale: int, params: BackendParams,
                     cancel: threading.Event, on_progress: ProgressFn | None = None) -> None:
        with Image.open(src) as im:
            im.load()
            out = im.resize((im.width * scale, im.height * scale), Image.Resampling.LANCZOS)
            out.save(dst)
        if on_progress:
            on_progress(1.0)

    def upscale_dir(self, model: ModelInfo, in_dir: Path, out_dir: Path, scale: int, params: BackendParams,
                    cancel: threading.Event, on_progress: ProgressFn | None = None) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        files = sorted(in_dir.glob("*.png"))
        for i, f in enumerate(files):
            if cancel.is_set():
                raise Canceled()
            target = out_dir / f.name
            if not target.exists():
                self.upscale_file(model, f, target, scale, params, cancel)
            if on_progress:
                on_progress((i + 1) / len(files))
