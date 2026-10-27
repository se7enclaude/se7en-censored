"""se7en Censored : suivi des zones, effets de flou et export d'une vraie vidéo (sans le modèle d'IA)."""
import threading

import numpy as np
import pytest
from PIL import Image

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


def test_isolated_detections_are_dropped():
    p = T.Params(sensitivity=65)
    single = T.build(det([(5, BREAST, 0.8, [0.1, 0.1, 0.2, 0.2])]), 25.0, 100, p)
    flash = T.build(det(moving(0, 3, score=0.7)), 25.0, 100, p)  # 4 images : un verre, un bout de bras…
    assert single == [] and flash == []
    assert len(T.build(det(moving(0, 7, score=0.7)), 25.0, 100, p)) == 1  # 8 images (0,3 s) : confirmée


def test_zone_must_be_sure_on_average():
    # ouverte une fois au-dessus du seuil (0,29) puis vue longtemps à peine : écartée
    rows = [(f, BREAST, 0.35 if f % 5 == 0 else 0.18, [0.2, 0.3, 0.3, 0.42]) for f in range(60)]
    assert T.build(det(rows), 25.0, 100, T.Params()) == []
    assert len(T.build(det(moving(0, 59, score=0.4, dx=0)), 25.0, 100, T.Params())) == 1


def test_fast_mode_needs_fewer_frames():
    rows = [r for r in moving(0, 9, score=0.7) if r[0] % 2 == 0]  # mode rapide : une image sur deux
    assert T.build(det(rows), 25.0, 100, T.Params()) == []
    assert len(T.build(det(rows), 25.0, 100, T.Params(), step=2)) == 1


def test_male_chest_is_not_a_breast():
    MB = T.LABELS.index("MALE_BREAST_EXPOSED")
    pecs = moving(0, 30, score=0.6) + moving(0, 30, cls=MB, score=0.55)  # même endroit, presque aussi sûr
    assert T.build(det(pecs), 25.0, 100, T.Params()) == []
    breast = moving(0, 30, score=0.7) + moving(0, 30, cls=MB, score=0.3)  # le torse masculin est bien moins sûr
    assert len(T.build(det(breast), 25.0, 100, T.Params())) == 1


def test_brief_zone_kept_when_nudity_is_on_screen():
    brief = moving(20, 23, cls=MALE, score=0.6, x0=0.6, dx=0)  # 4 images seulement
    assert T.build(det(brief), 25.0, 100, T.Params()) == []
    both = T.build(det(brief + moving(0, 40, score=0.7)), 25.0, 100, T.Params())
    assert sorted(t.cls for t in both) == ["FEMALE_BREAST_EXPOSED", "MALE_GENITALIA_EXPOSED"]


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


def test_blur_looks_the_same_in_preview_and_4k():
    """Le flou dépend de la taille de la zone, pas des pixels : l'aperçu (réduit) montre ce que donnera l'export 4K,
    et l'intensité agit aussi en 4K (réglée en pixels, elle n'y changeait presque rien)."""
    yy, xx = np.mgrid[0:1200, 0:1200]
    big = np.repeat(((np.sin(xx / 40.0) * np.cos(yy / 55.0) + 1) * 127).astype(np.uint8)[..., None], 3, 2)
    small = np.asarray(Image.fromarray(big).resize((400, 400), Image.BOX))
    box = [np.array([0, 0, 1, 1], np.float32)]
    for strength in (0, 70, 100):
        b, s = big.copy(), small.copy()
        effects.apply(effects.rgb_planes(b), box, "blur", "rect", strength)
        effects.apply(effects.rgb_planes(s), box, "blur", "rect", strength)
        b_small = np.asarray(Image.fromarray(b).resize((400, 400), Image.BOX)).astype(int)
        assert np.abs(b_small - s.astype(int)).mean() < 6, strength
    weak, strong = big.copy(), big.copy()
    effects.apply(effects.rgb_planes(weak), box, "blur", "rect", 0)
    effects.apply(effects.rgb_planes(strong), box, "blur", "rect", 100)
    assert strong.std() < weak.std() * 0.6  # « Fort » efface bien plus que « Léger »


