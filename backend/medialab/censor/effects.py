"""Flou, pixelisation ou bande noire sur des zones d'une image (rgb24, ou plans Y / U / V d'une image yuv420p)."""
from __future__ import annotations

from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

STYLES = ("blur", "pixel", "black")
SHAPES = ("ellipse", "rect")
ELLIPSE_GROW = 1.18  # l'ellipse déborde un peu de la boîte : les coins de la zone restent couverts
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


def _effect(region: np.ndarray, style: str, strength: int, fill: int | tuple) -> np.ndarray:
    h, w = region.shape[:2]
    s = max(0, min(100, int(strength))) / 100
    if style == "black":
        out = np.empty_like(region)
        out[...] = fill
        return out
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
        if shape == "ellipse":  # place pour le débord de l'ellipse
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
            m = _mask(fw, fh, shape if shape in SHAPES else "rect")
            ox, oy = int(round(x1 - fx1)), int(round(y1 - fy1))
            m = m[max(0, oy): max(0, oy) + (y2 - y1), max(0, ox): max(0, ox) + (x2 - x1)]
            if m.shape != (y2 - y1, x2 - x1):
                pad = np.zeros((y2 - y1, x2 - x1), np.float32)
                pad[: m.shape[0], : m.shape[1]] = m
                m = pad
            if region.ndim == 3:
                m = m[..., None]
            arr[y1:y2, x1:x2] = (region * (1 - m) + eff * m + 0.5).astype(np.uint8)


def rgb_planes(frame: np.ndarray) -> list[tuple[np.ndarray, float, float, int | tuple]]:
    return [(frame, 1.0, 1.0, (0, 0, 0))]


def yuv_planes(y: np.ndarray, u: np.ndarray, v: np.ndarray) -> list[tuple[np.ndarray, float, float, int | tuple]]:
    return [(y, 1.0, 1.0, 16), (u, 0.5, 0.5, 128), (v, 0.5, 0.5, 128)]
