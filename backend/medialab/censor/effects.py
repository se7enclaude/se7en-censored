"""Flou, pixelisation ou bande noire sur des zones d'une image (rgb24, ou plans Y / U / V d'une image yuv420p en 8 ou
10 bits : une vidéo 10 bits est traitée en 10 bits, sans perte de précision)."""
from __future__ import annotations

from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

STYLES = ("blur", "pixel", "black")
SHAPES = ("ellipse", "rect")
ELLIPSE_GROW = 1.18  # l'ellipse déborde un peu de la boîte : les coins de la zone restent couverts
MID = 8.0  # flou 10 bits : pixels par case pour le lissage
BLUR_CELLS = (20.0, 4.0)  # flou : détails gardés sur le grand côté de la zone, intensité 0 (léger) à 100 (fort)


@lru_cache(maxsize=256)
def _mask(w: int, h: int, shape: str) -> np.ndarray:
    """Masque 0..1 (h×w) : ellipse aux bords adoucis, ou rectangle plein."""
    if shape != "ellipse":
        return np.ones((h, w), np.float32)
    big = Image.new("L", (w, h), 0)
    gw, gh = w * (ELLIPSE_GROW - 1) / 2, h * (ELLIPSE_GROW - 1) / 2
    ImageDraw.Draw(big).ellipse((-gw, -gh, w - 1 + gw, h - 1 + gh), fill=255)
    feather = max(1.0, min(w, h) * 0.035)
    m = np.asarray(big.filter(ImageFilter.GaussianBlur(feather)), np.float32) / 255.0
    return m


def _box_blur(a: np.ndarray, r: int) -> np.ndarray:
    """Flou de rayon ``r`` (moyenne glissante, bords étendus) sur les deux axes d'un tableau 2D en float."""
    if r < 1:
        return a
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (r + 1, r)
        c = np.cumsum(np.pad(a, pad, mode="edge"), axis=axis, dtype=np.float64)
        n = a.shape[axis]
        hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
        lo = np.take(c, np.arange(0, n), axis=axis)
        a = ((hi - lo) / (2 * r + 1)).astype(np.float32)
    return a


def _effect16(region: np.ndarray, style: str, strength: int) -> np.ndarray:
    """Même effet sur un plan 10 bits (uint16) : calculé en flottants, sans repasser par 8 bits."""
    h, w = region.shape[:2]
    s = max(0, min(100, int(strength))) / 100
    img = Image.fromarray(region.astype(np.float32), "F")
    if style == "pixel":
        blocks = max(3, int(round(14 - 9 * s)))
        k = max(w, h) / blocks
        small = img.resize((max(1, round(w / k)), max(1, round(h / k))), Image.BOX)
        return np.asarray(small.resize((w, h), Image.NEAREST))
    cells = BLUR_CELLS[0] + (BLUR_CELLS[1] - BLUR_CELLS[0]) * s
    k = max(1.0, max(w, h) / cells)
    small = img.resize((max(1, round(w / k)), max(1, round(h / k))), Image.BOX)
    # le lissage (≈ le flou gaussien du chemin 8 bits) se fait sur une image intermédiaire de MID pixels par case,
    # puis on agrandit : même résultat, sans calculer en pleine résolution 4K
    sub = max(1.0, min(MID, k))
    mw, mh = max(1, round(w * sub / k)), max(1, round(h * sub / k))
    mid = np.asarray(small.resize((mw, mh), Image.BILINEAR), np.float32)
    sigma = max(1.5, 0.6 * k) * sub / k
    r = max(1, int(round((np.sqrt(1 + 4 * sigma * sigma) - 1) / 2)))  # 3 moyennes de rayon r : variance r(r+1)
    for _ in range(3):
        mid = _box_blur(mid, r)
    return np.asarray(Image.fromarray(mid, "F").resize((w, h), Image.BILINEAR), np.float32)


