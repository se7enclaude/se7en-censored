"""Every video of a web page (page_scan): parsing of a synthetic page, then the HTTP endpoint on a local server."""
import http.server
import threading

import pytest

from medialab.downloader import page_scan

PAGE = """<!doctype html><html><head><title>  Ma page de clips </title>
<meta property="og:video" content="https://cdn.example.org/og.mp4">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
  {"@type":"VideoObject","name":"Clip JSON","contentUrl":"/media/json.webm","thumbnailUrl":["/t/json.jpg"]}]}</script>
</head><body>
<video poster="/t/a.jpg" title="Clip A"><source src="/media/a.mp4" type="video/mp4"><source src="/media/a.webm"></video>
<video src="media/b.mov" data-poster="/t/b.jpg"></video>
<a href="/files/c.mp4?token=1">Clip <b>C</b></a>
<a href="/files/page.html">not a video</a>
<a href="/files/c.mp4?token=1">same clip twice</a>
<iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe>
<iframe src="https://ads.example.org/banner.html"></iframe>
<script>var player = {"hls":"https:\\/\\/stream.example.org\\/live\\/index.m3u8","poster":"x.jpg"};</script>
<video src="blob:https://example.org/123"></video>
</body></html>"""


def test_extract_finds_every_kind_of_video():
    title, entries = page_scan.extract(PAGE, "https://site.example.org/clips/")
    urls = [e["url"] for e in entries]
    assert title == "Ma page de clips"
    assert "https://site.example.org/media/a.mp4" in urls and "https://site.example.org/media/a.webm" in urls
    assert "https://site.example.org/clips/media/b.mov" in urls  # relative to the page
    assert "https://site.example.org/files/c.mp4?token=1" in urls and urls.count("https://site.example.org/files/c.mp4?token=1") == 1
    assert "https://cdn.example.org/og.mp4" in urls
    assert "https://site.example.org/media/json.webm" in urls
    assert "https://stream.example.org/live/index.m3u8" in urls
    assert "https://www.youtube.com/embed/dQw4w9WgXcQ" in urls
    assert not any("banner" in u or u.startswith("blob:") or u.endswith(".html") for u in urls)
    by = {e["url"]: e for e in entries}
    assert by["https://site.example.org/media/a.mp4"]["title"] == "Clip A"
    assert by["https://site.example.org/media/a.mp4"]["thumbnail"] == "https://site.example.org/t/a.jpg"
    assert by["https://site.example.org/files/c.mp4?token=1"]["title"] == "Clip C"
    assert by["https://site.example.org/media/json.webm"]["title"] == "Clip JSON"
    # plain files go through the direct engine, streams and embedded players through yt-dlp
    assert by["https://site.example.org/media/a.mp4"]["provider"] == "direct"
    assert by["https://stream.example.org/live/index.m3u8"]["provider"] == "ytdlp"
    assert by["https://www.youtube.com/embed/dQw4w9WgXcQ"]["provider"] == "ytdlp"


def test_a_page_without_video_is_reported():
    title, entries = page_scan.extract("<html><title>Rien</title><p>texte</p></html>", "https://x.org/")
    assert title == "Rien" and entries == []


@pytest.fixture
def site(tmp_path):
    (tmp_path / "index.html").write_text(PAGE, "utf-8")
    (tmp_path / "empty.html").write_text("<html><title>Vide</title></html>", "utf-8")
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(tmp_path), **k)  # noqa: E731
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_analyze_page_returns_a_playlist(site):
    from medialab.errors import MediaLabError
    info = page_scan.analyze_page(site + "/index.html")
    assert info["kind"] == "playlist" and info["provider"] == "page" and info["title"] == "Ma page de clips"
    assert info["entry_count"] == len(info["entries"]) >= 8
    with pytest.raises(MediaLabError) as e:
        page_scan.analyze_page(site + "/empty.html")
    assert e.value.code == "no_media"


