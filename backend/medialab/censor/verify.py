"""2e vérification : CLIP (OpenAI, ViT-B/32) juge chaque zone trouvée par le détecteur.

Le détecteur voit des poitrines dans des pecs, un verre ou un bout de bras. CLIP compare l'image à des descriptions
(« poitrine nue », « torse d'homme », « verre », « bras », « personne habillée »…) : la probabilité des descriptions de
nudité donne une note 0..1. On note la zone avec son contexte (trois fois sa taille) et l'image entière, puis on fait
la moyenne : une zone que CLIP juge nettement non nue n'est pas floutée.

Les descriptions sont encodées une fois pour toutes (``clip_text.npz``, à partir de ``clip_prompts.json``) : seule la
partie image de CLIP est livrée.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

import numpy as np
from PIL import Image

from .. import ai_assets
from ..upscale import onnx_rt

MODEL = "clip_vit_b32"
SIZE = 224
CONTEXT = 3.0  # la zone est jugée avec son entourage : 3 fois sa taille (un torse d'homme se reconnaît mieux)
_TEXT = Path(__file__).with_name("clip_text.npz")
_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], np.float32)


def square(rgb: np.ndarray, box: Sequence[float], context: float = 1.0) -> np.ndarray:
    """Carré centré sur ``box`` (normalisée), ``context`` fois sa taille, complété de noir, réduit à 224 px."""
    h, w = rgb.shape[:2]
    cx, cy = (box[0] + box[2]) / 2 * w, (box[1] + box[3]) / 2 * h
    side = max(16.0, max((box[2] - box[0]) * w, (box[3] - box[1]) * h) * context)
    x0, y0 = int(round(cx - side / 2)), int(round(cy - side / 2))
    x1, y1 = int(round(cx + side / 2)), int(round(cy + side / 2))
    out = np.zeros((y1 - y0, x1 - x0, 3), np.uint8)
    sx0, sy0, sx1, sy1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if sx1 > sx0 and sy1 > sy0:
        out[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = rgb[sy0:sy1, sx0:sx1]
    return np.asarray(Image.fromarray(out).resize((SIZE, SIZE), Image.BICUBIC))


class Verifier:
    def __init__(self, device: str = "auto", progress: Callable[[int, int], None] | None = None) -> None:
        path = ai_assets.ensure(MODEL, progress)
        self.sess, self.lock = onnx_rt.session(path, device)
        self.input = self.sess.get_inputs()[0].name
        with np.load(_TEXT) as z:
            self.text = z["emb"].astype(np.float32)
            self.nude = z["nude"].astype(bool)

    def nudity(self, images: np.ndarray) -> np.ndarray:
        """``images`` : (n, 224, 224, 3) uint8 → probabilité « nudité » de chacune (0..1)."""
        out = []
        for i in range(0, len(images), 32):
            x = ((images[i:i + 32].astype(np.float32) / 255 - _MEAN) / _STD).transpose(0, 3, 1, 2)
            with self.lock:
                e = self.sess.run(["image_embeds"], {self.input: np.ascontiguousarray(x)})[0].astype(np.float32)
            e /= np.linalg.norm(e, axis=1, keepdims=True) + 1e-9
            logits = 100.0 * e @ self.text.T
            logits -= logits.max(1, keepdims=True)
            p = np.exp(logits)
            p /= p.sum(1, keepdims=True)
            out.append(p[:, self.nude].sum(1))
        return np.concatenate(out) if out else np.zeros(0, np.float32)

    def frame(self, rgb: np.ndarray, boxes: Sequence[Sequence[float]]) -> list[float]:
        """Note de chaque zone d'une image : moyenne de la zone (avec son contexte) et de l'image entière."""
        if not len(boxes):
            return []
        crops = [square(rgb, b, CONTEXT) for b in boxes] + [square(rgb, (0.0, 0.0, 1.0, 1.0))]
        p = self.nudity(np.stack(crops))
        return [float((p[k] + p[-1]) / 2) for k in range(len(boxes))]
