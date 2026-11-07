"""se7en Censored 1.5 : copie d'aperçu (lecture dans l'appli, images numérotées comme l'analyse), zones validées et
passages à vérifier, composants « à jour », dernière vérification des mises à jour gardée."""
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from medialab.censor import frames as FR, project as P, proxy


def _probe(path: Path, *args: str) -> str:
    from medialab.components import require
    return subprocess.run([require("ffprobe"), "-v", "error", *args, str(path)], capture_output=True, text=True,
                          check=True).stdout.strip()


def _make(ffmpeg_bin: str, dst: Path, hdr: bool = False, seconds: int = 4) -> Path:
    base = [ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=640x360:r=25:d={seconds}",
            "-f", "lavfi", "-i", f"sine=f=440:d={seconds}"]
    if hdr:
        enc = ["-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-x265-params",
               "log-level=error:colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc", "-c:a", "ac3"]
    else:
        enc = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac"]
    subprocess.run([*base, *enc, "-shortest", str(dst)], check=True)
    return dst


def _edges(img: np.ndarray) -> np.ndarray:
    g = img.astype(np.float32).mean(2)
    e = np.abs(np.diff(g, axis=1))[:-1] + np.abs(np.diff(g, axis=0))[:, :-1]
    return (e - e.mean()) / (e.std() + 1e-6)


@pytest.mark.ffmpeg
def test_preview_copy_keeps_the_frame_numbers(tmp_path, ffmpeg_bin):
    src = _make(ffmpeg_bin, tmp_path / "film.mkv")
    assert proxy.needed(src)  # MKV : pas lisible tel quel par la fenêtre
    mp4 = _make(ffmpeg_bin, tmp_path / "ok.mp4")
    assert not proxy.needed(mp4)  # H.264 + AAC en MP4 : lu directement
    video = P.video_info(src)
    out = proxy.build(src, video, tmp_path / "preview.mp4", threading.Event())
    dst = Path(out["path"])
    assert _probe(dst, "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0") == "h264"
    assert _probe(dst, "-select_streams", "a:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0") == "aac"
    frames = int(_probe(dst, "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames",
                        "-of", "csv=p=0"))
    assert frames == 100
    # l'image n de la copie est l'image n de l'analyse (testsrc2 change à chaque image)
    for n in (0, 37, 99):
        a = _edges(FR.grab(src, 25.0, n, (320, 180)))
        scores = {d: float((a * _edges(FR.grab(dst, 25.0, n + d, (320, 180)))).mean()) for d in (-1, 0, 1) if 0 <= n + d < 100}
        assert max(scores, key=scores.get) == 0, (n, scores)


@pytest.mark.ffmpeg
def test_preview_copy_of_hdr_is_sdr(tmp_path, ffmpeg_bin):
    src = _make(ffmpeg_bin, tmp_path / "hdr.mkv", hdr=True, seconds=2)
    video = P.video_info(src)
    assert video["is_hdr"]
    out = proxy.build(src, video, tmp_path / "preview.mp4", threading.Event())
    st = _probe(Path(out["path"]), "-select_streams", "v:0", "-show_entries", "stream=pix_fmt,color_transfer",
                "-of", "csv=p=0")
    assert st.startswith("yuv420p") and "smpte2084" not in st


def _client():
    from fastapi.testclient import TestClient
    from medialab.app import Services, create_app
    return TestClient(create_app(Services(token="t")))


