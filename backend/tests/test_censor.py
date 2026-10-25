"""se7en Censored : suivi des zones, effets de flou et export d'une vraie vidéo (sans le modèle d'IA)."""
import threading

import numpy as np
import pytest

from medialab.censor import effects, frames as FR, render
from medialab.censor import project as P
from medialab.censor import track as T

BREAST = T.CLASS_IDS["FEMALE_BREAST_EXPOSED"]
MALE = T.CLASS_IDS["MALE_GENITALIA_EXPOSED"]


def det(rows):
    """rows : (image, classe, confiance, boîte)"""
    return {"f": np.array([r[0] for r in rows], np.int32), "c": np.array([r[1] for r in rows], np.int8),
            "s": np.array([r[2] for r in rows], np.float16), "b": np.array([r[3] for r in rows], np.float32)}


def moving(f0, f1, cls=BREAST, score=0.6, skip=(), x0=0.2, dx=0.002):
    return [(f, cls, score, [x0 + dx * f, 0.3, x0 + dx * f + 0.1, 0.42]) for f in range(f0, f1 + 1) if f not in skip]


def test_threshold_follows_sensitivity():
    assert T.threshold(0) == 0.55 and T.threshold(100) == 0.15
    assert T.threshold(30) > T.threshold(70)


def test_gaps_are_filled_and_padded():
    p = T.Params(pad_s=0.2, gap_s=0.4, margin=0.0, smooth=1)
    tracks = T.build(det(moving(10, 60, skip={20, 21, 22, 40})), 25.0, 200, p)
    assert len(tracks) == 1
    t = tracks[0]
    assert t.f0 == 10 - 5 and t.f1 == 60 + 5            # 0,2 s avant et après
    assert t.box_at(21) is not None                      # trou de 3 images comblé
    assert abs(float(t.box_at(21)[0]) - (0.2 + 0.002 * 21)) < 1e-3  # position interpolée


def test_long_gap_splits_tracks():
    p = T.Params(gap_s=0.2, smooth=1)
    tracks = T.build(det(moving(0, 20) + moving(60, 80)), 25.0, 200, p)
    assert len(tracks) == 2


def test_single_weak_detection_is_dropped_but_strong_one_kept():
    p = T.Params(sensitivity=65)
    weak = T.build(det([(5, BREAST, 0.31, [0.1, 0.1, 0.2, 0.2])]), 25.0, 100, p)
    strong = T.build(det([(5, BREAST, 0.8, [0.1, 0.1, 0.2, 0.2])]), 25.0, 100, p)
    assert weak == [] and len(strong) == 1


def test_classes_and_sensitivity_filter():
    rows = moving(0, 30, score=0.25) + moving(0, 30, cls=MALE, score=0.7, x0=0.6, dx=0)
    only_male = T.build(det(rows), 25.0, 100, T.Params(classes=["MALE_GENITALIA_EXPOSED"], sensitivity=100))
    assert [t.cls for t in only_male] == ["MALE_GENITALIA_EXPOSED"]
    strict = T.build(det(rows), 25.0, 100, T.Params(sensitivity=10))  # seuil 0,51 : la poitrine à 0,25 disparaît
    assert [t.cls for t in strict] == ["MALE_GENITALIA_EXPOSED"]


def test_two_breasts_are_two_tracks():
    rows = moving(0, 30, x0=0.2, dx=0) + moving(0, 30, x0=0.45, dx=0)
    tracks = T.build(det(rows), 25.0, 100, T.Params())
    assert len(tracks) == 2


def test_margin_grows_box():
    tight = T.build(det(moving(0, 10, dx=0)), 25.0, 100, T.Params(margin=0.0, smooth=1))[0]
    wide = T.build(det(moving(0, 10, dx=0)), 25.0, 100, T.Params(margin=0.4, smooth=1))[0]
    w0 = tight.boxes[3, 2] - tight.boxes[3, 0]
    w1 = wide.boxes[3, 2] - wide.boxes[3, 0]
    assert w1 == pytest.approx(w0 * 1.4, rel=1e-3)


def test_hidden_track_stays_hidden_when_sensitivity_changes():
    rows = moving(0, 50, score=0.5)
    t = T.build(det(rows), 25.0, 100, T.Params(sensitivity=65))[0]
    hidden = [{"cls": t.cls, "f0": t.f0, "f1": t.f1}]
    t2 = T.build(det(rows), 25.0, 100, T.Params(sensitivity=90))[0]
    assert T.hidden_match(t2, hidden)
    assert T.FrameBoxes([t2], [], hidden).at(20) == []


def test_manual_zone_interpolates_between_keys():
    z = {"keys": [{"f": 10, "box": [0.0, 0.0, 0.2, 0.2]}, {"f": 20, "box": [0.4, 0.0, 0.6, 0.2]}]}
    assert T.manual_box(z, 9) is None and T.manual_box(z, 21) is None
    assert float(T.manual_box(z, 15)[0]) == pytest.approx(0.2)
    fb = T.FrameBoxes([], [z])
    assert len(fb.at(12)) == 1 and fb.at(25) == []


