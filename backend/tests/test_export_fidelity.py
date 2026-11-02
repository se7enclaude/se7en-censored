"""Export fidèle et rendu intelligent : de vraies vidéos (SDR 8 bits, HDR10 10 bits, plage pleine) passent par
l'export ; hors des passages floutés, l'image doit rester celle de la source (au bit près en rendu intelligent),
avec les mêmes étiquettes couleur, la même profondeur et les mêmes métadonnées HDR."""
import json
import subprocess
import threading
from pathlib import Path

import numpy as np
import pytest

from medialab.censor import project as P, render, smart, source as S
from medialab.censor import track as T

pytestmark = pytest.mark.ffmpeg

HDR = ("hdr10=1:master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):"
       "max-cll=1000,400")


def _probe(path: Path, *args: str) -> str:
    from medialab.components import require
    return subprocess.run([require("ffprobe"), "-v", "error", *args, str(path)], capture_output=True, text=True,
                          check=True).stdout


def _md5(ffmpeg_bin: str, path: Path) -> list[tuple[str, str]]:
    out = subprocess.run([ffmpeg_bin, "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "framemd5", "-"],
                         capture_output=True, text=True, check=True).stdout
    rows = [ln.split(",") for ln in out.splitlines() if ln and not ln.startswith("#")]
    return [(r[2].strip(), r[-1].strip()) for r in rows]


def _decode_errors(ffmpeg_bin: str, path: Path) -> str:
    return subprocess.run([ffmpeg_bin, "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True,
                          text=True).stderr.strip()


def _make(ffmpeg_bin: str, dst: Path, kind: str, seconds: int = 12) -> Path:
    base = [ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=320x240:r=25:d={seconds}",
            "-f", "lavfi", "-i", f"sine=f=440:d={seconds}"]
    if kind == "sdr":
        enc = ["-c:v", "libx264", "-g", "50", "-keyint_min", "50", "-sc_threshold", "0", "-pix_fmt", "yuv420p",
               "-profile:v", "high", "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709:fullrange=off",
               "-c:a", "aac"]
    elif kind == "full":
        enc = ["-c:v", "libx264", "-g", "50", "-keyint_min", "50", "-sc_threshold", "0", "-pix_fmt", "yuvj420p",
               "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709:fullrange=on", "-c:a", "aac"]
    else:  # HDR10, HEVC 10 bits
        enc = ["-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-x265-params",
               "log-level=error:keyint=50:min-keyint=50:scenecut=0:open-gop=0:colorprim=bt2020:transfer=smpte2084:"
               f"colormatrix=bt2020nc:range=limited:{HDR}", "-c:a", "ac3"]
    subprocess.run([*base, *enc, "-metadata:s:v:0", "language=fra", "-shortest", str(dst)], check=True)
    c = _colors(dst)
    assert c["color_primaries"] and c["color_transfer"] and c["color_space"] and c["color_range"], c  # source marquée
    return dst


def _zone(f0: int, f1: int) -> dict:
    return {"keys": [{"f": f0, "box": [0.3, 0.3, 0.6, 0.7]}, {"f": f1, "box": [0.3, 0.3, 0.6, 0.7]}]}


def _export(src: Path, dst: Path, zones: list[dict], **kw) -> dict:
    video = P.video_info(src)
    frames = int(round(video["duration"] * 25))
    return render.export(src, video, T.FrameBoxes([], zones), 25.0, frames, dst, style="blur", shape="rect",
                         strength=70, cancel=threading.Event(), progress=lambda *a: None, use_gpu=False, **kw)


def _colors(path: Path) -> dict:
    st = json.loads(_probe(path, "-select_streams", "v:0", "-show_streams", "-of", "json"))["streams"][0]
    return {k: st.get(k) for k in ("codec_name", "pix_fmt", "color_range", "color_space", "color_transfer",
                                   "color_primaries", "profile")}


def _psnr_outside(ffmpeg_bin: str, a: Path, b: Path, frame: int) -> float:
    """PSNR (dB) entre deux vidéos sur une image, zone floutée (centre) masquée en noir dans les deux."""
    box = "drawbox=x=0.25*iw:y=0.25*ih:w=0.4*iw:h=0.5*ih:color=black:t=fill"
    out = subprocess.run([ffmpeg_bin, "-v", "info", "-i", str(a), "-i", str(b), "-filter_complex",
                          f"[0:v]select=eq(n\\,{frame}),{box}[x];[1:v]select=eq(n\\,{frame}),{box}[y];[x][y]psnr",
                          "-frames:v", "1", "-f", "null", "-"], capture_output=True, text=True).stderr
    v = out.rsplit("average:", 1)[1].split()[0]
    return 99.0 if v == "inf" else float(v)


def test_plan_extends_to_keyframes():
    keys = [0, 50, 100, 150, 200]
    assert smart.plan([(110, 131)], keys, 250) == [(100, 150)]
    assert smart.plan([(110, 131), (140, 160)], keys, 250) == [(100, 200)]   # deux passages dans des GOP voisins
    assert smart.plan([(0, 3), (240, 250)], keys, 250) == [(0, 50), (200, 250)]
    assert smart.plan([(150, 151)], keys, 250) == [(150, 200)]                # pile sur une image-clé
    assert smart.plan([], keys, 250) == []


@pytest.mark.parametrize("kind,ext", [("sdr", ".mp4"), ("hdr", ".mkv")])
def test_smart_export_is_bit_exact_outside_blur(tmp_path, ffmpeg_bin, kind, ext):
    src = _make(ffmpeg_bin, tmp_path / f"src{ext}", kind)
    dst = tmp_path / f"Ocean's (censuré){ext}"  # apostrophe et accent dans le nom : la liste de recollage tient
    res = _export(src, dst, [_zone(110, 130), _zone(260, 262)])
    assert res["mode"] == "smart", res
    assert res["reencoded_frames"] == 100 and res["blurred_frames"] == 24  # GOP 100-149 et 250-299
    a, b = _md5(ffmpeg_bin, src), _md5(ffmpeg_bin, dst)
    assert len(a) == len(b) == 300
    assert [x[0] for x in a] == [x[0] for x in b]                         # mêmes instants d'affichage
    diff = [i for i in range(300) if a[i][1] != b[i][1]]
    assert diff and set(diff) <= set(range(100, 150)) | set(range(250, 300))  # le reste est identique au bit près
    assert set(range(110, 131)) <= set(diff)
    assert _decode_errors(ffmpeg_bin, dst) == ""
    assert _colors(dst) == _colors(src)
    assert _psnr_outside(ffmpeg_bin, src, dst, 120) > 38                    # passage réencodé : très proche hors flou
    if kind == "hdr":  # métadonnées HDR10 au début et dans les passages réencodés
        for i in (0, 100):
            side = _probe(dst, "-select_streams", "v:0", "-read_intervals", f"{i / 25:.2f}%+#1", "-show_frames",
                          "-show_entries", "frame=side_data_list", "-of", "json")
            assert "Mastering display metadata" in side and '"max_content": 1000' in side
    info = json.loads(_probe(dst, "-show_streams", "-of", "json"))["streams"]
    assert [s["codec_type"] for s in info] == ["video", "audio"]
    assert info[0].get("tags", {}).get("language") == "fra"            # étiquettes de la piste vidéo gardées


def test_full_export_keeps_hdr10_and_10_bits(tmp_path, ffmpeg_bin):
    src = _make(ffmpeg_bin, tmp_path / "src.mkv", "hdr", seconds=4)
    dst = tmp_path / "out.mkv"
    res = _export(src, dst, [_zone(10, 30)], smart_render=False)
    assert res["mode"] == "full" and res["encoder"] == "libx265"
    assert _colors(dst) == _colors(src)
    side = _probe(dst, "-select_streams", "v:0", "-read_intervals", "%+#1", "-show_frames",
                  "-show_entries", "frame=side_data_list", "-of", "json")
    assert "Mastering display metadata" in side and '"max_average": 400' in side
    assert _psnr_outside(ffmpeg_bin, src, dst, 20) > 38


def test_full_export_keeps_full_range(tmp_path, ffmpeg_bin):
    """Plage pleine (0-255) : ni écrêtée ni convertie en plage limitée (16-235)."""
    src = _make(ffmpeg_bin, tmp_path / "src.mp4", "full", seconds=2)
    dst = tmp_path / "out.mp4"
    res = _export(src, dst, [_zone(10, 20)], smart_render=False)
    assert res["mode"] == "full"
    assert _colors(dst)["color_range"] == "pc"
    assert _psnr_outside(ffmpeg_bin, src, dst, 15) > 38


def test_describe_reads_hdr_and_builds_x265_params(tmp_path, ffmpeg_bin):
    src = _make(ffmpeg_bin, tmp_path / "src.mkv", "hdr", seconds=1)
    d = S.describe(src)
    assert d["depth"] == 10 and d["color_transfer"] == "smpte2084" and d["codec"] == "hevc"
    assert S.work_format(d) == "yuv420p10le"
    hdr = S.x265_hdr(d)
    assert "master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)" in hdr
    assert "max-cll=1000,400" in hdr and hdr.startswith("hdr10=1")
    assert S.color_args(d) == ["-color_primaries", "bt2020", "-color_trc", "smpte2084", "-colorspace", "bt2020nc",
                               "-color_range", "tv"]


def test_smart_render_falls_back_on_open_gop(tmp_path, ffmpeg_bin):
    src = tmp_path / "src.mkv"
    subprocess.run([ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=6",
                    "-c:v", "libx265", "-pix_fmt", "yuv420p", "-x265-params",
                    "log-level=error:keyint=50:min-keyint=50:scenecut=0:open-gop=1:bframes=4", str(src)], check=True)
    d = S.describe(src)
    why, _ = smart.check(d, S.packets(src), 25.0, 150)
    if why is None:
        pytest.skip("x265 n'a pas produit de GOP ouvert ici")
    assert "open GOP" in why
    res = _export(src, tmp_path / "out.mkv", [_zone(60, 70)])
    assert res["mode"] == "full" and "open GOP" in res["smart_skipped"]


def test_check_tolerates_frames_padded_at_the_end():
    """L'analyse peut compter quelques images de plus que la source (fin complétée) : rendu intelligent quand même ;
    des images en moins ou décalées : cadence variable, export complet."""
    d = {"codec": "hevc", "container": "matroska", "chroma": "420", "depth": 10, "field_order": "unknown",
         "start": 0.0, "file_start": 0.0}
    fps = 24000 / 1001
    pts = np.arange(1000) / fps
    key = np.zeros(1000, bool)
    key[::240] = True
    pk = {"pts": np.round(pts, 3), "key": key}
    assert smart.check(d, pk, fps, 1005) == (None, [0, 240, 480, 720, 960])
    assert "cadence variable" in smart.check(d, pk, fps, 990)[0]
    jitter = pk["pts"].copy()
    jitter[500:] += 0.03
    assert "cadence variable" in smart.check(d, {"pts": jitter, "key": key}, fps, 1000)[0]
