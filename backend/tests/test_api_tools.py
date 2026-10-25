"""HTTP API of the 1.1 tools: converter, cutter, player, duplicates, stats, shell open, updates."""
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from medialab.app import Services, create_app

TOKEN = "tools-token"
H = {"X-MediaLab-Token": TOKEN}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    from conftest import FFMPEG
    if not FFMPEG:
        pytest.skip("FFmpeg not available")
    d = tmp_path_factory.mktemp("api-media")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=4",
                    "-f", "lavfi", "-i", "sine=duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(d / "clip.mkv")], check=True)
    services = Services(token=TOKEN)
    services.settings.update({"advanced": {"ffmpeg_path": FFMPEG}})
    with TestClient(create_app(services)) as c:
        yield c, d


def wait_job(c, job_id, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/api/jobs/{job_id}", headers=H).json()
        if j["status"] in ("completed", "failed", "canceled"):
            return j
        time.sleep(0.1)
    raise AssertionError(f"job stuck: {j['status']}")


def test_catalog_and_probe(env):
    c, d = env
    cat = c.get("/api/convert/catalog", headers=H).json()
    ids = {t["id"] for t in cat["targets"]}
    assert {"mp4", "mp3", "png", "srt", "gif", "heic", "pdf"} <= ids
    files = c.post("/api/files/probe", headers=H, json={"paths": [str(d / "clip.mkv"), str(d / "nope.mp4")]}).json()["files"]
    assert files[0]["ok"] and files[0]["kind"] == "video" and files[0]["vcodec"] == "h264" and files[0]["has_audio"]
    assert not files[1]["ok"]


def test_convert_job_goes_through_the_queue_and_history(env):
    c, d = env
    r = c.post("/api/convert/jobs", headers=H, json={"paths": [str(d / "clip.mkv")], "target": "mp3", "options": {}}).json()
    j = wait_job(c, r["jobs"][0]["id"])
    assert j["status"] == "completed", j.get("error")
    assert Path(j["result"]["file_path"]).suffix == ".mp3"
    hist = c.get("/api/history?kind=convert", headers=H).json()["items"]
    assert hist and hist[0]["kind"] == "convert"
    assert c.post("/api/convert/jobs", headers=H, json={"paths": [], "target": "nope"}).status_code == 422


def test_trim_job(env):
    c, d = env
    r = c.post("/api/trim/jobs", headers=H, json={"path": str(d / "clip.mkv"), "segments": [{"start": 1, "end": 2.5}],
                                                  "options": {"mode": "precise"}}).json()
    j = wait_job(c, r["jobs"][0]["id"])
    assert j["status"] == "completed", j.get("error")
    assert abs(j["result"]["duration"] - 1.5) < 0.01


def test_player_endpoints(env):
    c, d = env
    plan = c.get("/api/media/plan", headers=H, params={"path": str(d / "clip.mkv")}).json()
    assert plan["mode"] == "remux" and plan["duration"] > 3
    with c.stream("GET", "/api/media/stream", headers=H, params={"path": str(d / "clip.mkv"), "mode": "remux", "t": 1}) as r:
        assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
        first = next(r.iter_bytes())
        assert b"ftyp" in first[:64]
    assert c.post("/api/media/stop", headers=H).json()["ok"]
    wf = c.get("/api/media/waveform", headers=H, params={"path": str(d / "clip.mkv"), "buckets": 200}).json()
    assert len(wf["peaks"]) >= 190
    fs = c.get("/api/media/filmstrip", headers=H, params={"path": str(d / "clip.mkv"), "count": 4}).json()
    assert len(fs["frames"]) == 4 and c.get(f"/api/thumb/{fs['frames'][0]['key']}", headers=H).status_code == 200
    # direct files support byte ranges (instant seeking)
    r = c.get("/api/media", headers={**H, "Range": "bytes=0-99"}, params={"path": str(d / "clip.mkv")})
    assert r.status_code == 206 and len(r.content) == 100


def test_duplicates_task_and_trash(env):
    c, d = env
    lib = d / "lib"
    lib.mkdir()
    shutil.copy(d / "clip.mkv", lib / "a.mkv")
    shutil.copy(d / "clip.mkv", lib / "a (1).mkv")
    t = c.post("/api/duplicates/scan", headers=H, json={"folders": [str(lib)], "options": {"similar": False}}).json()
    for _ in range(100):
        st = c.get(f"/api/tasks/{t['id']}", headers=H).json()
        if st["state"] != "running":
            break
        time.sleep(0.05)
    assert st["state"] == "done"
    g = st["result"]["groups"][0]
    assert g["type"] == "exact" and [f["name"] for f in g["files"] if f["keep"]] == ["a.mkv"]
    assert c.post("/api/duplicates/scan", headers=H, json={"folders": [str(d / "missing")]}).status_code == 422


def test_stats_and_shell_open(env):
    c, d = env
    s = c.get("/api/stats?days=7", headers=H).json()
    assert s["total"] >= 2 and len(s["timeline"]) == 7
    r = c.post("/api/shell/open", headers=H, json={"paths": [str(d / "clip.mkv"), str(d / "nope.mp4")]}).json()
    assert r["count"] == 1


def test_update_endpoint_without_feed(env, monkeypatch):
    c, _ = env
    monkeypatch.delenv("SE7EN_UPDATE_URL", raising=False)
    st = c.post("/api/update/check", headers=H).json()
    assert st["state"] in ("unconfigured", "uptodate", "available", "error") and st["current"]
