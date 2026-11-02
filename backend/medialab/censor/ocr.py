"""Lecture du texte d'une capture d'écran, sur ce PC (rien n'est envoyé sur Internet) : les lignes de texte sont
repérées par leurs bandes d'encre, puis lues par le modèle de reconnaissance de PaddleOCR (PP-OCRv3, anglais :
chiffres, lettres, ponctuation), téléchargé une fois au premier usage (9 Mo).

Le texte obtenu passe ensuite par timecodes.parse, comme un texte collé.
"""
from __future__ import annotations

import io
from typing import Callable

import numpy as np
from PIL import Image, ImageOps

from .. import ai_assets
from ..errors import MediaLabError
from ..upscale import onnx_rt

MODEL = "ppocr_en_rec"
HEIGHT = 48
MAX_SIDE = 4000
# dictionnaire du modèle (PaddleOCR, ppocr/utils/en_dict.txt, qui finit par une espace) ; 0 = « rien », et le
# modèle ajoute une espace en dernier
CHARS = ["", *"0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~!\"#$%&'()*+,-./ ", " "]
INK = 60  # écart au fond (0..255) à partir duquel un pixel est de l'encre


def lines(img: Image.Image) -> list[np.ndarray]:
    """Bandes de texte d'une image (texte foncé sur fond clair, ou l'inverse), en niveaux de gris, texte foncé."""
    g = np.asarray(ImageOps.exif_transpose(img).convert("L"), np.float32)
    if max(g.shape) > MAX_SIDE:
        k = MAX_SIDE / max(g.shape)
        g = np.asarray(Image.fromarray(g.astype(np.uint8)).resize((int(g.shape[1] * k), int(g.shape[0] * k))),
                       np.float32)
    bg = float(np.median(g))
    if bg < 128:
        g, bg = 255 - g, 255 - bg
    ink = np.abs(g - bg) > INK
    rows = ink.sum(1) > 0
    out: list[np.ndarray] = []
    start = None
    for y, on in enumerate([*rows, False]):
        if on and start is None:
            start = y
        elif not on and start is not None:
            if y - start >= 5:  # trop fin pour être du texte (trait, soulignement)
                band = ink[start:y]
                cols = np.flatnonzero(band.any(0))
                pad = max(2, (y - start) // 4)
                out.append(g[max(0, start - pad): y + pad, max(0, cols[0] - pad): cols[-1] + pad + 1])
            start = None
    return out


class Reader:
    def __init__(self, progress: Callable[[int, int], None] | None = None) -> None:
        path = ai_assets.ensure(MODEL, progress)
        self.sess, self.lock = onnx_rt.session(path, "cpu")
        self.input = self.sess.get_inputs()[0].name

    def line(self, band: np.ndarray) -> str:
        h, w = band.shape
        nw = max(16, min(3200, int(round(w * HEIGHT / max(1, h)))))
        im = np.asarray(Image.fromarray(band.astype(np.uint8)).convert("RGB").resize((nw, HEIGHT), Image.BICUBIC),
                        np.float32)
        x = ((im / 255 - 0.5) / 0.5).transpose(2, 0, 1)[None]
        with self.lock:
            y = self.sess.run(None, {self.input: np.ascontiguousarray(x)})[0][0]
        text, prev = [], 0
        for i in y.argmax(1):  # décodage CTC : on garde un caractère quand il change, sans les « rien »
            if i != prev and 0 < i < len(CHARS):
                text.append(CHARS[i])
            prev = i
        return "".join(text).strip()

    def read(self, data: bytes) -> list[str]:
        try:
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception as e:
            raise MediaLabError("bad_image", "Cette image n'a pas pu être ouverte.",
                                "Colle une capture d'écran (PNG ou JPEG).") from e
        return [t for t in (self.line(b) for b in lines(img)) if t]
