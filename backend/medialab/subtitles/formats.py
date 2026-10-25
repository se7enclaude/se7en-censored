"""Subtitle files: SRT, WebVTT, ASS (styled + animated, for burn-in) and plain text; SRT / VTT / ASS import.

ASS sizes match the HTML preview exactly: libass sizes a font so that ``usWinAscent + usWinDescent`` (OS/2 table)
equals the ASS font size, while CSS sizes the em square; the conversion factor is read from the font file.
"""
from __future__ import annotations

import difflib
import functools
import re
import struct
from pathlib import Path

from . import styles
from .languages import NO_SPACES


# ------------------------------------------------------------------ time codes
def _hmsms(t: float) -> tuple[int, int, int, int]:
    ms = max(0, int(round(t * 1000)))
    return ms // 3_600_000, ms // 60_000 % 60, ms // 1000 % 60, ms % 1000


def srt_time(t: float) -> str:
    h, m, s, ms = _hmsms(t)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def vtt_time(t: float) -> str:
    h, m, s, ms = _hmsms(t)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _lines(c: dict) -> list[str]:
    return [ln.strip() for ln in str(c["text"]).split("\n") if ln.strip()]


# ------------------------------------------------------------------ plain formats
def to_srt(cues: list[dict]) -> str:
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n" + "\n".join(_lines(c)) + "\n")
    return "\n".join(out)


def to_vtt(cues: list[dict], lang: str = "") -> str:
    out = ["WEBVTT" + (f"\nLanguage: {lang}" if lang else "") + "\n"]
    for i, c in enumerate(cues, 1):
        text = "\n".join(_lines(c)).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        out.append(f"{i}\n{vtt_time(c['start'])} --> {vtt_time(c['end'])}\n{text}\n")
    return "\n".join(out)


def to_txt(cues: list[dict], timestamps: bool = False) -> str:
    if timestamps:
        return "\n".join(f"[{vtt_time(c['start'])[:-4]}] " + " ".join(_lines(c)) for c in cues) + "\n"
    # a readable transcript: paragraphs at long pauses
    paras, cur, prev_end = [], [], None
    for c in cues:
        if prev_end is not None and c["start"] - prev_end > 2.5 and cur:
            paras.append(" ".join(cur))
            cur = []
        cur.append(" ".join(_lines(c)))
        prev_end = c["end"]
    if cur:
        paras.append(" ".join(cur))
    return "\n\n".join(paras) + "\n"


_TIME = r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})|(\d{1,2}):(\d{2})[,.](\d{1,3})"


def _parse_time(m: re.Match) -> float:
    g = m.groups()
    if g[0] is not None:
        return int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3].ljust(3, "0")) / 1000
    return int(g[4]) * 60 + int(g[5]) + int(g[6].ljust(3, "0")) / 1000


