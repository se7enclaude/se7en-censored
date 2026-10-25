"""Pillow helpers shared by the converter, the duplicate finder and the upscaler."""
from __future__ import annotations

import threading
from pathlib import Path

from PIL import Image, ImageOps

_plugins_lock = threading.Lock()
_plugins_ready = False


def ensure_plugins() -> None:
    """Register optional decoders (HEIC/HEIF photos from iPhones) once."""
    global _plugins_ready
    with _plugins_lock:
        if _plugins_ready:
            return
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except Exception:  # optional: HEIC stays unsupported without it
            pass
        _plugins_ready = True


def open_image(path: str | Path) -> Image.Image:
    ensure_plugins()
    return Image.open(path)


def flatten(im: Image.Image, background=(255, 255, 255)) -> Image.Image:
    """RGB copy; transparent areas become ``background`` (JPEG/BMP have no alpha)."""
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, background)
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return im.convert("RGB")


def upright(im: Image.Image) -> Image.Image:
    """Apply the EXIF orientation (photos from phones) so pixels are the right way up."""
    try:
        return ImageOps.exif_transpose(im)
    except Exception:
        return im
