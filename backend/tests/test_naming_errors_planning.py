import pytest

from medialab.downloader import naming
from medialab.downloader.registry import extract_urls
from medialab.errors import classify
from medialab.upscale import planning


# ------------------------------------------------------------------ naming
def test_friendly_tokens_translate_to_ytdlp():
    t = naming.build_outtmpl(folder_structure="{platform}/{uploader}/{year}/{month}",
                             naming_template="{date} - {title}", platform="twitter")
    assert t.startswith("X/%(uploader,")
    assert "%(upload_date>%Y|0000)s" in t
    assert t.endswith("%(title).150B.%(ext)s")


def test_native_ytdlp_template_passthrough():
    t = naming.build_outtmpl(folder_structure="", naming_template="%(uploader)s - %(title)s", platform="youtube")
    assert t == "%(uploader)s - %(title)s.%(ext)s"


def test_platform_override_and_percent_escape():
    t = naming.build_outtmpl(folder_structure="{platform}", naming_template="{title}", platform="youtube",
                             platform_overrides={"youtube": "YT 100%"})
    assert t.startswith("YT 100%%/")


@pytest.mark.parametrize("raw,expected", [
    ('a<b>c:d"e/f\\g|h?i*j', "a_b_c_d_e_f_g_h_i_j"),
    ("CON", "_CON"),
    ("name. ", "name"),
    ("", "untitled"),
])
def test_sanitize_windows(raw, expected):
    assert naming.sanitize_component(raw) == expected


def test_preview():
    p = naming.preview("{uploader} - {title}", "{platform}/{year}")
    assert p.replace("\\", "/") == "YouTube/2026/Creator - Example Video.mp4"


def test_extract_urls_from_messy_text():
    text = "look https://youtu.be/abc, and\nhttps://x.com/u/status/1).\nhttps://youtu.be/abc again"
    assert extract_urls(text) == ["https://youtu.be/abc", "https://x.com/u/status/1"]


# ------------------------------------------------------------------ errors
@pytest.mark.parametrize("msg,code", [
    ("ERROR: [youtube] xyz: Private video. Sign in if you've been granted access", "private"),
    ("ERROR: [youtube] xyz: Sign in to confirm your age. This video may be inappropriate", "age_restricted"),
    ("ERROR: [youtube] xyz: Sign in to confirm you’re not a bot", "login_required"),
    ("ERROR: This video is DRM protected", "drm"),
    ("ERROR: Unsupported URL: https://example.com", "unsupported_url"),
    ("ERROR: unable to download video data: HTTP Error 403: Forbidden", "forbidden"),
    ("HTTP Error 429: Too Many Requests", "rate_limited"),
    ("The uploader has not made this video available in your country", "geo_blocked"),
    ("[Errno 28] No space left on device", "disk_full"),
    ("ffprobe and ffmpeg not found. Please install or provide the path", "ffmpeg_missing"),
    ("Requested format is not available. Use --list-formats", "format_unavailable"),
    ("<urlopen error [Errno 11001] getaddrinfo failed>", "network"),
])
def test_error_classification(msg, code):
    e = classify(msg)
    assert e.code == code
    assert e.message and e.hint


def test_unknown_error_still_explained():
    e = classify("something weird happened")
    assert e.code == "unknown" and e.message and e.hint and e.retryable


# ------------------------------------------------------------------ planning
def test_plan_4x_exact():
    steps = planning.plan(1920, 1080, 7680, 4320, (4,))
    assert [(s.kind, s.scale) for s in steps] == [("ai", 4)]


def test_plan_2x_with_4x_model_supersamples():
    steps = planning.plan(1920, 1080, 3840, 2160, (4,))
    assert [s.kind for s in steps] == ["ai", "resize"]
    assert steps[-1].width == 3840


def test_plan_2x_native():
    steps = planning.plan(1920, 1080, 3840, 2160, (2, 3, 4))
    assert [(s.kind, s.scale) for s in steps] == [("ai", 2)]


def test_plan_8x_small_image_two_ai_passes():
    steps = planning.plan(400, 300, 3200, 2400, (4,))
    assert [s.kind for s in steps] == ["ai", "ai", "resize"]


def test_plan_8x_large_image_falls_back_to_resize():
    steps = planning.plan(1920, 1080, 15360, 8640, (4,))
    assert [s.kind for s in steps] == ["ai", "resize"]


def test_target_size_custom_keep_aspect():
    assert planning.target_size(1920, 1080, width=3840, height=2160) == (3840, 2160)
    assert planning.target_size(1000, 1000, width=3840, height=2160) == (2160, 2160)
    assert planning.target_size(1279, 719, scale=2) == (2558, 1438)


def test_vram_tiles():
    assert planning.ncnn_tile_for_vram(16000) == 512
    assert planning.ncnn_tile_for_vram(9500) == 400
    assert planning.ncnn_tile_for_vram(9500, limit_mb=3000) == 200
    assert planning.ncnn_tile_for_vram(None) == 0
    assert planning.reduce_tile(400, 1) == 192 or planning.reduce_tile(400, 1) == 200
    assert planning.reduce_tile(64, 3) == 32


def test_settings_migration_switches_to_the_glass_light_theme():
    # 1.3: the red "liquid glass" light look replaces the old dark default; an explicit "system" choice is kept
    from medialab.settings import SETTINGS_VERSION, _migrate
    assert _migrate({"version": 2, "general": {"theme": "dark"}})["general"]["theme"] == "light"
    assert _migrate({"version": 2, "general": {"theme": "system"}})["general"]["theme"] == "system"
    assert _migrate({"version": 2, "general": {"theme": "dark"}})["version"] == SETTINGS_VERSION
    assert _migrate({"version": SETTINGS_VERSION, "general": {"theme": "dark"}})["general"]["theme"] == "dark"


def test_a_windowless_instance_is_not_taken_for_the_open_app(data_dir, monkeypatch):
    # a dev / server instance writes the session file too: opening se7en must not jump to it in a browser
    from medialab import main as m
    from medialab.security import clear_session_file, write_session_file
    opened = []
    monkeypatch.setattr(m.webbrowser, "open", lambda url: opened.append(url))
    write_session_file(1, "dev", shell=False)
    try:
        assert m._single_instance_or_focus() is True and not opened
    finally:
        clear_session_file()
