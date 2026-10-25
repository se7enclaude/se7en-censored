"""Suivi visuel d'une zone d'une image à l'autre (corrélation normalisée, numpy seul).

Le détecteur rate parfois une zone pendant un mouvement, un flou de bougé ou un plan sombre. On garde alors l'aspect de
la zone (un « modèle » en niveaux de gris, pris quand elle était bien vue) et on la recherche autour de sa dernière
position dans l'image suivante. La même méthode fait suivre les zones tracées à la main.
"""
from __future__ import annotations

import numpy as np
from PIL import Image

TEMPLATE_SIDE = 40   # le modèle est réduit pour que son grand côté fasse ~40 px : rapide et peu sensible au bruit
MIN_SIDE = 6         # zone trop petite : pas de suivi possible


def gray(rgb: np.ndarray) -> np.ndarray:
    """Luminance (float32) d'une image rgb24."""
    return rgb[..., 0] * np.float32(0.299) + rgb[..., 1] * np.float32(0.587) + rgb[..., 2] * np.float32(0.114)


def ncc(region: np.ndarray, tmpl: np.ndarray) -> np.ndarray | None:
    """Corrélation croisée normalisée du modèle sur toutes les positions où il tient dans la région (-1..1)."""
    h, w = tmpl.shape
    H, W = region.shape
    if H < h or W < w:
        return None
    t = tmpl.astype(np.float64) - float(tmpl.mean())
    tn = float(np.sqrt((t * t).sum()))
    if tn < 1e-3 * h * w:  # modèle uni (mur, peau sans détail) : rien de fiable à suivre
        return None
    r = region.astype(np.float64)
    F = np.fft.rfft2(r)
    G = np.fft.rfft2(t[::-1, ::-1], s=(H, W))
    num = np.fft.irfft2(F * G, s=(H, W))[h - 1:H, w - 1:W]
    # sommes locales par images intégrales
    s1 = np.pad(r, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    s2 = np.pad(r * r, ((1, 0), (1, 0))).cumsum(0).cumsum(1)

    def box(s: np.ndarray) -> np.ndarray:
        return s[h:, w:] - s[:-h, w:] - s[h:, :-w] + s[:-h, :-w]

    n = h * w
    var = box(s2) - box(s1) ** 2 / n
    den = tn * np.sqrt(np.maximum(var, 1e-6))
    out = num / den
    out[var < 1e-3 * n] = -1.0  # fenêtre unie : pas une correspondance
    return np.clip(out, -1.0, 1.0)


class Follower:
    """Une zone suivie : son modèle et sa dernière position (boîte normalisée x1, y1, x2, y2)."""

    def __init__(self, g: np.ndarray, box, scales: tuple[float, ...] = (1.0,)) -> None:
        self.H, self.W = g.shape
        self.scales = scales
        self.box = np.asarray(box, np.float32)
        self.k = 1.0
        self.scale = 1.0          # taille actuelle / taille quand le modèle a été pris
        self.base = (0, 0)        # taille (px) de la zone quand le modèle a été pris
        self.tmpl: np.ndarray | None = None
        self.refresh(g, box)

    def _px(self, box) -> tuple[int, int, int, int]:
        x1, y1, x2, y2 = box
        return (int(round(x1 * self.W)), int(round(y1 * self.H)), int(round(x2 * self.W)), int(round(y2 * self.H)))

    def refresh(self, g: np.ndarray, box) -> None:
        """Nouveau modèle (zone bien vue à cette image)."""
        self.box = np.clip(np.asarray(box, np.float32), 0.0, 1.0)
        x1, y1, x2, y2 = self._px(self.box)
        bw, bh = x2 - x1, y2 - y1
        if bw < MIN_SIDE or bh < MIN_SIDE:
            self.tmpl = None
            return
        self.k = max(1.0, max(bw, bh) / TEMPLATE_SIDE)
        self.scale = 1.0
        self.base = (bw, bh)
        crop = g[y1:y2, x1:x2]
        self.tmpl = _resize(crop, 1 / self.k)

    @property
    def ok(self) -> bool:
        return self.tmpl is not None

    def track(self, g: np.ndarray, reach: float = 0.6) -> tuple[np.ndarray, float] | None:
        """Cherche la zone dans ``g`` autour de sa dernière position. Renvoie (boîte, ressemblance 0..1) ou None."""
        if self.tmpl is None:
            return None
        x1, y1, x2, y2 = self._px(self.box)
        bw, bh = x2 - x1, y2 - y1
        mx, my = int(bw * reach) + 4, int(bh * reach) + 4
        rx1, ry1 = max(0, x1 - mx), max(0, y1 - my)
        rx2, ry2 = min(self.W, x2 + mx), min(self.H, y2 + my)
        region = _resize(g[ry1:ry2, rx1:rx2], 1 / self.k)
        best: tuple[float, float, float, float] | None = None  # (score, x, y, échelle)
        for f in self.scales:  # échelles essayées autour de l'échelle actuelle, toujours depuis le modèle d'origine
            sc = min(2.5, max(0.4, self.scale * f))
            tm = self.tmpl if abs(sc - 1.0) < 1e-3 else _resize(self.tmpl, sc)
            res = ncc(region, tm)
            if res is None:
                continue
            j = int(res.argmax())
            yy, xx = divmod(j, res.shape[1])
            v = float(res.flat[j])
            if best is None or v > best[0]:
                best = (v, xx, yy, sc)
        if best is None:
            return None
        v, xx, yy, sc = best
        self.scale = sc
        nw, nh = self.base[0] * sc, self.base[1] * sc
        nx1 = rx1 + xx * self.k
        ny1 = ry1 + yy * self.k
        box = np.array([nx1 / self.W, ny1 / self.H, (nx1 + nw) / self.W, (ny1 + nh) / self.H], np.float32)
        box = np.clip(box, 0.0, 1.0)
        if box[2] - box[0] < 0.004 or box[3] - box[1] < 0.004:
            return None
        self.box = box
        return box, max(0.0, v)


def _resize(a: np.ndarray, f: float) -> np.ndarray:
    if abs(f - 1.0) < 1e-3:
        return a.astype(np.float32, copy=False)
    h, w = a.shape
    nw, nh = max(1, int(round(w * f))), max(1, int(round(h * f)))
    return np.asarray(Image.fromarray(a.astype(np.float32), mode="F").resize((nw, nh), Image.BILINEAR), np.float32)