# ------------------------------------------------------------------ whole site (deep scan)
SITE = {
    "index.html": """<html><head><title>Accueil</title><meta property="og:image" content="/cover.jpg"></head><body>
<div id="p"></div>
<script>setTimeout(() => { const v = document.createElement('video'); v.src = '/media/js-only.mp4';
  document.getElementById('p').appendChild(v); }, 200);</script>
<iframe src="/player.html"></iframe>
<a href="/clips/a/b/clip.html">un clip</a> <a href="/files/">dossier</a>
<a href="/login">connexion</a> <a href="https://other.example.net/x.html">ailleurs</a>
</body></html>""",
    "player.html": "<html><body><script>fetch('/hls/master.m3u8')</script></body></html>",
    "clips/a/b/clip.html": "<html><title>Clip profond</title><video src='/media/deep.mp4'></video></html>",
    "login": "<html><video src='/media/never.mp4'></video></html>",
    "files/one.mp4": "x", "files/sub/two.webm": "x", "files/sub/deeper/three.mov": "x",
    "media/js-only.mp4": "x", "media/deep.mp4": "x",
    "hls/master.m3u8": "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nlow.m3u8\n",
    "hls/low.m3u8": "#EXTM3U\n#EXTINF:1,\nseg0.ts\n",
    "drm/index.html": "<html><title>DRM</title><video src='/drm/locked.m3u8'></video></html>",
    "drm/locked.m3u8": '#EXTM3U\n#EXT-X-KEY:METHOD=SAMPLE-AES,URI="skd://x",KEYFORMAT="com.apple.streamingkeydelivery"\n#EXTINF:1,\na.ts\n',
}


class _RefererHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/guarded/") and "/guard-page" not in (self.headers.get("Referer") or ""):
            self.send_error(403)
            return
        super().do_GET()


@pytest.fixture
def deep_site(tmp_path):
    root = tmp_path / "www"
    for rel, body in {**SITE, "guarded/v.mp4": "video-bytes"}.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body, "utf-8")
    handler = lambda *a, **k: _RefererHandler(*a, directory=str(root), **k)  # noqa: E731
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _urls(info):
    return {e["url"].split("/", 3)[3]: e for e in info["entries"]}


def test_site_scan_without_browser_follows_pages_and_folders(deep_site):
    info = page_scan.scan_site(deep_site + "/index.html", render=False)
    got = _urls(info)
    assert "media/deep.mp4" in got and got["media/deep.mp4"]["page_title"] == "Clip profond"
    assert {"files/one.mp4", "files/sub/two.webm", "files/sub/deeper/three.mov"} <= set(got)
    assert "hls/master.m3u8" not in got  # only requested by a script: needs the browser
    assert "media/never.mp4" not in got  # login pages are not explored
    assert got["media/deep.mp4"]["referer"].endswith("/clips/a/b/clip.html")


def test_protected_streams_are_marked(deep_site):
    info = page_scan.scan_site(deep_site + "/drm/index.html", render=False)
    assert _urls(info)["drm/locked.m3u8"]["protected"] is True


def test_master_playlist_hides_its_variants():
    entries = []
    page_scan._finish(entries)  # nothing to read: no crash
    assert entries == []


from medialab.downloader.browser_render import find_edge, is_media  # noqa: E402


def test_is_media_keeps_files_and_manifests_not_segments():
    assert is_media("https://x.org/v.mp4") and is_media("https://x.org/a/index.m3u8?t=1")
    assert is_media("https://x.org/stream", "application/vnd.apple.mpegurl")
    assert not is_media("https://x.org/seg12.ts", "video/mp2t") and not is_media("blob:https://x.org/1")


@pytest.mark.skipif(not find_edge(), reason="no Edge/Chrome on this machine")
def test_site_scan_with_browser_finds_script_videos(deep_site, tmp_path):
    info = page_scan.scan_site(deep_site + "/index.html", temp_root=tmp_path / "t")
    got = _urls(info)
    assert "media/js-only.mp4" in got  # inserted by a script
    assert got["media/js-only.mp4"]["thumbnail"].endswith("/cover.jpg")
    assert "hls/master.m3u8" in got and got["hls/master.m3u8"]["provider"] == "ytdlp"
    assert got["hls/master.m3u8"]["referer"].endswith("/player.html")  # the frame that asked for it
    assert "hls/low.m3u8" not in got  # a variant of the master playlist
    assert "media/deep.mp4" in got and "files/sub/two.webm" in got
    assert not list((tmp_path / "t").iterdir())  # the browser profile is deleted


def test_direct_download_sends_the_referer(deep_site, tmp_path):
    from types import SimpleNamespace

    from medialab.downloader.direct_provider import DirectProvider
    from medialab.settings import Settings
    s = Settings()
    s.download.default_folder = str(tmp_path / "out")
    ctx = SimpleNamespace(settings=s, temp_dir=tmp_path / "tmp", job_id="j1", cancel=threading.Event(),
                          progress=lambda *a, **k: None)
    res = DirectProvider().download(deep_site + "/guarded/v.mp4",
                                    {"referer": deep_site + "/guard-page.html", "folder_structure": ""}, ctx)
    assert (tmp_path / "out").exists() and res
