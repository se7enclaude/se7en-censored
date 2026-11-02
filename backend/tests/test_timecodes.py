"""Lecture des timecodes : formats courants, séparateurs, millisecondes, images, lignes ignorées avec un message."""
import pytest

from medialab.censor import timecodes as TC


def one(line: str, **kw) -> tuple[float, float]:
    res = TC.parse(line, **{"fps": 25.0, **kw})
    assert len(res["ranges"]) == 1, res
    r = res["ranges"][0]
    return r["start"], r["end"]


@pytest.mark.parametrize("line,expected", [
    ("00:12:34 - 00:13:10", (754, 790)),
    ("12:34–13:10", (754, 790)),
    ("12:34 — 13:10", (754, 790)),
    ("de 12:34 à 13:10", (754, 790)),
    ("12:34 to 13:10", (754, 790)),
    ("00:12:34 -> 00:13:10", (754, 790)),
    ("00:12:34 → 00:13:10", (754, 790)),
    ("00:12:34,00:13:10", (754, 790)),                 # CSV, virgule
    ("00:12:34;00:13:10", (754, 790)),                 # CSV, point-virgule (Excel en français)
    ("00:12:34\t00:13:10", (754, 790)),                # colonnes séparées par une tabulation
    ("00:01:02,500 --> 00:01:05,000", (62.5, 65.0)),   # SRT
    ("1:02:03.500 - 1:02:10.25", (3723.5, 3730.25)),   # millisecondes
    ("75:20 - 76:00", (4520, 4560)),                   # minutes au-delà de 59 sans heures
    ("01:05:10:12 - 01:05:20:00", (3910.48, 3920.0)),  # HH:MM:SS:images (25 i/s)
    ("01:05:10;12 - 01:05:20;00", (3910.48, 3920.0)),  # drop-frame
    ("1h02m03s - 1h02m30s", (3723, 3750)),
    ("12m34s - 13m10", (754, 790)),
    ("45 min 10 s - 46 min", (2710, 2760)),
    ("754s - 790s", (754, 790)),
    ("Scène 2 : 00:12:34 - 00:13:10 (cuisine)", (754, 790)),
])
def test_ranges(line, expected):
    assert one(line) == pytest.approx(expected, abs=1e-3)


def test_single_timecode_is_a_point_with_margin():
    assert one("54:34") == (3272, 3276)
    assert one("54:34", margin=5) == (3269, 3279)
    assert one("00:00:01", margin=3) == (0, 4)                # pas avant le début
    assert TC.parse("54:34")["ranges"][0]["point"] is True
    assert TC.parse("54:34 - 54:34")["ranges"][0]["point"] is True


def test_frames_use_the_video_rate():
    assert one("00:00:01:12", fps=24.0, margin=0) == pytest.approx((1.5, 1.5))
    res = TC.parse("00:00:01:30", fps=25.0)                   # image 30 impossible à 25 i/s
    assert not res["ranges"] and "image 30" in res["skipped"][0]["reason"]


def test_invalid_lines_are_skipped_with_a_reason():
    text = "\n".join([
        "Passages à couper",            # 1 : pas de timecode
        "00:12:34 - 00:13:10",          # 2 : ok
        "00:75:10",                     # 3 : minutes impossibles
        "13:10 - 12:34",                # 4 : fin avant le début
        "",                             # 5 : vide, ignorée sans message
        "02:00:00 - 02:01:00",          # 6 : après la fin de la vidéo
        "00:59:00 - 01:10:00",          # 7 : fin ramenée à la fin
        "12:00 - 12:30 - 13:00",        # 8 : trois timecodes
    ])
    res = TC.parse(text, fps=25.0, duration=3600.0)
    assert [r["line"] for r in res["ranges"]] == [2, 7, 8]
    assert res["ranges"][1]["end"] == 3600 and "fin ramenée" in res["ranges"][1]["note"]
    assert "deux premiers" in res["ranges"][2]["note"] and res["ranges"][2]["end"] == 750
    reasons = {s["line"]: s["reason"] for s in res["skipped"]}
    assert set(reasons) == {1, 3, 4, 6}
    assert reasons[1] == "pas de timecode"
    assert "59" in reasons[3]
    assert "avant le début" in reasons[4]
    assert "après la fin" in reasons[6]


def test_srt_file_keeps_only_the_timings():
    srt = ("1\n00:00:01,000 --> 00:00:02,500\nBonjour\n\n2\n00:10:00,000 --> 00:10:04,000\nUne réplique 12 fois\n"
           "\n")
    res = TC.parse(srt, fps=25.0)
    assert [(r["start"], r["end"]) for r in res["ranges"]] == [(1.0, 2.5), (600.0, 604.0)]
    assert res["skipped"] == []                               # numéros et répliques : rien à signaler


def test_csv_with_header():
    csv = "debut;fin;note\n00:12:34;00:13:10;douche\n01:02:03;01:02:30;\n"
    res = TC.parse(csv, fps=25.0)
    assert [(r["start"], r["end"]) for r in res["ranges"]] == [(754, 790), (3723, 3750)]
    assert [s["line"] for s in res["skipped"]] == [1]


def test_ocr_mistakes_are_fixed():
    res = TC.parse("0O:l2:34 - OO:13:1O\nNote : 2 fois", fps=25.0, ocr=True)
    assert [(r["start"], r["end"]) for r in res["ranges"]] == [(754, 790)]


