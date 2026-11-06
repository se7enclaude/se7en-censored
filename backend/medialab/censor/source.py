"""Ce qu'il faut savoir de l'image d'une vidéo pour l'exporter sans la dénaturer : codec, profil, niveau, format de
pixel, couleurs (primaires, transfert SDR / HDR10 / HLG, matrice, plage), métadonnées HDR, débit ; et ses images-clés
(rendu intelligent, voir smart.py)."""
from __future__ import annotations

import json
import subprocess
from fractions import Fraction
from pathlib import Path

import numpy as np

from ..components import CREATE_NO_WINDOW, require
from ..errors import MediaLabError
from ..media.ffmpeg import _rotation

COLOR_KEYS = ("color_primaries", "color_transfer", "color_space", "color_range")


def _run(args: list[str], timeout: float = 120) -> str:
    out = subprocess.run([require("ffprobe"), "-v", "error", *args], capture_output=True, text=True, timeout=timeout,
                         creationflags=CREATE_NO_WINDOW, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise MediaLabError("probe_failed", "La vidéo n'a pas pu être analysée.", "Le fichier est peut-être abîmé.",
                            True, out.stderr[-2000:])
    return out.stdout


def _ratio(v: str | None) -> float | None:
    try:
        return float(Fraction(v)) if v else None
    except (ValueError, ZeroDivisionError):
        return None


def describe(src: Path) -> dict:
    """La piste vidéo de ``src``, telle qu'il faut la reproduire à l'export."""
    data = json.loads(_run(["-print_format", "json", "-show_format", "-show_streams", "-select_streams", "v:0",
                            str(src)]) or "{}")
    st = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    pix = st.get("pix_fmt") or "yuv420p"
    depth = int(st.get("bits_per_raw_sample") or 0) or (12 if "12" in pix else 10 if "10" in pix else 8)
    tags = {k.lower(): v for k, v in (st.get("tags") or {}).items()}
    rate = None
    for k in ("bit_rate", "bps"):  # débit de la piste (MKV : étiquette BPS)
        v = st.get(k) if k == "bit_rate" else tags.get("bps") or tags.get("bps-eng")
        if v and str(v).isdigit():
            rate = int(v)
            break
    d = {
        "codec": st.get("codec_name"), "profile": st.get("profile"), "level": st.get("level"),
        "codec_tag": st.get("codec_tag_string"), "pix_fmt": pix, "depth": depth,
        "chroma": "420" if "420" in pix or pix in ("nv12", "p010le") else "422" if "422" in pix else "444"
        if "444" in pix else "other",
        "full_range": st.get("color_range") == "pc" or pix.startswith("yuvj"),
        # FFmpeg tourne l'image au décodage : les images reçues ont les dimensions affichées, pas celles du flux
        "rotation": _rotation(st),
        "width": st.get("width"), "height": st.get("height"),
        "r_frame_rate": st.get("r_frame_rate"), "avg_frame_rate": st.get("avg_frame_rate"),
        "fps": _ratio(st.get("avg_frame_rate")) or _ratio(st.get("r_frame_rate")),
        "field_order": st.get("field_order") or "unknown", "bit_rate": rate,
        "start": float(st.get("start_time") or 0), "file_start": float(fmt.get("start_time") or 0),
        "container": (fmt.get("format_name") or "").split(",")[0],
        **{k: (st.get(k) if st.get(k) not in (None, "unknown", "unspecified", "reserved") else None) for k in COLOR_KEYS},
        "hdr": hdr_metadata(src),
        "tags": {k: tags[k] for k in ("language", "title") if tags.get(k)},  # étiquettes de la piste vidéo
    }
    if abs(d["rotation"]) % 180 == 90:
        d["width"], d["height"] = d["height"], d["width"]
    if d["color_range"] is None and pix.startswith("yuvj"):
        d["color_range"] = "pc"
    return d


def hdr_metadata(src: Path) -> dict:
    """Métadonnées HDR10 statiques (écran de mastering, MaxCLL / MaxFALL) de la première image, s'il y en a."""
    try:
        data = json.loads(_run(["-select_streams", "v:0", "-read_intervals", "%+#1", "-show_frames",
                                "-show_entries", "frame=side_data_list", "-of", "json", str(src)], 60) or "{}")
    except (MediaLabError, subprocess.TimeoutExpired):
        return {}
    out: dict = {}
    for fr in data.get("frames") or []:
        for sd in fr.get("side_data_list") or []:
            t = sd.get("side_data_type", "")
            if t == "Mastering display metadata" and "red_x" in sd:
                out["mastering"] = {k: _ratio(sd[k]) for k in ("red_x", "red_y", "green_x", "green_y", "blue_x",
                                                                 "blue_y", "white_point_x", "white_point_y",
                                                                 "min_luminance", "max_luminance") if k in sd}
            elif t == "Content light level metadata":
                out["cll"] = (int(sd.get("max_content") or 0), int(sd.get("max_average") or 0))
    return out


def packets(src: Path) -> dict:
    """Tous les paquets de l'image, dans l'ordre de décodage : instant d'affichage (s) et image-clé ou non."""
    text = _run(["-select_streams", "v:0", "-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(src)],
                timeout=1800)
    pts, key = [], []
    for line in text.splitlines():
        parts = line.split(",")
        if len(parts) < 2 or parts[0] in ("", "N/A"):
            continue
        pts.append(float(parts[0]))
        key.append("K" in parts[1])
    return {"pts": np.array(pts, np.float64), "key": np.array(key, bool)}


# ------------------------------------------------------------------ encodage fidèle à la source
def work_format(d: dict) -> str:
    """Format des images brutes entre le décodage et l'encodage : 10 bits pour une source de plus de 8 bits."""
    return "yuv420p10le" if d.get("depth", 8) > 8 else "yuv420p"


def color_args(d: dict) -> list[str]:
    """Étiquettes couleur de la source, recopiées telles quelles (pas de conversion, juste le marquage)."""
    names = {"color_primaries": "-color_primaries", "color_transfer": "-color_trc", "color_space": "-colorspace",
             "color_range": "-color_range"}
    out = []
    for k, flag in names.items():
        if d.get(k):
            out += [flag, d[k]]
    return out


def frame_colors(d: dict) -> list[str]:
    """Filtre qui marque chaque image avec les couleurs de la source : l'encodeur les écrit dans le flux (les seules
    options -color_* ne suffisent pas avec x265 et les images brutes)."""
    p = []
    for k, name in (("color_primaries", "color_primaries"), ("color_transfer", "color_trc"),
                    ("color_space", "colorspace"), ("color_range", "range")):
        if d.get(k):
            p.append(f"{name}={d[k]}")
    return ["-vf", "setparams=" + ":".join(p)] if p else []


X265_COLORS = (("color_primaries", "colorprim"), ("color_transfer", "transfer"), ("color_space", "colormatrix"))


def x265_hdr(d: dict) -> str:
    """Paramètres x265 qui réécrivent les métadonnées HDR10 de la source (unités : 1/50000 et 1/10000 cd/m²)."""
    h = d.get("hdr") or {}
    parts = []
    m = h.get("mastering")
    if m and len(m) == 10:
        c = {k: int(round((v or 0) * 50000)) for k, v in m.items() if not k.endswith("luminance")}
        parts.append("master-display=G({green_x},{green_y})B({blue_x},{blue_y})R({red_x},{red_y})"
                     "WP({white_point_x},{white_point_y})".format(**c)
                     + f"L({int(round(m['max_luminance'] * 10000))},{int(round(m['min_luminance'] * 10000))})")
    if h.get("cll"):
        parts.append(f"max-cll={h['cll'][0]},{h['cll'][1]}")
    if parts or d.get("color_transfer") in ("smpte2084", "arib-std-b67"):
        parts.insert(0, "hdr10=1" if d.get("color_transfer") == "smpte2084" else "hdr10=0")
    return ":".join(parts)


H264_PROFILES = {"Constrained Baseline": "baseline", "Baseline": "baseline", "Main": "main", "High": "high",
                 "High 10": "high10", "High 4:2:2": "high422", "High 4:4:4 Predictive": "high444"}


def cpu_encoder_args(d: dict, *, crf: int = 18, preset: str = "fast") -> list[str]:
    """Encodeur logiciel aligné sur la source (même codec, profil, niveau, profondeur, couleurs, HDR), avec les
    paramètres répétés à chaque image-clé : un morceau réencodé se recolle au milieu de la vidéo d'origine."""
    pix = work_format(d)
    vbv = []
    if d.get("bit_rate"):
        kbps = max(1000, int(d["bit_rate"] * 2 / 1000))  # au plus deux fois le débit moyen de la source
        vbv = [kbps, kbps * 2]
    if d.get("codec") == "hevc":
        p = ["log-level=error", "repeat-headers=1", "open-gop=0", "info=0"]
        p += [f"{name}={d[k]}" for k, name in X265_COLORS if d.get(k)]
        if d.get("color_range"):
            p.append("range=" + ("full" if d["color_range"] == "pc" else "limited"))
        if d.get("level"):
            p.append(f"level-idc={d['level'] / 30:g}")
        if vbv:
            p += [f"vbv-maxrate={vbv[0]}", f"vbv-bufsize={vbv[1]}"]
        if x265_hdr(d):
            p.append(x265_hdr(d))
        return ["-c:v", "libx265", "-preset", preset, "-crf", str(crf), "-pix_fmt", pix, *color_args(d),
                "-x265-params", ":".join(p)]
    args = ["-c:v", "libx264", "-preset", preset, "-crf", str(crf - 1), "-pix_fmt", pix, *color_args(d)]
    prof = H264_PROFILES.get(d.get("profile") or "", "high10" if pix.endswith("10le") else "high")
    if pix.endswith("10le") and prof in ("baseline", "main", "high"):
        prof = "high10"
    args += ["-profile:v", prof]
    if d.get("level"):
        args += ["-level:v", f"{d['level'] / 10:g}"]
    x = ["repeat-headers=1"]
    x += [f"{name}={d[k]}" for k, name in X265_COLORS if d.get(k)]
    if d.get("color_range"):
        x.append("fullrange=" + ("on" if d["color_range"] == "pc" else "off"))
    if vbv:
        x += [f"vbv-maxrate={vbv[0]}", f"vbv-bufsize={vbv[1]}"]
    return args + ["-x264-params", ":".join(x)]
