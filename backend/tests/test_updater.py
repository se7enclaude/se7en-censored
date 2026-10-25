"""Automatic updates against a local fake release server."""
import hashlib
import http.server
import json
import threading
from pathlib import Path

import pytest

from medialab import __version__, updater as upd


def test_version_comparison():
    assert upd.is_newer("1.10.0", "1.9.3")
    assert upd.is_newer("v2.0", "1.99.99")
    assert not upd.is_newer("1.0.0", "1.0.0")
    assert not upd.is_newer("0.9", "1.0.0")
    assert upd.parse_version("v1.2.3-beta") == (1, 2, 3)


@pytest.fixture
def feed(tmp_path, monkeypatch):
    payload = b"fake installer bytes" * 5000
    (tmp_path / "se7en-Update-9.9.9.exe").write_bytes(payload)
    state = {"sha": hashlib.sha256(payload).hexdigest(), "version": "9.9.9"}

    class H(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(tmp_path), **kw)

        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/latest.json":
                body = json.dumps({"version": state["version"], "notes": "New tools", "update": {
                    "name": "se7en-Update-9.9.9.exe", "url": f"http://127.0.0.1:{port}/se7en-Update-9.9.9.exe",
                    "sha256": state["sha"], "size": len(payload)}, "portable": {
                    "name": "p.zip", "url": f"http://127.0.0.1:{port}/p.zip", "sha256": "0" * 64, "size": 1}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("SE7EN_UPDATE_URL", f"http://127.0.0.1:{port}/latest.json")
    yield state
    srv.shutdown()


def test_check_download_and_verify(feed):
    u = upd.Updater()
    st = u.check()
    assert st["state"] == "available" and st["latest"] == "9.9.9" and st["notes"] == "New tools"
    st = u.download()
    assert st["state"] == "ready" and Path(st["file"]).exists()
    assert u.check()["state"] == "ready"  # a later check keeps the downloaded update ready to install
    with pytest.raises(Exception) as e:  # installing needs the packaged app
        u.install()
    assert getattr(e.value, "code", "") == "update_dev"


def test_corrupted_download_is_refused(feed):
    feed["sha"] = "f" * 64
    u = upd.Updater()
    u.check()
    st = u.download()
    assert st["state"] == "available" and st["error"]["code"] == "checksum_mismatch"
    assert not list((Path(upd.paths().data) / "updates").glob("*.part"))


def test_same_version_is_up_to_date(feed):
    feed["version"] = __version__
    assert upd.Updater().check()["state"] == "uptodate"


def test_unconfigured_without_feed(monkeypatch):
    monkeypatch.delenv("SE7EN_UPDATE_URL", raising=False)
    assert upd.Updater().check()["state"] == "unconfigured"


def test_install_waits_for_se7en_to_exit(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(upd.subprocess, "Popen", lambda args, **kw: started.append(args))
    exe = tmp_path / "se7en-Update-9.9.9.exe"
    exe.write_bytes(b"x")
    upd.Updater()._run_installer(exe, relaunch=True)
    script = Path(started[0][2])
    text = script.read_text("utf-8", errors="replace")
    assert started[0][0].lower().endswith("cmd.exe") and started[0][1] == "/c"
    assert f'PID eq {upd.os.getpid()}' in text and "/VERYSILENT" in text and '"RELAUNCH=1"' in text
    assert text.index("tasklist") < text.index(str(exe))  # waits for se7en, then installs
    # a se7en reopened meanwhile is closed first (its locked files made the installer abort halfway),
    # the installer may force-close what is left, and se7en is reopened afterwards
    assert text.index("taskkill") < text.index(str(exe)) and "/FORCECLOSEAPPLICATIONS" in text
    assert "/RELAUNCH=%RELAUNCH%" in text and "	" not in text


def test_portable_swap_keeps_data_and_components(monkeypatch, tmp_path):
    import zipfile
    started = []
    monkeypatch.setattr(upd.subprocess, "Popen", lambda args, **kw: started.append(args))
    z = tmp_path / "p.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("se7en/se7en-censored.exe", "new")
        f.writestr("se7en/_internal/lib.dll", "new")
    upd.Updater()._swap_portable(z, relaunch=False)
    text = Path(started[0][2]).read_text("utf-8", errors="replace")
    assert "/XD" in text and "data" in text and "portable.flag" in text and "/MIR" in text
    assert "start \"\"" not in text  # relaunch=False
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as f:
        f.writestr("../evil.exe", "x")
    with pytest.raises(Exception):
        upd.Updater()._swap_portable(bad)


HELPER_PARENT = """
import os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from medialab import updater as u
script, marker = Path(sys.argv[2]), sys.argv[3]
u._write_script(script, [*u._wait_for(os.getpid()), f'echo ok> "{marker}"'])
u._launch(script, str(script.parent))
time.sleep(2)
"""


def test_helper_runs_after_se7en_exits(tmp_path):
    """Real scenario: a process starts the helper like se7en does, then exits. The helper must wait for it and then
    run its next lines (a stray CR once broke ``goto wait``; DETACHED_PROCESS made cmd quit silently)."""
    import subprocess
    import sys
    import time
    marker = tmp_path / "dossier é" / "done.txt"
    marker.parent.mkdir()
    script = tmp_path / "helper.cmd"
    backend = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-c", HELPER_PARENT, str(backend), str(script), str(marker)], check=True, timeout=60)
    assert b"\r\r\n" not in script.read_bytes()
    for _ in range(40):
        if marker.exists():
            break
        time.sleep(0.25)
    assert marker.read_text().strip() == "ok"




def test_what_changed_is_shown_once_after_an_update(monkeypatch, tmp_path):
    from types import SimpleNamespace
    monkeypatch.setattr(upd, "paths", lambda: SimpleNamespace(data=tmp_path, settings=tmp_path / "settings.json"))
    # first install: nothing to announce
    u = upd.Updater()
    u.note_launch()
    assert "updated_from" not in u.snapshot() and (tmp_path / "version.txt").read_text() == __version__
    # launch after an update from an older version
    (tmp_path / "version.txt").write_text("0.9.0")
    u = upd.Updater()
    u.note_launch()
    assert u.snapshot()["updated_from"] == "0.9.0"
    assert "updated_from" not in u.seen()
    # the next launch of the same version shows nothing
    u = upd.Updater()
    u.note_launch()
    assert "updated_from" not in u.snapshot()


def test_update_config_written_with_a_bom_is_read(monkeypatch, tmp_path):
    # PowerShell's Set-Content -Encoding utf8 starts the file with a BOM: it must not switch updates off
    monkeypatch.delenv("SE7EN_UPDATE_URL", raising=False)
    monkeypatch.setattr(upd, "app_root", lambda: tmp_path)
    (tmp_path / "update.json").write_bytes(b'\xef\xbb\xbf{\r\n    "repo":  "owner/se7en"\r\n}')
    assert upd.feed_url() == "https://github.com/owner/se7en/releases/latest/download/latest.json"
