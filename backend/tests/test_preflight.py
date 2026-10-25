import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows start-up checks")


def _mark(p: Path) -> None:
    with open(f"{p}:Zone.Identifier", "w") as f:
        f.write("[ZoneTransfer]\nZoneId=3\n")


def test_unblock_removes_web_mark_from_app_files(tmp_path, monkeypatch):
    from medialab import preflight

    exe = tmp_path / "se7en.exe"
    dll = tmp_path / "_internal" / "pythonnet" / "runtime" / "Python.Runtime.dll"
    user_file = tmp_path / "data" / "downloads" / "video.mp4"
    for f in (exe, dll, user_file):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
        _mark(f)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert preflight.has_web_mark(dll)
    n = preflight.unblock_app_files(tmp_path)
    assert n == 2
    assert not preflight.has_web_mark(exe) and not preflight.has_web_mark(dll)
    assert preflight.has_web_mark(user_file)  # the user's data folder is never touched


def test_unblock_does_nothing_when_exe_is_not_marked(tmp_path, monkeypatch):
    from medialab import preflight

    exe = tmp_path / "se7en.exe"
    exe.write_bytes(b"x")
    other = tmp_path / "a.dll"
    other.write_bytes(b"x")
    _mark(other)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert preflight.unblock_app_files(tmp_path) == 0
    assert preflight.has_web_mark(other)


@pytest.mark.parametrize("trace,expected", [
    ("RuntimeError: Failed to resolve Python.Runtime.Loader.Initialize from C:\\x\\Python.Runtime.dll", ("Débloquer", "Unblock")),
    ("webview.errors.WebViewException: You must have pythonnet installed", ("WebView2", "WebView2")),
    ("PermissionError: [WinError 5] Access is denied: 'C:\\\\Program Files\\\\se7en\\\\data'", ("Program Files", "Program Files")),
    ("ModuleNotFoundError: No module named 'yt_dlp'", ("antivirus", "antivirus")),
])
def test_crash_explanations(trace, expected):
    from medialab import preflight

    text = preflight.explain_crash(trace, Path("C:/x/crash.log"))
    assert expected[0] in text or expected[1] in text
    assert "crash.log" in text