def test_ai_zones_are_validated_and_passages_kept_to_check(tmp_path):
    from medialab.censor import detect
    src = tmp_path / "film.mp4"
    src.write_bytes(b"\0")
    p = P.create(str(src))
    P.patch(p["id"], {"status": "review", "video": {"fps": 25.0, "duration": 20.0, "width": 320, "height": 240},
                      "preview": {"direct": True}})
    rows = [(f, 3, 0.9, [0.4, 0.4, 0.6, 0.6], 0) for f in range(100, 200)]
    detect._save(P.cache_dir(p["id"]), rows, {"v": detect.DETECT_VERSION, "fps": 25.0, "frames": 500, "step": 1,
                                             "done": True})
    h = {"X-MediaLab-Token": "t"}
    with _client() as c:
        z = c.get(f"/api/censor/projects/{p['id']}/zones", headers=h).json()
        assert len(z["tracks"]) == 1 and not z["tracks"][0]["validated"]
        tid = z["tracks"][0]["id"]
        r = c.post(f"/api/censor/projects/{p['id']}/validate", headers=h, json={"track": tid})
        assert r.status_code == 200, r.text
        assert c.get(f"/api/censor/projects/{p['id']}/zones", headers=h).json()["tracks"][0]["validated"]
        # valider deux fois ne double pas l'entrée
        c.post(f"/api/censor/projects/{p['id']}/validate", headers=h, json={"track": tid})
        assert len(P.load(p["id"])["validated"]) == 1
        c.post(f"/api/censor/projects/{p['id']}/unvalidate", headers=h, json={"track": tid})
        assert not c.get(f"/api/censor/projects/{p['id']}/zones", headers=h).json()["tracks"][0]["validated"]
        segs = [{"id": "a", "f0": 10, "f1": 40, "mode": "full", "validated": False},
                {"id": "b", "f0": 300, "f1": 320, "mode": "full", "validated": True}]
        saved = c.put(f"/api/censor/projects/{p['id']}/segments", headers=h, json={"segments": segs}).json()["segments"]
        assert [s["validated"] for s in saved] == [False, True]
        # projet lisible tel quel : pas de copie d'aperçu à préparer
        assert c.post(f"/api/censor/projects/{p['id']}/preview", headers=h).json() == {"preview": {"direct": True}}
    P.delete(p["id"])


def test_component_freshness(monkeypatch, data_dir):
    from medialab import components as C
    from medialab.config import paths
    latest = {"_checked": 1.0, "ytdlp": {"latest": "2026.10.26"}, "deno": {"latest": "2.5.0"}, "ffmpeg": {"latest": "9.1"}}
    monkeypatch.setattr(C, "_installed_ytdlp", lambda: "2026.10.21")
    monkeypatch.setattr(C, "ytdlp_version", lambda: "2026.10.21")
    f = C.freshness({"id": "ytdlp", "installed": True}, latest)
    assert f["update_available"] and f["latest"] == "2026.10.26" and not f["restart_required"]
    monkeypatch.setattr(C, "_installed_ytdlp", lambda: "2026.10.26")  # téléchargé, pas encore chargé
    f = C.freshness({"id": "ytdlp", "installed": True}, latest)
    assert f["restart_required"] and not f["update_available"]
    assert C.freshness({"id": "deno", "installed": True, "runtime": "node", "version": "24.1.0"}, latest)["managed"] == "system"
    f = C.freshness({"id": "deno", "installed": True, "runtime": "deno", "version": "2.5.0"}, latest)
    assert not f["update_available"] and not f["managed"]
    mine = str(paths().components / "ffmpeg" / "bin" / "ffmpeg.exe")
    assert C.freshness({"id": "ffmpeg", "installed": True, "path": mine, "version": "n9.0.2-14-g1"}, latest)["update_available"]
    assert not C.freshness({"id": "ffmpeg", "installed": True, "path": mine, "version": "n9.1-3-g2"}, latest)["update_available"]
    assert C.freshness({"id": "ffmpeg", "installed": True, "path": r"D:\outils\ffmpeg.exe", "version": "n7.0"}, latest)["managed"] == "custom"
    assert not C.freshness({"id": "aipack", "installed": True}, latest)["update_available"]


def test_last_update_check_is_remembered(monkeypatch, data_dir):
    from medialab import __version__, updater as U
    monkeypatch.setattr(U, "feed_url", lambda: "https://example.invalid/latest.json")
    U._save_last({"checked": time.time() - 3600, "latest": __version__})
    u = U.Updater()
    assert u.state["state"] == "uptodate" and u.state["checked"] and u.state["latest"] == __version__
    U._save_last({"checked": time.time(), "latest": "999.0.0"})  # une version plus récente : on revérifie
    assert U.Updater().state["state"] == "idle"
    monkeypatch.setattr(U, "feed_url", lambda: "")
    assert U.Updater().state["state"] == "unconfigured"
