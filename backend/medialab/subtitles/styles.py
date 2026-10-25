"""Subtitle styles: one schema shared by the live preview (HTML) and the burned-in export (ASS / libass).

Sizes are relative so a style looks the same at any resolution:
``size`` is the font size in % of the video height; ``outline`` / ``shadow`` / ``bg_padding`` / ``letter_spacing``
are fractions of the font size; ``position`` is the distance from the bottom (or top) edge in % of the height;
``max_width`` is in % of the video width.

Fonts are Windows system fonts, present on every PC: the preview and the burned-in video use the very same files.
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT: dict = {
    "preset": "netflix", "font": "Segoe UI", "size": 5.0, "weight": 600, "italic": False, "uppercase": False,
    "color": "#FFFFFF", "opacity": 1.0,
    "outline_color": "#000000", "outline": 0.08,
    "shadow_color": "#000000", "shadow": 0.05, "shadow_opacity": 0.55,
    "background": "none", "bg_color": "#000000", "bg_opacity": 0.6, "bg_padding": 0.25,
    "position": 7.0, "vertical": "bottom", "align": "center", "letter_spacing": 0.0, "line_height": 1.18,
    "max_width": 84.0, "animation": "none", "highlight_color": "#FFD60A", "anim_speed": 1.0,
}

PRESETS: dict[str, dict] = {
    "minimal": {"font": "Segoe UI", "size": 4.4, "weight": 400, "outline": 0.0, "shadow": 0.08,
                "shadow_opacity": 0.8, "position": 6.0},
    "youtube": {"font": "Arial", "size": 4.6, "weight": 400, "outline": 0.0, "shadow": 0.0, "background": "box",
                "bg_color": "#080808", "bg_opacity": 0.75, "bg_padding": 0.22, "position": 6.0},
    "cinematic": {"font": "Georgia", "size": 4.8, "weight": 400, "italic": False, "color": "#F4EFE6",
                  "outline": 0.05, "shadow": 0.07, "shadow_opacity": 0.7, "letter_spacing": 0.02, "position": 8.5,
                  "animation": "fade"},
    "tiktok": {"font": "Arial Black", "size": 6.6, "weight": 900, "uppercase": True, "outline": 0.16,
               "shadow": 0.0, "position": 30.0, "max_width": 78.0, "animation": "pop",
               "highlight_color": "#FFE000"},
    "anime": {"font": "Trebuchet MS", "size": 5.4, "weight": 700, "color": "#FFFFFF", "outline_color": "#1E2A78",
              "outline": 0.16, "shadow": 0.06, "shadow_color": "#0B1030", "position": 6.5},
    "documentary": {"font": "Segoe UI", "size": 4.6, "weight": 500, "color": "#FFFFFF", "outline": 0.05,
                    "shadow": 0.06, "shadow_opacity": 0.6, "align": "left", "position": 8.0, "max_width": 70.0},
    "netflix": {"font": "Segoe UI", "size": 5.0, "weight": 600, "outline": 0.08, "shadow": 0.05,
                "shadow_opacity": 0.55, "position": 7.0},
    "clean": {"font": "Calibri", "size": 5.0, "weight": 400, "outline": 0.06, "shadow": 0.0, "position": 7.0},
    "bold": {"font": "Impact", "size": 6.2, "weight": 400, "letter_spacing": 0.03, "outline": 0.12,
             "shadow": 0.06, "position": 9.0, "highlight_color": "#FF3B30", "animation": "highlight"},
}

ANIMATIONS = ["none", "fade", "pop", "slide", "word", "char", "highlight", "typewriter", "karaoke"]

# family -> (file, weight) for the variants se7en uses; only families found on this PC are offered
_FONT_FILES = {
    "Segoe UI": [("segoeuil.ttf", 300), ("segoeui.ttf", 400), ("seguisb.ttf", 600), ("segoeuib.ttf", 700),
                 ("seguibl.ttf", 900), ("segoeuii.ttf", 400)],
    "Arial": [("arial.ttf", 400), ("arialbd.ttf", 700), ("ariali.ttf", 400)],
    "Arial Black": [("ariblk.ttf", 900)],
    "Calibri": [("calibril.ttf", 300), ("calibri.ttf", 400), ("calibrib.ttf", 700)],
    "Verdana": [("verdana.ttf", 400), ("verdanab.ttf", 700)],
    "Tahoma": [("tahoma.ttf", 400), ("tahomabd.ttf", 700)],
    "Trebuchet MS": [("trebuc.ttf", 400), ("trebucbd.ttf", 700)],
    "Georgia": [("georgia.ttf", 400), ("georgiab.ttf", 700), ("georgiai.ttf", 400)],
    "Times New Roman": [("times.ttf", 400), ("timesbd.ttf", 700)],
    "Impact": [("impact.ttf", 400)],
    "Comic Sans MS": [("comic.ttf", 400), ("comicbd.ttf", 700)],
    "Consolas": [("consola.ttf", 400), ("consolab.ttf", 700)],
    "Cambria": [("cambria.ttc", 400), ("cambriab.ttf", 700)],
    "Candara": [("Candara.ttf", 400), ("Candarab.ttf", 700)],
    "Franklin Gothic Medium": [("framd.ttf", 500)],
    "Bahnschrift": [("bahnschrift.ttf", 400)],
}


def fonts_dir() -> Path:
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"


def available_fonts() -> list[dict]:
    d = fonts_dir()
    out = []
    for family, files in _FONT_FILES.items():
        weights = sorted({w for f, w in files if (d / f).exists()})
        if weights:
            out.append({"family": family, "weights": weights})
    return out


def resolve(style: dict | None) -> dict:
    """A complete style: defaults < preset < user values."""
    s = dict(style or {})
    preset = s.get("preset") or DEFAULT["preset"]
    return {**DEFAULT, **PRESETS.get(preset, {}), **{k: v for k, v in s.items() if v is not None}, "preset": preset}


def presets() -> dict:
    return {name: resolve({"preset": name}) for name in PRESETS}