def _effect(region: np.ndarray, style: str, strength: int, fill: int | tuple) -> np.ndarray:
    h, w = region.shape[:2]
    s = max(0, min(100, int(strength))) / 100
    if style == "black":
        out = np.empty_like(region)
        out[...] = fill
        return out
    if region.dtype == np.uint16:
        return _effect16(region, style, strength)
    img = Image.fromarray(region)
    if style == "pixel":
        blocks = max(3, int(round(14 - 9 * s)))  # nombre de carrés sur le grand côté : 14 (léger) à 5 (fort)
        k = max(w, h) / blocks
        small = img.resize((max(1, round(w / k)), max(1, round(h / k))), Image.BOX)
        return np.asarray(small.resize((w, h), Image.NEAREST))
    # flou très fort et rapide : réduction puis agrandissement, puis flou gaussien pour lisser. Tout est relatif à la
    # taille de la zone : le flou est le même dans l'aperçu (image réduite) et à l'export (4K). Réglée en pixels,
    # l'intensité ne changeait presque rien sur une zone de 4K.
    cells = BLUR_CELLS[0] + (BLUR_CELLS[1] - BLUR_CELLS[0]) * s  # détails gardés sur le grand côté
    k = max(1.0, max(w, h) / cells)
    small = img.resize((max(1, round(w / k)), max(1, round(h / k))), Image.BOX)
    out = small.resize((w, h), Image.BILINEAR).filter(ImageFilter.GaussianBlur(max(1.5, 0.6 * k)))
    return np.asarray(out)


def apply(planes: list[tuple[np.ndarray, float, float, int | tuple]], boxes: list[np.ndarray], style: str = "blur",
          shape: str = "ellipse", strength: int = 70) -> None:
    """Modifie les plans en place. ``planes`` : (tableau, échelle x, échelle y, couleur de la bande noire) ; les
    boîtes sont normalisées (0..1) par rapport à l'image entière."""
    for box in boxes:
        x1n, y1n, x2n, y2n = (float(v) for v in box)
        if x2n <= x1n or y2n <= y1n:
            continue
        whole = x1n <= 0 and y1n <= 0 and x2n >= 1 and y2n >= 1  # image entière (passage plein cadre) : pas d'ovale
        if shape == "ellipse" and not whole:  # place pour le débord de l'ellipse
            gx, gy = (x2n - x1n) * (ELLIPSE_GROW - 1) / 2, (y2n - y1n) * (ELLIPSE_GROW - 1) / 2
            x1n, y1n, x2n, y2n = x1n - gx, y1n - gy, x2n + gx, y2n + gy
        for arr, sx, sy, fill in planes:
            ph, pw = arr.shape[:2]
            x1, y1 = max(0, int(np.floor(x1n * pw))), max(0, int(np.floor(y1n * ph)))
            x2, y2 = min(pw, int(np.ceil(x2n * pw))), min(ph, int(np.ceil(y2n * ph)))
            if x2 - x1 < 2 or y2 - y1 < 2:
                continue
            region = arr[y1:y2, x1:x2]
            eff = _effect(np.ascontiguousarray(region), style, strength, fill)
            # masque calculé sur la boîte complète (même si elle sort de l'image), puis recadré
            fx1, fy1 = x1n * pw, y1n * ph
            fw, fh = max(2, int(round((x2n - x1n) * pw))), max(2, int(round((y2n - y1n) * ph)))
            m = _mask(fw, fh, shape if shape in SHAPES and not whole else "rect")
            ox, oy = int(round(x1 - fx1)), int(round(y1 - fy1))
            m = m[max(0, oy): max(0, oy) + (y2 - y1), max(0, ox): max(0, ox) + (x2 - x1)]
            if m.shape != (y2 - y1, x2 - x1):
                pad = np.zeros((y2 - y1, x2 - x1), np.float32)
                pad[: m.shape[0], : m.shape[1]] = m
                m = pad
            if region.ndim == 3:
                m = m[..., None]
            top = 1023 if arr.dtype == np.uint16 else 255
            arr[y1:y2, x1:x2] = np.clip(region * (1 - m) + eff * m + 0.5, 0, top).astype(arr.dtype)


def rgb_planes(frame: np.ndarray) -> list[tuple[np.ndarray, float, float, int | tuple]]:
    return [(frame, 1.0, 1.0, (0, 0, 0))]


def yuv_planes(y: np.ndarray, u: np.ndarray, v: np.ndarray, full_range: bool = False
               ) -> list[tuple[np.ndarray, float, float, int | tuple]]:
    """Plans d'une image yuv420p 8 ou 10 bits ; la bande noire suit la profondeur et la plage (limitée ou pleine)."""
    shift = 2 if y.dtype == np.uint16 else 0
    black, grey = (0 if full_range else 16) << shift, 128 << shift
    return [(y, 1.0, 1.0, black), (u, 0.5, 0.5, grey), (v, 0.5, 0.5, grey)]