def test_coverage_counts_seconds():
    tracks = T.build(det(moving(0, 49, dx=0)), 25.0, 250, T.Params(pad_s=0))
    cov = T.coverage(tracks, [], [], 25.0, 250)
    assert cov["zones"] == 1 and cov["seconds"] == pytest.approx(2.0, abs=0.05)


@pytest.mark.parametrize("style", ["blur", "pixel", "black"])
@pytest.mark.parametrize("shape", ["rect", "ellipse"])
def test_effect_changes_only_the_zone(style, shape):
    rng = np.random.default_rng(1)
    img = rng.integers(0, 255, (180, 320, 3), np.uint8)
    out = img.copy()
    box = np.array([0.25, 0.25, 0.5, 0.75], np.float32)
    effects.apply(effects.rgb_planes(out), [box], style, shape, 70)
    zone = (slice(int(0.35 * 180), int(0.65 * 180)), slice(int(0.3 * 320), int(0.45 * 320)))
    assert np.abs(out[zone].astype(int) - img[zone]).mean() > 30      # le centre est bien masqué
    outside = out.copy()
    outside[int(0.15 * 180):int(0.85 * 180), int(0.2 * 320):int(0.55 * 320)] = img[int(0.15 * 180):int(0.85 * 180), int(0.2 * 320):int(0.55 * 320)]
    assert np.array_equal(outside, img)                               # le reste est intact, pixel pour pixel


def test_blur_hides_detail():
    img = np.zeros((200, 200, 3), np.uint8)
    img[::2] = 255  # rayures fines : un flou faible les laisserait visibles
    out = img.copy()
    effects.apply(effects.rgb_planes(out), [np.array([0, 0, 1, 1], np.float32)], "blur", "rect", 70)
    assert out[50:150, 50:150].std() < 12


def test_effect_on_yuv_planes_and_edges():
    w, h = 64, 48
    frame = np.full(w * h * 3 // 2, 200, np.uint8)
    y, u, v = FR.yuv_planes(frame, w, h)
    effects.apply(effects.yuv_planes(y, u, v), [np.array([-0.2, -0.2, 0.5, 0.5], np.float32)], "black", "rect", 70)
    assert y[5, 5] == 16 and u[3, 3] == 128 and y[40, 60] == 200


def test_analysis_size_keeps_ratio():
    assert FR.analysis_size(1920, 1080) == (640, 360)
    assert FR.analysis_size(1080, 1920) == (360, 640)


def test_project_roundtrip(tmp_path):
    f = tmp_path / "film.mp4"
    f.write_bytes(b"x")
    p = P.create(str(f), {"sensitivity": 80, "bogus": 1})
    assert p["options"]["sensitivity"] == 80 and "bogus" not in p["options"]
    assert P.load(p["id"])["source"]["path"] == str(f.resolve())
    assert any(x["id"] == p["id"] for x in P.listing())
    P.delete(p["id"])


def test_output_name(tmp_path):
    assert render.output_path(tmp_path / "Very Bad Trip.mkv", tmp_path).name == "Very Bad Trip (censuré).mkv"
    assert render.output_path(tmp_path / "clip.avi", tmp_path).suffix == ".mp4"


@pytest.mark.ffmpeg
def test_export_blurs_frames_and_keeps_audio(tmp_path, ffmpeg_bin):
    """Vraie vidéo (mire + son) : seules les images 10 à 19 sont floutées, le son est copié, même durée."""
    import subprocess
    from medialab.media import ffmpeg
    src = tmp_path / "src.mp4"
    subprocess.run([ffmpeg_bin, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=25:d=2", "-f", "lavfi",
                    "-i", "sine=f=440:d=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(src)], check=True)
    video = P.video_info(src)
    zone = {"keys": [{"f": 10, "box": [0.2, 0.2, 0.6, 0.7]}, {"f": 19, "box": [0.2, 0.2, 0.6, 0.7]}]}
    fb = T.FrameBoxes([], [zone])
    dst = tmp_path / "out.mp4"
    res = render.export(src, video, fb, 25.0, 50, dst, style="black", shape="rect", strength=70,
                        cancel=threading.Event(), progress=lambda *a: None, use_gpu=False)
    assert res["frames"] == 50 and res["blurred_frames"] == 10
    info = ffmpeg.probe(dst)
    assert info["has_audio"] and abs(info["duration"] - 2.0) < 0.15
    size = (320, 240)
    a = FR.grab(dst, 25.0, 5, size)
    b = FR.grab(dst, 25.0, 15, size)
    ref = FR.grab(src, 25.0, 15, size)
    cy, cx = int(0.45 * 240), int(0.4 * 320)
    assert b[cy - 10:cy + 10, cx - 10:cx + 10].mean() < 30                         # zone noire à l'image 15
    assert np.abs(b[200:230, 250:300].astype(int) - ref[200:230, 250:300]).mean() < 8  # le reste ressemble à la source
    assert a[cy - 10:cy + 10, cx - 10:cx + 10].mean() > 30                         # image 5 : pas de zone
