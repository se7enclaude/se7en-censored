"""Components: « à jour » vs « mise à jour disponible », no download when already up to date."""
import zipfile

import pytest

from medialab import component_updates as cu, components
from medialab.config import paths
from medialab.events import bus


@pytest.fixture(autouse=True)
def clean_state():
    f = paths().data / "components-state.json"
    f.unlink(missing_ok=True)
    yield
    f.unlink(missing_ok=True)


def test_compare_versions():
    assert cu.compare("ytdlp", "2026.10.26", "2026.10.26") == "up_to_date"
    assert cu.compare("ytdlp", "2026.08.19", "2026.10.26") == "update_available"
    assert cu.compare("ytdlp", "2026.10.26.1", "2026.10.26") == "up_to_date"
    assert cu.compare("deno", "2.9.8", "2.10.0") == "update_available"
    # FFmpeg: the daily rebuild of the same release branch is not a new version
    assert cu.compare("ffmpeg", "n9.0.2-14-gebafaee10a-20260928", "9.0") == "up_to_date"
    assert cu.compare("ffmpeg", "n8.1-3-gabc-20260101", "9.0") == "update_available"
    assert cu.compare("ffmpeg", "N-121000-gdeadbeef-20260101", "9.0") == "unknown"
    assert cu.compare("ytdlp", None, "2026.10.26") == "unknown"


def _items(**over):
    base = {"id": "ytdlp", "name": "yt-dlp", "installed": True, "version": "2026.08.19"}
    base.update(over)
    return [base]


def test_annotate_uses_last_check_and_pins():
    data = cu.load()
    data["latest"] = {"ytdlp": "2026.10.26"}
    data["checked_at"] = "2026-11-09T10:00:00+00:00"
    cu._save(data)
    it = cu.annotate(_items())[0]
    assert it["state"] == "update_available" and it["latest"] == "2026.10.26"
    assert it["checked_at"] == "2026-11-09T10:00:00+00:00"
    assert cu.annotate(_items(version="2026.10.26"))[0]["state"] == "up_to_date"
    # downloaded, waiting for a restart
    assert cu.annotate(_items(pending_version="2026.10.26"))[0]["state"] == "restart_required"
    # pinned components are up to date as soon as they are installed
    pinned = cu.annotate([{"id": "rife", "name": "RIFE", "installed": True, "version": "20221029 · v4.6"}])[0]
    assert pinned["state"] == "up_to_date" and pinned["pinned"]
    assert cu.annotate([{"id": "rife", "name": "RIFE", "installed": False}])[0]["state"] == "missing"
    # never checked: we do not pretend to know
    cu._save({"checked_at": None, "latest": {}, "installed": {}, "notified": {}})
    assert cu.annotate(_items())[0]["state"] == "unknown"


def test_check_notifies_once_per_version(monkeypatch):
    monkeypatch.setattr(cu, "fetch_latest", lambda cid, c: {"ytdlp": "2026.10.26", "ffmpeg": "9.0", "deno": "2.9.8"}[cid])
    seen = []
    bus.add_listener(lambda ev: ev["type"] == "component_updates" and seen.append(ev["data"]))
    items = _items()
    cu.check(force=True, items=items)
    cu.check(force=True, items=items)
    assert len(seen) == 1 and seen[0]["items"][0]["latest"] == "2026.10.26"
    st = cu.load()
    assert st["latest"]["ffmpeg"] == "9.0" and st["checked_at"]
    assert not cu.due(st)  # next automatic check in 12 h


def test_check_offline_keeps_previous_date(monkeypatch):
    import httpx

    def boom(cid, c):
        raise httpx.ConnectError("offline")
    cu._save({"checked_at": "2026-11-01T10:00:00+00:00", "latest": {"ytdlp": "2026.10.26"}, "installed": {},
              "notified": {}})
    monkeypatch.setattr(cu, "fetch_latest", boom)
    st = cu.check(force=True, items=_items(version="2026.10.26"))
    assert st["checked_at"] == "2026-11-01T10:00:00+00:00" and st["latest"]["ytdlp"] == "2026.10.26"
    assert set(st["errors"]) == {"ytdlp", "ffmpeg", "deno"}


def test_install_skips_up_to_date_component(monkeypatch):
    calls = []
    monkeypatch.setattr(components, "_install_realcugan", lambda: calls.append("download"))
    monkeypatch.setattr(components, "is_installed", lambda cid: True)
    monkeypatch.setattr(components, "status", lambda: cu.annotate(
        [{"id": "realcugan", "name": "Real-CUGAN", "installed": True, "version": "20220728"}]))
    events = []
    bus.add_listener(lambda ev: ev["type"] == "component" and events.append(ev["data"]))
    r = components.install("realcugan")
    assert r["state"] == "up_to_date" and calls == []
    assert events[-1]["state"] == "up_to_date"
    components.install("realcugan", force=True)  # repair on request
    assert calls == ["download"]


def test_newer_downloaded_engine_wins_older_one_is_dropped(monkeypatch, tmp_path):
    engine = paths().components / "ytdlp" / "yt-dlp.zip"
    engine.parent.mkdir(parents=True, exist_ok=True)

    def make(version):
        with zipfile.ZipFile(engine, "w") as z:
            z.writestr("yt_dlp/version.py", f"__version__ = '{version}'\n")
    make("2026.01.01")
    assert components._zip_ytdlp_version(engine) == "2026.01.01"
    monkeypatch.setattr(components, "_bundled_ytdlp_version", lambda: "2026.08.19")
    monkeypatch.delitem(__import__("sys").modules, "yt_dlp", raising=False)
    import sys
    before = list(sys.path)
    components.activate_engine_override()
    assert not engine.exists() and sys.path == before  # older than the bundled engine: removed
    make("2099.01.01")
    try:
        components.activate_engine_override()
        assert sys.path[0] == str(engine)
    finally:
        sys.path[:] = before
        engine.unlink(missing_ok=True)
