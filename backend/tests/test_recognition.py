"""Reconnaissance : le détecteur et la 2e vérification se complètent, une zone est reprise depuis sa toute première
image, retrouvée après une courte perte, et un soutien-gorge n'est pas une poitrine nue."""
import numpy as np

from medialab.censor import track as T

BREAST = T.CLASS_IDS["FEMALE_BREAST_EXPOSED"]
MALE = T.CLASS_IDS["MALE_GENITALIA_EXPOSED"]
COVERED = T.LABELS.index("FEMALE_BREAST_COVERED")


def det(rows, note=None, cuts=()):
    d = {"f": np.array([r[0] for r in rows], np.int32), "c": np.array([r[1] for r in rows], np.int8),
         "s": np.array([r[2] for r in rows], np.float16), "b": np.array([r[3] for r in rows], np.float32)}
    if note is not None:
        d["v"] = np.array([note(r) if callable(note) else note for r in rows], np.float16)
        d["cuts"] = np.array(cuts, np.int32)
    return d


def still(f0, f1, cls=BREAST, score=0.6, x0=0.2, y0=0.3):
    return [(f, cls, score, [x0, y0, x0 + 0.1, y0 + 0.12]) for f in range(f0, f1 + 1)]


def test_weak_zone_confirmed_by_the_second_check_is_blurred():
    rows = still(0, 50, cls=MALE, score=0.21)  # photo fixe : le détecteur hésite, toujours à 0,21
    assert T.build(det(rows), 25.0, 100, T.Params()) == []                 # pas de 2e vérification : trop faible
    assert T.build(det(rows, 0.45), 25.0, 100, T.Params()) == []           # CLIP hésite aussi
    tracks = T.build(det(rows, 0.67), 25.0, 100, T.Params())               # CLIP est net : floutée
    assert len(tracks) == 1 and tracks[0].cls == "MALE_GENITALIA_EXPOSED"
    assert T.build(det(rows, 0.67), 25.0, 100, T.Params(verify=False)) == []


def test_fused_score_never_lowers_and_caps_at_one():
    s = np.array([0.2, 0.2, 0.2, 0.8], np.float32)
    out = T.fused(s, np.array([np.nan, 0.3, 0.7, 0.9], np.float32))
    assert out[0] == s[0] and out[1] == s[1] and abs(out[2] - 0.4) < 1e-6 and out[3] == 1.0


def test_zone_starts_at_its_first_faint_frame():
    rows = still(0, 9, score=0.2) + still(10, 40, score=0.6)  # vue faiblement avant d'être sûre
    t = T.build(det(rows), 25.0, 100, T.Params(pad_s=0.0, margin=0.0))
    assert len(t) == 1 and t[0].f0 == 0 and t[0].f1 == 40
    far = still(0, 9, score=0.2, x0=0.7) + still(10, 40, score=0.6)  # ailleurs dans l'image : pas la même zone
    t = T.build(det(far), 25.0, 100, T.Params(pad_s=0.0, margin=0.0))
    assert len(t) == 1 and t[0].f0 == 10


def test_zone_lost_a_moment_is_one_zone():
    rows = still(0, 20) + still(45, 65)  # perdue 1 s (main qui passe), retrouvée au même endroit
    t = T.build(det(rows, 0.5), 25.0, 200, T.Params(pad_s=0.0))
    assert len(t) == 1 and t[0].box_at(30) is not None
    t = T.build(det(rows, 0.5, cuts=[30]), 25.0, 200, T.Params(pad_s=0.0))  # changement de plan entre les deux
    assert len(t) == 2 and all(x.box_at(30) is None for x in t)
    t = T.build(det(still(0, 20) + still(100, 120), 0.5), 25.0, 200, T.Params(pad_s=0.0))  # 3 s : deux zones
    assert len(t) == 2


def test_bra_is_not_a_bare_breast():
    rows = still(0, 30, score=0.6) + still(0, 30, cls=COVERED, score=0.55)
    assert T.build(det(rows), 25.0, 100, T.Params()) == []
    rows = still(0, 30, score=0.7) + still(0, 30, cls=COVERED, score=0.3)  # « couverte » bien moins sûre : nue
    assert len(T.build(det(rows), 25.0, 100, T.Params())) == 1


def test_faint_zone_needs_a_confirmed_scene():
    faint = still(5, 25, cls=MALE, score=0.22, x0=0.6)
    note = lambda r: 0.5  # noqa: E731
    assert T.build(det(faint, note), 25.0, 100, T.Params()) == []          # seule à l'écran : écartée
    both = T.build(det(still(0, 40, score=0.7) + faint, note), 25.0, 100, T.Params())
    assert sorted(t.cls for t in both) == ["FEMALE_BREAST_EXPOSED", "MALE_GENITALIA_EXPOSED"]
    low = lambda r: 0.5 if r[1] == BREAST else 0.3  # noqa: E731
    assert len(T.build(det(still(0, 40, score=0.7) + faint, low), 25.0, 100, T.Params())) == 1


def test_two_zones_at_once_are_told_apart():
    """Retirer une fausse alerte ne doit pas retirer la vraie zone de la même classe, à l'écran au même moment."""
    rows = still(0, 40, score=0.7, x0=0.2) + still(0, 40, score=0.7, x0=0.6)
    a, b = T.build(det(rows), 25.0, 100, T.Params())
    assert T.hidden_match(a, [T.mark(a)]) and not T.hidden_match(b, [T.mark(a)])
    assert T.hidden_match(b, [{"cls": a.cls, "f0": a.f0, "f1": a.f1}])  # ancien choix, sans endroit : tout le moment
    wide = T.build(det(rows), 25.0, 100, T.Params(margin=0.6))           # la marge change : toujours la même zone
    assert [T.hidden_match(t, [T.mark(a)]) for t in wide] == [True, False]


def test_whole_video_analysis_is_not_undone_by_the_timecodes(tmp_path):
    """« Analyser toute la vidéo » : les timecodes donnés à la création ne sont pas réappliqués (ni dédoublés)."""
    from medialab.censor import pipeline, project as P
    src = tmp_path / "film.mp4"
    src.write_bytes(b"\0")
    p = P.create(str(src), None, None, "0:10 - 0:20", 2.0)
    video = {"fps": 25.0, "duration": 100.0, "width": 320, "height": 240}
    first = pipeline.apply_scope(p["id"], video)
    assert first["scope_only"] and len(first["segments"]) == 1
    P.patch(p["id"], {"scope_only": False})  # ce que fait « Analyser toute la vidéo »
    again = pipeline.apply_scope(p["id"], video)
    assert not again["scope_only"] and len(again["segments"]) == 1
    P.delete(p["id"])
