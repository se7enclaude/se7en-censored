"""Corrections de la revue de se7en Censored 1.4 : vidéo tournée, rien à flouter, nom déjà pris, index des zones,
cache des zones, une seule tâche par projet, reprise au lancement."""
import subprocess
import threading
from pathlib import Path

import numpy as np
import pytest

from medialab.censor import project as P, render, source as S
from medialab.censor import track as T


def _probe(path: Path, *args: str) -> str:
    from medialab.components import require
    return subprocess.run([require("ffprobe"), "-v", "error", *args, str(path)], capture_output=True, text=True,
                          check=True).stdout


def _make(ffmpeg_bin: str, dst: Path, seconds: int = 4, rotate: int | None = None) -> Path:
    tmp = dst.with_name("raw_" + dst.name)
    subprocess.run([ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=320x240:r=25:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-c:v", "libx264", "-g", "50", "-keyint_min", "50",
                    "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(tmp)], check=True)
    if rotate is None:
        tmp.replace(dst)
    else:  # comme un téléphone : flux 320×240 avec une indication de rotation
        subprocess.run([ffmpeg_bin, "-v", "error", "-y", "-display_rotation", str(rotate), "-i", str(tmp), "-c", "copy",
                        str(dst)], check=True)
        tmp.unlink()
    return dst


def _export(src: Path, dst: Path, zones: list[dict], **kw) -> dict:
    video = P.video_info(src)
    frames = int(round(video["duration"] * 25))
    return render.export(src, video, T.FrameBoxes([], zones), 25.0, frames, dst, style="blur", shape="rect",
                         strength=70, cancel=threading.Event(), progress=lambda *a: None, use_gpu=False, **kw)


ZONE = {"keys": [{"f": 10, "box": [0.1, 0.1, 0.5, 0.5]}, {"f": 40, "box": [0.1, 0.1, 0.5, 0.5]}]}


@pytest.mark.ffmpeg
def test_rotated_phone_video_exports_upright(tmp_path, ffmpeg_bin):
    src = _make(ffmpeg_bin, tmp_path / "phone.mp4", rotate=90)
    d = S.describe(src)
    assert (d["width"], d["height"]) == (240, 320) and d["rotation"]  # dimensions affichées
    for smart_render in (True, False):
        dst = tmp_path / f"out_{smart_render}.mp4"
        res = _export(src, dst, [ZONE], smart_render=smart_render)
        assert res["mode"] == "full"  # les morceaux copiés perdraient la rotation
        wh = _probe(dst, "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "csv=p=0").strip()
        assert wh == "240,320"
        # l'image est droite et nette : le coin bas droit (hors flou) ressemble à celui de la source
        a = subprocess.run([ffmpeg_bin, "-v", "error", "-i", str(src), "-vf", "select=eq(n\\,60),crop=80:80:160:240",
                            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
        b = subprocess.run([ffmpeg_bin, "-v", "error", "-i", str(dst), "-vf", "select=eq(n\\,60),crop=80:80:160:240",
                            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
        diff = np.abs(np.frombuffer(a, np.uint8).astype(int) - np.frombuffer(b, np.uint8).astype(int)).mean()
        assert diff < 6, diff


@pytest.mark.ffmpeg
def test_nothing_to_blur_copies_the_picture(tmp_path, ffmpeg_bin):
    src = _make(ffmpeg_bin, tmp_path / "clean.mp4", seconds=8)
    res = _export(src, tmp_path / "out.mp4", [])
    assert res["mode"] == "smart" and res["reencoded_frames"] == 0 and res["parts"] == 1


def test_output_path_never_overwrites_another_file(tmp_path):
    src = tmp_path / "a" / "clip.mp4"
    out = tmp_path / "out"
    out.mkdir()
    first = render.output_path(src, out)
    assert first.name == "clip (censuré).mp4"
    first.write_bytes(b"x")  # export d'un autre projet, ou fichier à toi
    assert render.output_path(src, out).name == "clip (censuré 2).mp4"
    assert render.output_path(src, out, previous=str(first)) == first  # son propre export : remplacé


def test_leftovers_of_an_interrupted_export(tmp_path):
    src = Path("C:/v/100% fun.mkv")
    for name in ("100% fun (censuré).part.mkv", "100% fun (censuré 2).video.mkv", "100% fun (censuré).mkv"):
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / ".100_ fun (censuré).morceaux").mkdir()
    names = sorted(p.name for p in render.leftovers(src, tmp_path))
    assert names == [".100_ fun (censuré).morceaux", "100% fun (censuré 2).video.mkv", "100% fun (censuré).part.mkv"]


def test_frame_boxes_index_matches_a_full_scan():
    rng = np.random.default_rng(3)
    tracks = []
    for k in range(300):
        f0 = int(rng.integers(0, 5000))
        n = int(rng.integers(1, 900))
        tracks.append(T.Track(f"t{k}", "FEMALE_BREAST_EXPOSED", f0, f0 + n - 1,
                              rng.random((n, 4)).astype(np.float32), 0.9, n))
    tracks.sort(key=lambda t: (t.f0, t.f1))
    manual = [{"keys": [{"f": 900, "box": [0, 0, .5, .5]}, {"f": 100, "box": [.5, .5, 1, 1]}]},
              {"keys": [{"f": 3000, "box": [.2, .2, .3, .3]}]}]
    fb = T.FrameBoxes(tracks, manual)
    for f in [0, 99, 100, 255, 256, 500, 900, 901, 2999, 3000, 3001, 5600, 6000]:
        want = [t.box_at(f) for t in tracks if t.f0 <= f <= t.f1]
        want += [b for b in (T.manual_box(z, f) for z in manual) if b is not None]
        got = fb.at(f)
        assert len(got) == len(want), f
        for a, b in zip(got, want):
            np.testing.assert_allclose(a, b, atol=1e-6)