def test_plain_numbers_are_not_timecodes():
    for line in ("Scène 2", "3 mars", "Il y a 12 personnes", "version 1.2.0"):
        res = TC.parse(line, fps=25.0)
        assert res["ranges"] == [], line


def test_to_frames_and_clock():
    assert TC.to_frames(754, 790, 25.0) == (18850, 19749)
    assert TC.to_frames(1.5, 1.5, 24.0) == (36, 36)
    assert TC.to_frames(0, 10, 25.0, frames=100) == (0, 99)
    assert TC.clock(3723.5) == "1:02:03.500" and TC.clock(754) == "12:34"


# ------------------------------------------------------------------ passages : rendu et API
import numpy as np  # noqa: E402

from medialab.censor import project as P, track as T  # noqa: E402


def _track(f0: int, f1: int) -> T.Track:
    return T.Track("t1", "FEMALE_BREAST_EXPOSED", f0, f1,
                   np.tile(np.array([0.2, 0.2, 0.4, 0.4], np.float32), (f1 - f0 + 1, 1)), 0.9, f1 - f0 + 1)


def test_full_frame_and_ai_only_segments():
    tracks = [_track(10, 20), _track(200, 210)]
    segs = [{"f0": 50, "f1": 60, "mode": "full"}, {"f0": 195, "f1": 205, "mode": "ai"}]
    fb = T.FrameBoxes(tracks, [], [], segs)
    assert fb.at(55)[0].tolist() == [0, 0, 1, 1]               # plein cadre
    assert len(fb.at(15)) == 1 and len(fb.at(202)) == 1       # zones de l'IA partout (par défaut)
    only = T.FrameBoxes(tracks, [], [], segs, ai_outside=False)
    assert only.at(15) == []                                  # hors des passages : plus de zones de l'IA
    assert len(only.at(202)) == 1 and only.at(207) == []      # dans le passage « IA » seulement
    assert only.at(55)[0].tolist() == [0, 0, 1, 1]
    cov = T.coverage(tracks, [], [], 25.0, 300, segs, ai_outside=False)
    assert cov["zones"] == 2 and cov["seconds"] == pytest.approx((11 + 6) / 25, abs=0.05)


def test_api_reads_timecodes_and_saves_segments(tmp_path):
    from fastapi.testclient import TestClient
    from medialab.app import Services, create_app
    from medialab.censor import detect
    src = tmp_path / "film.mp4"
    src.write_bytes(b"\0")
    p = P.create(str(src))
    P.patch(p["id"], {"status": "review", "video": {"fps": 25.0, "duration": 120.0, "width": 320, "height": 240}})
    detect._save(P.cache_dir(p["id"]), [], {"v": detect.DETECT_VERSION, "fps": 25.0, "frames": 3000, "step": 1,
                                           "done": True})
    h = {"X-MediaLab-Token": "t"}
    with TestClient(create_app(Services(token="t"))) as c:
        r = c.post(f"/api/censor/projects/{p['id']}/timecodes", headers=h,
                   json={"text": "00:10 - 00:20\nrien ici\n01:00\n05:00 - 05:10", "margin": 1})
        assert r.status_code == 200, r.text
        res = r.json()
        assert [(x["f0"], x["f1"]) for x in res["ranges"]] == [(250, 499), (1475, 1524)]
        assert [s["line"] for s in res["skipped"]] == [2, 4]   # pas de timecode ; après la fin (2:00)
        segs = [{"id": "a", "f0": 250, "f1": 499, "mode": "full"}, {"id": "b", "f0": 1475, "f1": 9999, "mode": "ai"}]
        r = c.put(f"/api/censor/projects/{p['id']}/segments", headers=h, json={"segments": segs})
        assert r.status_code == 200, r.text
        saved = r.json()["segments"]
        assert saved[1]["f1"] == 2999                         # ramené à la fin de la vidéo
        assert r.json()["stats"]["seconds"] == pytest.approx(10.0, abs=0.05)
        z = c.get(f"/api/censor/projects/{p['id']}/zones", headers=h).json()
        assert [s["id"] for s in z["segments"]] == ["a", "b"]
    P.delete(p["id"])


def test_ocr_reads_timecodes_from_a_screenshot(ai_models):
    import io
    from PIL import Image, ImageDraw, ImageFont
    from medialab import ai_assets
    from medialab.censor import ocr
    if not ai_assets.path_of("ppocr_en_rec"):
        pytest.skip("modèle de lecture de texte absent")
    img = Image.new("RGB", (640, 200), (30, 30, 34))        # mode sombre : texte clair sur fond foncé
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype("arial.ttf", 24)
    for i, line in enumerate(["Scene 1 : 00:12:34 - 00:13:10", "1:02:03.500", "Note sans timecode"]):
        d.text((16, 16 + i * 56), line, fill=(230, 230, 230), font=f)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    lines = ocr.Reader().read(buf.getvalue())
    res = TC.parse("\n".join(lines), 25.0, ocr=True)
    assert [(r["start"], r["end"]) for r in res["ranges"]] == [(754, 790), (3721.5, 3725.5)], lines
    assert [s["reason"] for s in res["skipped"]] == ["pas de timecode"]