def parse(text: str) -> list[dict]:
    """SRT, WebVTT or ASS / SSA -> cues (tags removed)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    if re.search(r"^\[Events\]", text, re.M | re.I):
        return _parse_ass(text)
    blocks = re.split(r"\n\s*\n", text)
    cues = []
    arrow = re.compile(rf"({_TIME})\s*-->\s*({_TIME})")
    for b in blocks:
        lines = b.strip("\n").split("\n")
        for k, line in enumerate(lines):
            m = arrow.search(line)
            if not m:
                continue
            times = list(re.finditer(_TIME, line))
            start, end = _parse_time(times[0]), _parse_time(times[1])
            body = "\n".join(re.sub(r"<[^>]+>|\{[^}]*\}", "", ln).strip() for ln in lines[k + 1:])
            body = body.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").strip()
            if body and end > start:
                cues.append({"start": start, "end": end, "text": body})
            break
    return sorted(cues, key=lambda c: c["start"])


def _parse_ass(text: str) -> list[dict]:
    fields = ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"]
    seen, cues = set(), []
    for line in text.split("\n"):
        key, _, rest = line.partition(":")
        key = key.strip().lower()
        if key == "format" and "text" in rest.lower():
            fields = [f.strip().lower() for f in rest.split(",")]
        if key != "dialogue":
            continue
        parts = rest.strip().split(",", len(fields) - 1)
        if len(parts) < len(fields):
            continue
        row = dict(zip(fields, parts))
        raw = row.get("text", "")
        if re.search(r"\\p[1-9]", raw):  # vector drawing, not text
            continue
        body = re.sub(r"\{[^}]*\}", "", raw).replace("\\N", "\n").replace("\\n", "\n").replace("\\h", "\u00a0")
        body = "\n".join(ln.strip() for ln in body.replace("⧵", "\\").split("\n") if ln.strip())
        try:
            h, m, s = row["start"].strip().split(":")
            start = int(h) * 3600 + int(m) * 60 + float(s)
            h, m, s = row["end"].strip().split(":")
            end = int(h) * 3600 + int(m) * 60 + float(s)
        except (KeyError, ValueError):
            continue
        # the same text on two layers (a box drawn behind the text) is one subtitle
        if body and end > start and (start, end, body) not in seen:
            seen.add((start, end, body))
            cues.append({"start": round(start, 3), "end": round(end, 3), "text": body})
    return sorted(cues, key=lambda c: c["start"])


# ------------------------------------------------------------------ word timing of the displayed text
def _norm(w: str) -> str:
    return re.sub(r"\W+", "", w.lower())


def display_units(text: str, lang: str) -> list[str]:
    flat = re.sub(r"\s*\n\s*", " ", text).strip()
    return list(flat.replace(" ", "")) if lang in NO_SPACES else [u for u in flat.split(" ") if u]


def word_times(cue: dict, lang: str) -> list[tuple[str, float, float]]:
    """Each displayed word with its time: from the recognized words where they match (after correction too),
    interpolated in between, spread over the voiced part of the cue when there is nothing to match."""
    units = display_units(cue["text"], lang)
    if not units:
        return []
    src = [w for w in cue.get("words") or [] if str(w.get("text", "")).strip()]
    start, end = float(cue["start"]), float(cue["end"])
    voice_end = min(end, max((float(w["end"]) for w in src), default=end)) if src else end
    times: list[tuple[float, float] | None] = [None] * len(units)
    if src and lang not in NO_SPACES:
        sm = difflib.SequenceMatcher(a=[_norm(u) for u in units], b=[_norm(w["text"]) for w in src], autojunk=False)
        for a, b, n in sm.get_matching_blocks():
            for k in range(n):
                w = src[b + k]
                times[a + k] = (max(start, float(w["start"])), min(end, float(w["end"])))
    # interpolate unmatched runs by character length between known neighbours
    i = 0
    while i < len(units):
        if times[i] is not None:
            i += 1
            continue
        j = i
        while j < len(units) and times[j] is None:
            j += 1
        lo = times[i - 1][1] if i > 0 else start
        hi = times[j][0] if j < len(units) else voice_end
        if hi <= lo:
            hi = lo + 0.12 * (j - i)
        weights = [max(1, len(u)) for u in units[i:j]]
        total = sum(weights)
        t = lo
        for k, wgt in zip(range(i, j), weights):
            d = (hi - lo) * wgt / total
            times[k] = (t, t + d)
            t += d
        i = j
    return [(u, t[0], t[1]) for u, t in zip(units, times)]


# ------------------------------------------------------------------ font metrics (libass sizing)
@functools.lru_cache(maxsize=64)
def font_scale(family: str, weight: int = 400) -> float:
    """(usWinAscent + usWinDescent) / unitsPerEm of the font file: ASS size = CSS size × this factor."""
    files = styles._FONT_FILES.get(family) or []
    best = min(files, key=lambda fw: abs(fw[1] - weight), default=None)
    if not best:
        return 1.2
    path = styles.fonts_dir() / best[0]
    try:
        data = path.read_bytes()
        base = 0
        if data[:4] == b"ttcf":
            base = struct.unpack(">I", data[12:16])[0]
        num = struct.unpack(">H", data[base + 4:base + 6])[0]
        tables = {}
        for k in range(num):
            rec = data[base + 12 + 16 * k: base + 28 + 16 * k]
            tag, _chk, off, _ln = struct.unpack(">4sIII", rec)
            tables[tag] = off
        upem = struct.unpack(">H", data[tables[b"head"] + 18:tables[b"head"] + 20])[0]
        os2 = tables[b"OS/2"]
        win_asc, win_desc = struct.unpack(">HH", data[os2 + 74:os2 + 78])
        return (win_asc + win_desc) / upem if upem else 1.2
    except (OSError, KeyError, struct.error, IndexError):
        return 1.2


# ------------------------------------------------------------------ ASS
def _hex(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return 255, 255, 255


def ass_color(color: str, opacity: float = 1.0) -> str:
    r, g, b = _hex(color)
    a = round((1 - max(0.0, min(1.0, opacity))) * 255)
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def _bgr(color: str) -> str:
    r, g, b = _hex(color)
    return f"&H{b:02X}{g:02X}{r:02X}&"


def _escape(t: str) -> str:
    return t.replace("\\", "⧵").replace("{", "\\{").replace("}", "\\}")


def to_ass(cues: list[dict], style: dict | None, width: int, height: int, lang: str = "") -> str:
    s = styles.resolve(style)
    W, H = int(width or 1920), int(height or 1080)
    em = s["size"] / 100 * H  # CSS font size in video pixels
    fs = em * font_scale(s["font"], int(s["weight"]))
    outline = s["outline"] * em
    shadow = s["shadow"] * em
    align = {"left": 1, "center": 2, "right": 3}[s["align"]] + (6 if s["vertical"] == "top" else 0)
    margin_v = round(s["position"] / 100 * H)
    margin_lr = round((100 - s["max_width"]) / 200 * W)
    box = s["background"] == "box"
    primary = ass_color(s["color"], s["opacity"])
    secondary = primary
    if box:  # libass BorderStyle 3: the box uses the outline colour; its padding is the outline width
        outline_col = ass_color(s["bg_color"], s["bg_opacity"])
        outline = s["bg_padding"] * em
        back = ass_color(s["shadow_color"], s["shadow_opacity"] if shadow else 0)
    else:
        outline_col = ass_color(s["outline_color"], s["opacity"])
        back = ass_color(s["shadow_color"], s["shadow_opacity"] * s["opacity"])
    bold = int(s["weight"])
    style_line = (f"{fs:.1f},{primary},{secondary},{{oc}},{{back}},{1 if bold >= 600 else 0},"
                  f"{1 if s['italic'] else 0},0,0,100,100,{s['letter_spacing'] * em:.1f},0,{{bs}},{{ol}},"
                  f"{{sh}},{align},{margin_lr},{margin_lr},{margin_v},1")
    # With a box, text split into several timed runs (karaoke, word by word...) would get one translucent box per
    # run, darker where they overlap: the box is then drawn once per line on its own layer, the text above it.
    split_box = box and s["animation"] in _TIMED
    head = [
        "[Script Info]", "; se7en subtitles", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}",
        "WrapStyle: 2", "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Default,{s['font']}," + style_line.format(oc=outline_col, back=back, bs=3 if box else 1,
                                                            ol=f"{outline:.1f}", sh=f"{shadow:.1f}"),
        *([f"Style: Text,{s['font']}," + style_line.format(oc=outline_col, back=back, bs=1, ol="0", sh="0")]
          if split_box else []),
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    x = {1: margin_lr, 2: W / 2, 3: W - margin_lr}[align if align <= 3 else align - 6]
    y = margin_v if s["vertical"] == "top" else H - margin_v
    events = []
    for c in cues:
        if not str(c.get("text", "")).strip():
            continue
        t = f"{ass_time(c['start'])},{ass_time(c['end'])}"
        if split_box:
            text = c["text"].upper() if s["uppercase"] else c["text"]
            plain = "\\N".join(_escape(ln.strip()) for ln in text.split("\n") if ln.strip())
            events.append(f"Dialogue: 0,{t},Default,,0,0,0,,{{\\b{int(s['weight'])}\\1a&HFF&}}" + plain)
            events.append(f"Dialogue: 1,{t},Text,,0,0,0,,{_event_text(c, s, lang, em, x, y)}")
        else:
            events.append(f"Dialogue: 0,{t},Default,,0,0,0,,{_event_text(c, s, lang, em, x, y)}")
    return "\n".join(head + events) + "\n"


_TIMED = ("word", "char", "typewriter", "highlight", "karaoke")


def _event_text(c: dict, s: dict, lang: str, em: float, x: float, y: float) -> str:
    anim = s["animation"]
    speed = max(0.25, float(s.get("anim_speed") or 1.0))
    text = c["text"].upper() if s["uppercase"] else c["text"]
    tag = f"\\b{int(s['weight'])}"  # libass lays out right-to-left scripts by itself
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    plain = "\\N".join(_escape(ln) for ln in lines)
    if anim == "fade":
        return "{" + tag + f"\\fad({int(160 / speed)},{int(120 / speed)})" + "}" + plain
    if anim == "pop":
        d = int(120 / speed)
        return ("{" + tag + f"\\fad({int(70 / speed)},{int(90 / speed)})\\fscx72\\fscy72\\t(0,{d},\\fscx106\\fscy106)"
                f"\\t({d},{int(d * 1.7)},\\fscx100\\fscy100)" + "}" + plain)
    if anim == "slide":
        dy = em * 0.45 * (1 if s["vertical"] != "top" else -1)
        return ("{" + tag + f"\\fad({int(150 / speed)},{int(100 / speed)})"
                f"\\move({x:.0f},{y + dy:.0f},{x:.0f},{y:.0f},0,{int(240 / speed)})" + "}" + plain)
    if anim in _TIMED:
        return "{" + tag + "}" + _timed_text({**c, "text": text}, s, lang, anim, speed)
    return "{" + tag + "}" + plain


def _timed_text(c: dict, s: dict, lang: str, anim: str, speed: float) -> str:
    """Per-word / per-character timing tags, words placed back on their lines."""
    start = float(c["start"])
    timed = word_times(c, lang)
    lines = [ln.strip() for ln in c["text"].split("\n") if ln.strip()]
    per_line = [len(display_units(ln, lang)) for ln in lines]
    sep = "" if lang in NO_SPACES else " "
    base, hl = _bgr(s["color"]), _bgr(s["highlight_color"])
    out_lines, k = [], 0
    for n in per_line:
        parts = []
        for unit, t0, t1 in timed[k:k + n]:
            if anim == "karaoke":
                break  # built by _karaoke_line
            a = max(0, int((t0 - start) * 1000))
            b = max(a + 1, int((t1 - start) * 1000))
            u = _escape(unit)
            if anim == "word":
                parts.append("{\\alpha&HFF&\\t(%d,%d,\\alpha&H00&)}%s" % (a, a + int(90 / speed), u))
            elif anim == "highlight":
                parts.append("{\\1c%s\\t(%d,%d,\\1c%s)\\t(%d,%d,\\1c%s)}%s" % (base, a, a + 60, hl, b, b + 80, base, u))
            else:  # char / typewriter: characters appear one after another across the word
                chars = list(unit)
                step = (b - a) / max(1, len(chars)) if anim == "char" else min(60 / speed, (b - a) / max(1, len(chars)))
                for q, ch in enumerate(chars):
                    ta = int(a + q * step)
                    fade = int(70 / speed) if anim == "char" else 1
                    parts.append("{\\alpha&HFF&\\t(%d,%d,\\alpha&H00&)}%s" % (ta, ta + fade, _escape(ch)))
        if anim == "karaoke":
            out_lines.append(_karaoke_line(timed[k:k + n], start, base, hl, sep))
        else:
            out_lines.append(sep.join(parts) if anim != "char" and anim != "typewriter" else
                             _join_chars(parts, timed[k:k + n], sep))
        k += n
    return "\\N".join(out_lines)


def _join_chars(parts: list[str], timed: list, sep: str) -> str:
    # parts are per character; put the separators back between words
    out, idx = [], 0
    for unit, _a, _b in timed:
        out.append("".join(parts[idx:idx + len(unit)]))
        idx += len(unit)
    return sep.join(out)


def _karaoke_line(timed: list, start: float, base: str, hl: str, sep: str) -> str:
    """ASS karaoke: the text fills from its base colour to the highlight colour as each word is spoken."""
    out = ["{\\1c%s\\2c%s}" % (hl, base)]
    cursor = start
    for i, (unit, t0, t1) in enumerate(timed):
        gap = max(0, int(round((t0 - cursor) * 100)))
        if gap:
            out.append("{\\k%d}" % gap)
        dur = max(1, int(round((t1 - max(t0, cursor)) * 100)))
        out.append("{\\kf%d}%s%s" % (dur, _escape(unit), sep if i < len(timed) - 1 else ""))
        cursor = max(cursor, t1)
    return "".join(out)


def write(path: Path, content: str) -> Path:
    path.write_text(content, "utf-8-sig" if path.suffix.lower() in (".srt", ".ass") else "utf-8", newline="\n")
    return path
