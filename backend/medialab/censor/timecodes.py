"""Passages à censurer donnés par timecodes : une liste collée, un .txt / .srt / .csv, ou le texte lu sur une capture
d'écran (ocr.py).

Formats reconnus, une plage ou un instant par ligne :
  « 00:12:34 - 00:13:10 », « 12:34–13:10 », « 12:34 à 13:10 », « 00:01:02,500 --> 00:01:05,000 » (SRT),
  « 00:12:34;00:13:10 » ou « 00:12:34,00:13:10 » (CSV), « 1:02:03.500 » (millisecondes),
  « 01:02:03:12 » (HH:MM:SS:images), « 1h02m03s », « 12m34s », « 754s ».
Un timecode seul est un instant : il devient une plage de ± ``margin`` secondes. Les lignes sans timecode, ou dont le
timecode est impossible, sont ignorées avec un message.
"""
from __future__ import annotations

import math
import re

# HH:MM:SS:FF (images ; « ; » avant les images en drop-frame)
_FRAMES = r"(?P<f_h>\d{1,2}):(?P<f_m>\d{1,2}):(?P<f_s>\d{1,2})[:;](?P<f_f>\d{1,3})(?![\d:])(?![.,]\d)"
# [H:]MM:SS[.mmm] — la virgule décimale (SRT) n'est prise que si elle n'est pas suivie d'un autre timecode (CSV)
_CLOCK = r"(?:(?P<c_h>\d{1,3}):)?(?P<c_m>\d{1,3}):(?P<c_s>\d{1,2})(?:[.,](?P<c_ms>\d{1,3})(?![\d:]))?(?![\d:])"
# 1h02m03s, 12m34s, 12m34, 754s, 45 min 10 s
_UNITS = (r"(?:(?P<u_h>\d{1,3})\s*h\s*)?(?:(?P<u_m>\d{1,3})\s*(?:min|mn|m)\s*)?"
          r"(?:(?P<u_s>\d{1,5}(?:[.,]\d{1,3})?)\s*(?:sec|s)?)?(?![\w:])")
_UNIT_AHEAD = r"(?=\d+\s*(?:h|min|mn|m|sec|s)(?![a-zà-ÿ]))"
_TOKEN = re.compile(rf"(?<![\d:])(?:{_FRAMES}|{_CLOCK}|(?<!\d[.,]){_UNIT_AHEAD}{_UNITS})", re.IGNORECASE)
_OCR_FIX = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1"})


class BadTime(ValueError):
    pass


def _ocr_clean(line: str) -> str:
    """Erreurs de lecture courantes sur une capture : O pour 0, l ou I pour 1, dans les timecodes."""
    return re.sub(r"(?<![A-Za-z])[\dOolI|]{1,3}(?:[:.,][\dOolI|]{1,3})+(?![A-Za-z])",
                  lambda m: m.group(0).translate(_OCR_FIX) if re.search(r"\d", m.group(0)) else m.group(0), line)


def _seconds(m: re.Match, fps: float) -> float:
    g = m.groupdict()
    if g.get("f_h") is not None:
        h, mi, s, f = (int(g[k]) for k in ("f_h", "f_m", "f_s", "f_f"))
        if mi > 59 or s > 59:
            raise BadTime("minutes ou secondes au-delà de 59")
        if f >= math.ceil(fps):
            raise BadTime(f"n° d'image {f} impossible à {fps:g} images/s")
        return h * 3600 + mi * 60 + s + f / fps
    if g.get("c_m") is not None:
        h = int(g["c_h"]) if g.get("c_h") else 0
        mi, s = int(g["c_m"]), int(g["c_s"])
        if s > 59 or (g.get("c_h") and mi > 59):
            raise BadTime("minutes ou secondes au-delà de 59")
        ms = int((g.get("c_ms") or "0").ljust(3, "0"))
        return h * 3600 + mi * 60 + s + ms / 1000
    h = int(g["u_h"] or 0)
    mi = int(g["u_m"] or 0)
    s = float((g["u_s"] or "0").replace(",", "."))
    if (g["u_h"] or g["u_m"]) and s >= 60:
        raise BadTime("secondes au-delà de 59")
    return h * 3600 + mi * 60 + s


def times_in(line: str, fps: float) -> list[float]:
    """Les instants (en secondes) écrits dans une ligne, dans l'ordre."""
    out = []
    for m in _TOKEN.finditer(line):
        if not m.group(0).strip():
            continue
        out.append(_seconds(m, fps))
    return out


def clock(t: float) -> str:
    """Instant lisible : 1:02:03.500 (ou 2:03.500 sous une heure)."""
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    base = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    return base + (f".{ms:03d}" if ms else "")


def parse(text: str, fps: float = 25.0, duration: float | None = None, margin: float = 2.0,
          ocr: bool = False) -> dict:
    """Plages trouvées dans ``text`` et lignes ignorées (avec la raison).

    Plage : {start, end (secondes), line (n° de ligne, à partir de 1), text, point (un instant seul), note}."""
    fps = fps or 25.0
    srt = "-->" in text
    ranges: list[dict] = []
    skipped: list[dict] = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip().lstrip("﻿")
        if not line:
            continue
        if ocr:
            line = _ocr_clean(line)
        try:
            ts = times_in(line, fps)
        except BadTime as e:
            skipped.append({"line": n, "text": raw.strip(), "reason": f"timecode impossible : {e}"})
            continue
        if not ts:
            if not srt and not re.fullmatch(r"\d+", line):  # en SRT : numéros et texte des sous-titres attendus
                skipped.append({"line": n, "text": raw.strip(), "reason": "pas de timecode"})
            continue
        note = ""
        if len(ts) > 2:
            note = f"{len(ts)} timecodes sur la ligne : les deux premiers sont gardés"
        point = len(ts) == 1 or ts[0] == ts[1]
        if point:
            start, end = max(0.0, ts[0] - margin), ts[0] + margin
        else:
            start, end = ts[0], ts[1]
            if end < start:
                skipped.append({"line": n, "text": raw.strip(), "reason": "la fin est avant le début"})
                continue
        if duration:
            if start >= duration:
                skipped.append({"line": n, "text": raw.strip(),
                                "reason": f"après la fin de la vidéo ({clock(duration)})"})
                continue
            if end > duration:
                end = duration
                note = (note + " · " if note else "") + "fin ramenée à la fin de la vidéo"
        ranges.append({"start": round(start, 3), "end": round(end, 3), "line": n, "text": raw.strip(),
                       "point": point, "note": note})
    return {"ranges": ranges, "skipped": skipped}


def to_frames(start: float, end: float, fps: float, frames: int | None = None) -> tuple[int, int]:
    """Plage en secondes → images (première, dernière incluse)."""
    f0 = max(0, int(math.floor(start * fps + 1e-6)))
    f1 = max(f0, int(math.ceil(end * fps - 1e-6)) - 1)
    if frames:
        f1 = min(f1, frames - 1)
    return f0, f1
