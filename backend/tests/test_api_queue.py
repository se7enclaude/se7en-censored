import time

import pytest
from fastapi.testclient import TestClient

from medialab.app import Services, create_app
from medialab.errors import MediaLabError

TOKEN = "test-token"
H = {"X-MediaLab-Token": TOKEN}


def fake_worker(job, ctx):
    opts = job["options"]
    steps = int(opts.get("steps", 5))
    if opts.get("fail_once") and ctx.attempt == 0:
        raise MediaLabError("network", "Network error while contacting the platform.", "retry", True)
    if opts.get("fail_always"):
        raise MediaLabError("private", "This media is private.", "Only the owner can access it.", False)
    for i in range(steps):
        ctx.progress({"stage": "downloading", "percent": (i + 1) / steps * 100, "speed": 1000.0})
        time.sleep(float(opts.get("delay", 0.05)))
    return {"file_path": __file__, "title": job.get("title"), "size": 123, "format": "MP4"}


@pytest.fixture(scope="module")
def client():
    services = Services(token=TOKEN)
    services.jobs.register_worker("fake", fake_worker, lambda: 2)
    import medialab.jobs.manager as m
    orig = m.JobManager._fallback_for
    m.JobManager._fallback_for = lambda self, job, code, attempt: (
        {"label": "Retrying"} if job["kind"] == "fake" and attempt == 0 else orig(self, job, code, attempt))
    with TestClient(create_app(services)) as c:
        c.services = services
        yield c
    m.JobManager._fallback_for = orig


def wait_status(client, job_id, statuses, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(f"/api/jobs/{job_id}", headers=H)
        assert r.status_code == 200, r.text
        j = r.json()
        if j["status"] in statuses:
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} stuck in {j['status']}")


def test_health_is_public_but_api_requires_token(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/settings").status_code == 401
    assert client.get("/api/settings", headers={"X-MediaLab-Token": "wrong"}).status_code == 401
    assert client.get("/api/settings", headers=H).status_code == 200
    assert client.get(f"/api/settings?token={TOKEN}").status_code == 200


def test_foreign_host_is_rejected(client):
    r = client.get("/api/settings", headers={**H, "host": "evil.example.com"})
    assert r.status_code == 403


def test_settings_patch_and_validation(client):
    r = client.patch("/api/settings", headers=H, json={"download": {"concurrent_downloads": 5}})
    assert r.status_code == 200 and r.json()["download"]["concurrent_downloads"] == 5
    bad = client.patch("/api/settings", headers=H, json={"download": {"concurrent_downloads": 999}})
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_setting"


def test_presets_crud_and_seed(client):
    presets = client.get("/api/presets", headers=H).json()["presets"]
    assert any(p["name"] == "My YouTube Preset" for p in presets)
    r = client.post("/api/presets", headers=H, json={"name": "T", "platforms": ["tiktok"],
                                                     "options": {"quality": "720", "junk": 1}})
    p = r.json()
    assert p["options"] == {"quality": "720"}
    assert client.delete(f"/api/presets/{p['id']}", headers=H).json()["ok"]


def test_detect_and_extract(client):
    d = client.post("/api/detect", headers=H, json={"url": "https://www.youtube.com/shorts/abcdefghijk"}).json()
    assert d["platform"] == "youtube" and d["platform_name"] == "YouTube"
    d = client.post("/api/detect", headers=H, json={"url": "https://x.com/someone/status/12345"}).json()
    assert d["platform"] == "twitter" and d["platform_name"] == "X"
    d = client.post("/api/detect", headers=H, json={"url": "https://example.com/pic.jpg"}).json()
    assert d["provider"] == "direct"
    r = client.post("/api/extract-urls", headers=H, json={"text": "a https://vimeo.com/76979871 b https://www.tiktok.com/@a/video/1"})
    assert [u["platform"] for u in r.json()["urls"]] == ["vimeo", "tiktok"]


def test_job_lifecycle_and_history(client):
    job = client.services.jobs.add("fake", "https://example.com/v", {"steps": 3}, title="Video A")
    done = wait_status(client, job["id"], ["completed", "failed"])
    assert done["status"] == "completed" and done["result"]["size"] == 123
    hist = client.get("/api/history", headers=H).json()["items"]
    assert any(h["title"] == "Video A" for h in hist)


def test_pause_resume_cancel(client):
    job = client.services.jobs.add("fake", "u", {"steps": 200, "delay": 0.02}, title="Long")
    wait_status(client, job["id"], ["downloading"])
    assert client.post(f"/api/jobs/{job['id']}/pause", headers=H).status_code == 200
    wait_status(client, job["id"], ["paused"])
    client.post(f"/api/jobs/{job['id']}/resume", headers=H)
    wait_status(client, job["id"], ["downloading"])
    client.post(f"/api/jobs/{job['id']}/cancel", headers=H)
    j = wait_status(client, job["id"], ["canceled"])
    assert j["status"] == "canceled"


def test_auto_retry_then_success(client):
    job = client.services.jobs.add("fake", "u", {"fail_once": True, "steps": 2}, title="Flaky")
    j = wait_status(client, job["id"], ["completed", "failed"], timeout=20)
    assert j["status"] == "completed" and j["attempts"] == 1


def test_non_retryable_failure_is_explained(client):
    job = client.services.jobs.add("fake", "u", {"fail_always": True}, title="Private")
    j = wait_status(client, job["id"], ["failed"])
    assert j["error"]["code"] == "private" and j["error"]["hint"]
    # Retry keeps the task and puts it back in the queue.
    client.post(f"/api/jobs/{job['id']}/retry", headers=H)
    wait_status(client, job["id"], ["failed"])


def test_priority_and_remove(client):
    client.services.jobs.bulk("pause_all")
    a = client.services.jobs.add("fake", "u", {}, title="A", start_paused=True)
    b = client.services.jobs.add("fake", "u", {}, title="B", start_paused=True)
    client.post(f"/api/jobs/{b['id']}/priority", headers=H, json={"value": 5})
    ids = [j["id"] for j in client.get("/api/jobs", headers=H).json()["jobs"]]
    assert ids.index(b["id"]) < ids.index(a["id"])
    client.post(f"/api/jobs/{a['id']}/remove", headers=H)
    ids = [j["id"] for j in client.get("/api/jobs", headers=H).json()["jobs"]]
    assert a["id"] not in ids


def test_upscale_capabilities_always_offer_classic(client):
    caps = client.get("/api/upscale/capabilities", headers=H).json()
    classic = next(m for m in caps["models"] if m["id"] == "classic-lanczos")
    assert classic["usable"]
    assert caps["enhancements"]["deblur"]["available"] is False  # never claimed without a model


def test_classic_image_upscale_end_to_end(client, tmp_path):
    from PIL import Image
    src = tmp_path / "small.png"
    Image.new("RGB", (64, 48), (200, 30, 30)).save(src)
    r = client.post("/api/upscale/jobs", headers=H, json={"paths": [str(src)],
                                                         "options": {"model": "classic-lanczos", "scale": 4, "sharpen": 1}})
    job = r.json()["jobs"][0]
    j = wait_status(client, job["id"], ["completed", "failed"])
    assert j["status"] == "completed", j.get("error")
    with Image.open(j["result"]["output_path"]) as im:
        assert im.size == (256, 192)


def test_websocket_requires_token(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/api/ws?token=nope") as ws:
            ws.receive_json()
    with client.websocket_connect(f"/api/ws?token={TOKEN}") as ws:
        assert ws.receive_json()["type"] == "hello"