@pytest.mark.ffmpeg
def test_zoomed_sample_shows_the_zone(tmp_path, ffmpeg_bin):
    import io as _io
    import subprocess
    src = tmp_path / "src.mp4"
    subprocess.run([ffmpeg_bin, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=1", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(src)], check=True)
    video = P.video_info(src)
    box = np.array([0.4, 0.4, 0.6, 0.7], np.float32)
    jpg = render.sample(src, video, 25.0, 5, [box], style="black", shape="rect", strength=70)
    img = np.asarray(Image.open(_io.BytesIO(jpg)).convert("RGB"))
    assert max(img.shape[:2]) <= 360 and img.shape[0] > 50
    h, w = img.shape[:2]
    assert img[h // 2 - 5:h // 2 + 5, w // 2 - 5:w // 2 + 5].mean() < 25  # la zone (noire) est au centre du gros plan
    plain = render.sample(src, video, 25.0, 5, [], style="black", shape="rect", strength=70)
    assert Image.open(_io.BytesIO(plain)).width == 360  # sans zone : l'image entière en petit


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


@pytest.mark.ffmpeg
def test_export_mkv_keeps_tracks_and_interleaves_audio(tmp_path, ffmpeg_bin):
    """MKV à deux pistes son + sous-titres : tout est gardé avec ses réglages, et le son reste à côté de l'image
    dans le fichier (écrit en avance, les lecteurs n'avaient plus de son)."""
    import json
    import subprocess
    from medialab.components import require
    src = tmp_path / "src.mkv"
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nBonjour\n", encoding="utf-8")
    subprocess.run([ffmpeg_bin, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x120:r=25:d=40", "-f", "lavfi",
                    "-i", "sine=f=440:d=40", "-f", "lavfi", "-i", "sine=f=660:d=40", "-i", str(srt),
                    "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "ac3", "-c:s", "srt", "-metadata:s:a:1", "language=fre", "-disposition:a:0", "default",
                    "-disposition:a:1", "0", "-disposition:s:0", "0", str(src)], check=True)
    video = P.video_info(src)
    dst = tmp_path / "out.mkv"
    res = render.export(src, video, T.FrameBoxes([], []), 25.0, 1000, dst, style="blur", shape="rect", strength=70,
                        cancel=threading.Event(), progress=lambda *a: None, use_gpu=False)
    assert res["frames"] == 1000 and not list(tmp_path.glob("*.video.*")) and not list(tmp_path.glob("*.part.*"))
    probe = require("ffprobe")
    info = json.loads(subprocess.run([probe, "-v", "error", "-show_streams", "-of", "json", str(dst)],
                                     capture_output=True, check=True).stdout)["streams"]
    assert [s["codec_type"] for s in info] == ["video", "audio", "audio", "subtitle"]
    assert [s["disposition"]["default"] for s in info] == [1, 1, 0, 0]
    assert info[2]["tags"]["language"] == "fre"
    out = subprocess.run([probe, "-v", "error", "-show_entries", "packet=stream_index,pts_time,pos", "-of", "csv=p=0",
                          str(dst)], capture_output=True, text=True, check=True).stdout
    pk = sorted((int(p), int(s), float(t)) for s, t, p in (ln.split(",")[:3] for ln in out.split())
                if t != "N/A" and p != "N/A")
    last_v = 0.0
    worst = 0.0
    for _, s, t in pk:
        if s == 0:
            last_v = t
        elif s in (1, 2):
            worst = max(worst, t - last_v)
    assert worst < 2.0, f"son écrit {worst:.1f} s devant l'image"


# ------------------------------------------------------------------ suivi visuel
def _scene(rng, x, y, tex, h=360, w=640):
    g = np.full((h, w, 3), 110, np.uint8)
    g[y:y + tex.shape[0], x:x + tex.shape[1]] = tex
    return g


def test_ncc_finds_the_patch():
    from medialab.censor import follow as FW
    rng = np.random.default_rng(3)
    region = rng.uniform(0, 255, (80, 120)).astype(np.float32)
    tmpl = region[30:50, 70:95].copy()
    res = FW.ncc(region, tmpl)
    y, x = divmod(int(res.argmax()), res.shape[1])
    assert (x, y) == (70, 30) and res.max() > 0.99
    assert FW.ncc(region, np.full((10, 10), 7, np.float32)) is None  # zone unie : pas de suivi


def test_follower_tracks_a_moving_zone():
    from medialab.censor import follow as FW
    rng = np.random.default_rng(4)
    tex = rng.integers(0, 255, (60, 50, 3), np.uint8)
    fw = FW.Follower(FW.gray(_scene(rng, 100, 100, tex)), [100 / 640, 100 / 360, 150 / 640, 160 / 360])
    for i in range(1, 40):
        box, score = fw.track(FW.gray(_scene(rng, 100 + 5 * i, 100 + 2 * i, tex)))
    assert abs(box[0] * 640 - (100 + 5 * 39)) <= 2 and abs(box[1] * 360 - (100 + 2 * 39)) <= 2 and score > 0.8


def test_watch_follows_a_zone_the_detector_lost():
    from medialab.censor import detect
    rng = np.random.default_rng(5)
    tex = rng.integers(0, 255, (60, 50, 3), np.uint8)
    w = detect.Watch(keep_frames=10, step=1)
    box = [100 / 640, 100 / 360, 150 / 640, 160 / 360]
    assert w.step(0, _scene(rng, 100, 100, tex), [(BREAST, 0.7, box)]) == []
    got = [w.step(i, _scene(rng, 100 + 4 * i, 100, tex), []) for i in range(1, 15)]
    assert all(len(g) == 1 for g in got[:10])           # suivie à l'image pendant 10 images
    assert all(len(g) == 0 for g in got[10:])           # puis abandonnée (au-delà de keep_frames)
    assert abs(got[5][0][2][0] * 640 - (100 + 4 * 6)) <= 2


def test_tracked_rows_extend_but_never_open_a_zone():
    real = moving(0, 10, dx=0)
    tracked = [(f, BREAST, 0.9, [0.2, 0.3, 0.3, 0.42]) for f in range(11, 40)]
    d = det(real + tracked)
    d["t"] = np.array([0] * len(real) + [1] * len(tracked), np.int8)
    tr = T.build(d, 25.0, 100, T.Params(pad_s=0, smooth=1))
    assert len(tr) == 1 and tr[0].f1 == 39 and tr[0].hits == 11   # prolongée jusqu'à 39, 11 vraies détections
    only = det(tracked)
    only["t"] = np.ones(len(tracked), np.int8)
    assert T.build(only, 25.0, 100, T.Params()) == []               # le suivi seul n'ouvre jamais de zone


@pytest.mark.ffmpeg
def test_manual_zone_follows_on_a_real_video(tmp_path, ffmpeg_bin):
    """Un motif texturé traverse l'image : la zone tracée sur l'image 0 le suit toute seule."""
    import subprocess
    from medialab.censor import pipeline
    src = tmp_path / "move.mp4"
    subprocess.run([ffmpeg_bin, "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=640x360:r=25:d=3",
                    "-f", "lavfi", "-i", "testsrc2=s=80x80:r=25:d=3",
                    "-filter_complex", "[0][1]overlay=x='60+t*120':y=140:shortest=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)
    p = P.create(str(src))
    p = P.patch(p["id"], {"video": P.video_info(src)})
    box = [60 / 640, 140 / 360, 140 / 640, 220 / 360]
    res = pipeline.follow_zone(p, 0, box, seconds=2.0)
    assert res["reason"] == "limit" and res["seconds"] == pytest.approx(2.0, abs=0.1)
    end = res["keys"][-1]
    x_expected = (60 + 2.0 * 120) / 640
    assert abs(end["box"][0] - x_expected) < 0.02 and end["f"] == 50
    P.delete(p["id"])


def test_fixed_manual_zone_is_kept_as_drawn(tmp_path):
    """Une zone posée à la main « fixe » garde son drapeau (pas de suivi) ; une zone qui suit n'en a pas."""
    from fastapi.testclient import TestClient
    from medialab.app import Services, create_app
    from medialab.censor import detect
    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\0")
    p = P.create(str(src))
    P.patch(p["id"], {"status": "review", "video": {"fps": 25.0, "duration": 4.0, "width": 320, "height": 240}})
    detect._save(P.cache_dir(p["id"]), [], {"v": detect.DETECT_VERSION, "fps": 25.0, "frames": 100, "step": 1,
                                           "done": True})
    box = [0.1, 0.1, 0.3, 0.3]
    manual = [{"id": "a", "fixed": True, "keys": [{"f": 10, "box": box}, {"f": 60, "box": box}]},
              {"id": "b", "keys": [{"f": 0, "box": box}, {"f": 5, "box": [0.2, 0.1, 0.4, 0.3]}]}]
    with TestClient(create_app(Services(token="t"))) as c:
        r = c.put(f"/api/censor/projects/{p['id']}/manual", json={"manual": manual}, headers={"X-MediaLab-Token": "t"})
    assert r.status_code == 200, r.text
    saved = P.load(p["id"])["manual"]
    assert saved[0]["fixed"] is True and "fixed" not in saved[1]
    assert T.manual_box(saved[0], 35).tolist() == pytest.approx(box)   # même cadre du début à la fin
    assert saved[0]["keys"][-1]["f"] == 60
    P.delete(p["id"])


# ------------------------------------------------------------------ 2e passe : vérification, plans, tuiles
def test_verify_threshold_follows_sensitivity():
    assert T.verify_threshold(65) == 0.2 and T.verify_threshold(0) == 0.3 and T.verify_threshold(100) == 0.1


def test_zone_judged_not_nude_is_dropped():
    rows = moving(0, 30, score=0.7)
    d = det(rows)
    d["v"] = np.full(len(rows), 0.08, np.float16)          # CLIP : un torse, un verre…
    assert T.build(d, 25.0, 100, T.Params()) == []
    assert len(T.build(d, 25.0, 100, T.Params(verify=False))) == 1   # vérification coupée : floutée
    d["v"][:] = 0.45                                        # CLIP : nudité
    assert len(T.build(d, 25.0, 100, T.Params())) == 1
    d["v"][:] = np.nan                                      # pas vérifiée (ancienne analyse) : comme avant
    assert len(T.build(d, 25.0, 100, T.Params())) == 1


def test_zone_never_crosses_a_shot_change():
    d = det(moving(0, 60, score=0.7, dx=0))
    d["v"] = np.full(61, np.nan, np.float16)
    d["cuts"] = np.array([30], np.int32)
    tracks = T.build(d, 25.0, 100, T.Params(pad_s=0.4, smooth=1))
    assert [(t.f0, t.f1) for t in tracks] == [(0, 29), (30, 60 + 10)]  # marges coupées au plan, pas au-delà


def test_second_pass_plan_and_tiles():
    from medialab.censor import refine as R
    d = det([(100, BREAST, 0.5, [0.1, 0.1, 0.2, 0.2]), (101, BREAST, 0.1, [0.1, 0.1, 0.2, 0.2])])
    d["t"] = np.zeros(2, np.int8)
    fine, segs = R.plan(d, 1000, 25.0)
    assert fine[100 - 12] and fine[100 + 12] and not fine[100 + 14]   # tuiles ±0,5 s
    assert segs == [(50, 150)]                                         # plans cherchés ±2 s
    t = R.tiles(960, 400)
    assert t == [(0, 0, 640, 400), (320, 0, 960, 400)]                 # deux tuiles qui se chevauchent de moitié
    assert R.tiles(640, 360) == [(0, 0, 640, 360)]
    assert len(R.tiles(960, 720)) == 4


def test_shot_change_detector():
    from medialab.censor import refine as R
    yy, xx = np.mgrid[0:120, 0:160]

    def scene(shift, phase):  # image lisse (comme un vrai plan) qui glisse de ``shift`` px
        g = 127 + 100 * np.sin((xx + shift) / 9 + phase) * np.cos(yy / 13)
        return np.repeat(g[..., None], 3, 2).astype(np.uint8)
    cuts = R.Cuts()
    seq = [scene(2 * k, 0.0) for k in range(10)] + [255 - scene(40, 1.0) // 2 for _ in range(5)]  # autre plan
    hits = [k for k, im in enumerate(seq) if cuts.step(im)]
    assert hits == [10]                                                # le mouvement n'est pas un changement de plan


@pytest.mark.ffmpeg
def test_second_pass_on_a_real_video(tmp_path, ffmpeg_bin, monkeypatch):
    """2e passe complète (détecteur et CLIP remplacés) : tuiles fusionnées, notes, changement de plan, sauvegarde."""
    import subprocess
    import threading
    from medialab.censor import detect as D, refine as R, verify as V
    src = tmp_path / "cut.mp4"
    subprocess.run([ffmpeg_bin, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x266:r=25:d=2",
                    "-f", "lavfi", "-i", "color=c=navy:s=640x266:r=25:d=2", "-filter_complex", "[0][1]concat=n=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)], check=True)

    class FakeDetector:
        def __init__(self, *a, **k): pass
        def run(self, rgb, mirror=False):
            return [(BREAST, 0.6, [0.4, 0.4, 0.6, 0.6])]               # au milieu de chaque tuile

    class FakeVerifier:
        def __init__(self, *a, **k): pass
        def frame(self, rgb, boxes):
            return [0.05 for _ in boxes]                               # rien de nu

    monkeypatch.setattr(D, "Detector", FakeDetector)
    monkeypatch.setattr(V, "Verifier", FakeVerifier)
    rows = [(f, BREAST, 0.6, [0.45, 0.4, 0.55, 0.6]) for f in range(10, 40)]
    d = det(rows)
    d["t"] = np.zeros(len(rows), np.int8)
    meta = {"v": D.DETECT_VERSION, "fps": 25.0, "frames": 100, "step": 1, "done": True}
    cache = tmp_path / "c"
    cache.mkdir()
    out, meta2 = R.run(src, {"width": 640, "height": 266}, cache, d, meta, cancel=threading.Event(),
                       progress=lambda *a: None)
    assert meta2["refined"] == R.REFINE_VERSION
    assert 50 in out["cuts"].tolist()                                  # le plan change à 2 s
    assert np.isfinite(out["v"].astype(np.float32)).sum() > 0
    loaded, m = D.load(cache)
    assert m["refined"] == R.REFINE_VERSION and len(loaded["v"]) == len(loaded["f"])
    assert T.build(loaded, 25.0, 100, T.Params()) == []                # jugé non nu : rien à flouter
    assert len(T.build(loaded, 25.0, 100, T.Params(verify=False))) >= 1
