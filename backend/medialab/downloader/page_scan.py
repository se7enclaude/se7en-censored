"""Every video of a web page.

Reads the page's HTML (no browser, no login, no cookies) and collects what it shows or links to:
``<video>`` / ``<source>`` tags, links to video files (``.mp4 .webm .mov .m4v .mkv .m3u8``), ``og:video``
meta tags, JSON-LD ``VideoObject`` data, video URLs written in the page's scripts, and embedded players of
sites the yt-dlp engine knows (YouTube, Vimeo, Dailymotion, Streamable...).

The result is a playlist: each entry keeps the engine that downloads it (``direct`` for a plain file,
``ytdlp`` for streams and embedded players). Protected streams (DRM) stay protected: they fail to download
like anywhere else in se7en.
"""
from __future__ import annotations

import heapq
import json
import re
import threading
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse

import httpx

from ..errors import MediaLabError, classify
from ..logging_setup import get_logger
from .base import media_info
from .direct_provider import UA, referer_headers
from .platforms import domain

log = get_logger("download")

VIDEO_EXT = (".mp4", ".webm", ".mov", ".m4v", ".mkv", ".m3u8", ".mpd", ".ogv")
MAX_PAGE = 6 * 1024 * 1024
MAX_ENTRIES = 500
# a video URL written anywhere in the page (scripts, data attributes, JSON)
_URL_IN_TEXT = re.compile(r"""https?:(?:\\?/){2}[^\s"'<>()\\]+?\.(?:mp4|webm|mov|m4v|mkv|m3u8|mpd|ogv)(?:\?[^\s"'<>\\]*)?(?=["'\s<>\\)]|$)""", re.I)
_GENERIC_LINK = re.compile(r"^(download|t[ée]l[ée]charger|watch|play|voir|regarder|lire|link|lien|here|ici|"
                           r"click here|cliquez ici|mp4|webm|mov|video|vid[ée]o|hd|sd|\d{3,4}p|\d+\s*[mk]b)$", re.I)
_OG_VIDEO = {"og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"}


def _is_video_url(url: str) -> bool:
    return Path(urlparse(url).path).suffix.lower() in VIDEO_EXT


def _clean(url: str, base: str) -> str | None:
    url = (url or "").strip().replace("\\/", "/")
    if not url or url.startswith(("blob:", "data:", "javascript:", "#", "mailto:")):
        return None
    full = urljoin(base, url)
    p = urlparse(full)
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    return urlunparse(p._replace(fragment=""))


def _embed_supported(url: str) -> bool:
    """An embedded player of a site yt-dlp knows (its generic extractor does not count)."""
    try:
        from yt_dlp.extractor import gen_extractor_classes
    except Exception:  # pragma: no cover - yt-dlp is always bundled
        return False
    for ie in gen_extractor_classes():
        if ie.ie_key() == "Generic":
            continue
        try:
            if ie.suitable(url):
                return True
        except Exception:
            continue
    return False


class _Collector(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base = base
        self.title = ""
        self._in_title = False
        self._in_ld = False
        self._ld: list[str] = []
        self._link: dict | None = None  # the <a> being read (its text becomes the title)
        self._video: dict | None = None  # the <video> being read (its <source> children)
        self.found: list[dict] = []  # {url, title, thumbnail, source}
        self.iframes: list[str] = []
        self.links: list[str] = []  # every link of the page (to explore the site)
        self.image: str | None = None  # og:image, the page's picture

    def _add(self, url: str | None, source: str, title: str | None = None, thumbnail: str | None = None) -> None:
        u = _clean(url or "", self.base)
        if u:
            self.found.append({"url": u, "title": title, "thumbnail": _clean(thumbnail or "", self.base), "source": source})

    def handle_starttag(self, tag: str, attrs_list: list) -> None:
        a = {k.lower(): (v or "") for k, v in attrs_list}
        if tag == "title":
            self._in_title = not self.title  # only the document's title, not the <title> of SVG icons
        elif tag == "base" and a.get("href"):
            self.base = urljoin(self.base, a["href"])
        elif tag == "video":
            self._video = {"title": a.get("title") or a.get("aria-label"), "poster": a.get("poster") or a.get("data-poster")}
            for key in ("src", "data-src"):
                if a.get(key):
                    self._add(a[key], "video", self._video["title"], self._video["poster"])
        elif tag == "source" and self._video is not None:
            for key in ("src", "data-src"):
                if a.get(key):
                    self._add(a[key], "video", self._video["title"], self._video["poster"])
        elif tag == "a" and a.get("href"):
            u = _clean(a["href"], self.base)
            if u:
                self.links.append(u)
            if u and _is_video_url(u):
                self._link = {"url": u, "text": [], "title": a.get("title") or a.get("download")}
        elif tag == "iframe":
            src = _clean(a.get("src") or a.get("data-src") or "", self.base)
            if src:
                self.iframes.append(src)
        elif tag == "meta":
            prop = (a.get("property") or a.get("name") or "").lower()
            if prop in _OG_VIDEO and a.get("content"):
                self._add(a["content"], "meta")
            elif prop in ("og:image", "twitter:image") and a.get("content") and not self.image:
                self.image = _clean(a["content"], self.base)
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_ld = True
            self._ld.append("")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "video":
            self._video = None
        elif tag == "a" and self._link:
            text = " ".join("".join(self._link["text"]).split())
            if len(text) < 3 or _GENERIC_LINK.match(text):
                text = ""  # "Download", "▶", "MP4"...: the file name says more
            self.found.append({"url": self._link["url"], "title": self._link["title"] or text or None,
                               "thumbnail": None, "source": "link"})
            self._link = None
        elif tag == "script":
            self._in_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._link is not None:
            self._link["text"].append(data)
        if self._in_ld:
            self._ld[-1] += data

    def json_ld(self) -> None:
        def walk(node: Any) -> None:
            if isinstance(node, list):
                for n in node:
                    walk(n)
            elif isinstance(node, dict):
                kind = node.get("@type")
                kinds = kind if isinstance(kind, list) else [kind]
                if "VideoObject" in kinds:
                    thumb = node.get("thumbnailUrl")
                    thumb = thumb[0] if isinstance(thumb, list) and thumb else thumb
                    for key in ("contentUrl", "embedUrl"):
                        if isinstance(node.get(key), str):
                            self._add(node[key], "json-ld", node.get("name"), thumb if isinstance(thumb, str) else None)
                for v in node.values():
                    if isinstance(v, (list, dict)):
                        walk(v)
        for raw in self._ld:
            try:
                walk(json.loads(raw))
            except (ValueError, TypeError):
                continue


def _provider(url: str) -> str:
    """Plain files go through the direct engine, streams (m3u8/mpd) and players through yt-dlp."""
    return "direct" if _is_video_url(url) and not _is_manifest(url) else "ytdlp"


def _is_manifest(url: str) -> bool:
    return urlparse(url).path.lower().endswith((".m3u8", ".mpd"))


def _name(url: str) -> str:
    return unquote(Path(urlparse(url).path).stem) or "video"


def _parse(html: str, base: str) -> tuple[_Collector, list[dict]]:
    c = _Collector(base)
    try:
        c.feed(html)
        c.close()
    except Exception as e:  # malformed HTML: keep what was read
        log.debug("HTML parse stopped early: %s", e)
    c.json_ld()
    for m in _URL_IN_TEXT.finditer(html.replace("\\/", "/")):  # JSON escapes slashes: https:\/\/...
        c._add(m.group(0), "script")
    out: list[dict] = []
    seen: set[str] = set()
    for f in c.found:
        url = f["url"]
        if url in seen:
            continue
        direct = _is_video_url(url)
        if not direct and f["source"] not in ("video", "meta", "json-ld"):
            continue
        if not direct and not _embed_supported(url):
            continue  # an og:video page / embed URL no engine can read
        seen.add(url)
        out.append({"url": url, "title": (f["title"] or _name(url))[:200], "thumbnail": f["thumbnail"],
                    "provider": _provider(url)})
    for src in c.iframes:
        if src not in seen and _embed_supported(src):
            seen.add(src)
            out.append({"url": src, "title": f"{domain(src) or 'Vidéo'} — {len(out) + 1}", "thumbnail": None,
                        "provider": "ytdlp"})
    return c, out[:MAX_ENTRIES]


def extract(html: str, base: str) -> tuple[str, list[dict]]:
    """(page title, entries) from a page's HTML. Entries: {url, title, thumbnail, provider}."""
    c, out = _parse(html, base)
    return " ".join(c.title.split()), out


def fetch(url: str, referer: str | None = None) -> tuple[str, str]:
    """(final URL, HTML) of a page, as a browser would get it without being logged in."""
    try:
        with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(20), headers={
                "User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8", **referer_headers(referer)}) as c, \
                c.stream("GET", url) as r:
            if r.status_code >= 400:
                raise MediaLabError("http_error", f"The page answered HTTP {r.status_code}.",
                                    "Check the link, or open it in your browser.", r.status_code >= 500)
            ctype = r.headers.get("content-type", "")
            if "html" not in ctype and "xml" not in ctype and ctype:
                raise MediaLabError("no_media", "This link is not a web page.")
            body = b""
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) > MAX_PAGE:
                    break
            return str(r.url), body.decode(r.encoding or "utf-8", errors="replace")
    except MediaLabError:
        raise
    except httpx.HTTPError as e:
        raise classify(e) from e


def analyze_page(url: str) -> dict[str, Any]:
    """Quick look: the raw HTML of one page only (used as the last resort of a normal analysis)."""
    final, html = fetch(url)
    title, entries = extract(html, final)
    if not entries:
        raise MediaLabError("no_media", "No video was found on this page.",
                            "The videos may load only after an action in the browser, or be protected.")
    log.info("Page scan of %s: %d video(s)", final, len(entries))
    return media_info(url=url, webpage_url=final, provider="page", platform="web",
                      platform_name=domain(final) or "Web", kind="playlist", title=title or domain(final),
                      entries=entries, entry_count=len(entries), has_video=True, has_audio=True)


# ============================================================================ whole site
MAX_PAGES = 40  # pages opened while exploring a site
MAX_LISTING_PAGES = 200  # folder listings ("Index of /") are cheap: more of them
MAX_RENDERS = 12  # pages opened in the browser (the slow part)
MAX_DEPTH = 2  # links followed from the first page
MAX_LISTING_DEPTH = 4  # sub-folders followed in a folder listing
TIME_BUDGET = 180.0  # seconds
_PLAYER_HINT = re.compile(r"<video|<iframe|m3u8|\.mp4|\.webm|\.mpd|jwplayer|videojs|video-js|plyr|flowplayer|"
                          r"hls\.js|dash\.js|shaka|clappr|mediaelement|player", re.I)
_SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".css", ".js", ".json", ".xml", ".pdf",
             ".zip", ".rar", ".7z", ".exe", ".apk", ".dmg", ".mp3", ".m4a", ".flac", ".wav", ".txt", ".woff",
             ".woff2", ".ttf", ".rss", ".atom")
_SKIP_PATH = re.compile(r"/(wp-admin|wp-login|wp-json|feed|login|logout|signin|sign-in|signup|sign-up|register|"
                        r"account|cart|checkout|password|privacy|terms|cookie|contact|dmca|cdn-cgi)(/|$|\.)", re.I)
_CONTENT_WORD = re.compile(r"video|clip|episode|watch|movie|film|play|stream|vod|media|gallery|ep-?\d", re.I)
_AD_HOST = re.compile(r"doubleclick|googlesyndication|googleads|adnxs|adsystem|adservice|taboola|outbrain|"
                      r"popads|propeller|exoclick|juicyads|trafficjunky|adsterra|vidoomy|imasdk", re.I)
_PLACEHOLDER = re.compile(r"[{}<>]|%7[bd]|%3[ce]|\$\{|\.\.\.|…", re.I)  # code samples: https://x/{id}.m3u8
_CACHE_PARAMS = {"_", "t", "ts", "cb", "cachebust", "rand", "nocache", "v"}


def _site(url: str) -> str:
    """The site a page belongs to: its domain without the sub-domain (approximation, no public-suffix list)."""
    parts = (urlparse(url).hostname or "").lower().split(".")
    if len(parts) > 2 and len(parts[-1]) == 2 and parts[-2] in ("co", "com", "org", "net", "gov", "ac", "ne", "or"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _key(url: str) -> str:
    """Same video = same address, ignoring cache-busting parameters."""
    p = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in _CACHE_PARAMS]
    return urlunparse(p._replace(query=urlencode(q), fragment="")).lower()


def _is_listing(title: str, html: str) -> bool:
    t = title.strip().lower()
    return t.startswith(("index of", "directory listing")) or bool(
        re.search(r"<h1[^>]*>\s*(index of|directory listing for)\b", html[:5000], re.I))


class SiteScanner:
    """Every video of a site, starting from one page.

    The first page is read raw, then opened in the browser (scripts run, the page is scrolled and its players
    started, the network is watched). Its frames are read too. Then the links of the same site are followed
    (pages under the starting address first), and folder listings are walked through their sub-folders.
    """

    def __init__(self, url: str, report=None, cancel: threading.Event | None = None, render: bool = True,
                 temp_root: Path | None = None, max_pages: int = MAX_PAGES, time_budget: float = TIME_BUDGET) -> None:
        self.start = url
        self.report = report or (lambda **kw: None)
        self.cancel = cancel or threading.Event()
        self.render_enabled = render
        self.temp_root = temp_root
        self.max_pages = max_pages
        self.deadline = time.monotonic() + time_budget
        self.entries: list[dict] = []
        self._keys: set[str] = set()
        self.pages: list[dict] = []  # {url, title}
        self.visited: set[str] = set()
        self._queue: list[tuple] = []
        self._order = 0
        self.renders = 0
        self.listing_pages = 0
        self.browser = None
        self.browser_failed = False
        self.start_title = ""
        self.sites = {_site(url)}  # the site, and where its address redirects to

    # ------------------------------------------------------------------ helpers
    def _stopped(self) -> bool:
        return self.cancel.is_set() or time.monotonic() > self.deadline

    def _add(self, url: str, *, page: str, page_title: str, referer: str | None, title: str | None = None,
             thumbnail: str | None = None, provider: str | None = None, stream: bool = False) -> None:
        if not url or _AD_HOST.search(urlparse(url).hostname or "") or _PLACEHOLDER.search(url):
            return
        k = _key(url)
        if k in self._keys or len(self.entries) >= MAX_ENTRIES:
            return
        self._keys.add(k)
        self.entries.append({"url": url, "title": (title or "").strip()[:200] or None, "thumbnail": thumbnail,
                             "provider": provider or _provider(url), "referer": referer, "page": page,
                             "page_title": page_title, "protected": False, "stream": stream or _is_manifest(url)})

    def _enqueue(self, url: str, depth: int, listing: bool = False) -> None:
        u = _clean(url, self.start)
        if not u:
            return
        u = urlunparse(urlparse(u)._replace(fragment=""))
        if u in self.visited or _site(u) not in self.sites:
            return
        path = urlparse(u).path.lower()
        if path.endswith(_SKIP_EXT) or _is_video_url(u) or _SKIP_PATH.search(path):
            return
        start_dir = urlparse(self.start).path.rsplit("/", 1)[0] + "/"
        score = 0
        if path.startswith(start_dir) and start_dir != "/":
            score -= 3  # below the starting address
        if _CONTENT_WORD.search(u):
            score -= 1
        if urlparse(u).query:
            score += 1
        self._order += 1
        self.visited.add(u)
        heapq.heappush(self._queue, (0 if listing else depth, score, self._order, u, depth, listing))

    def _browser(self):
        if self.browser is None and self.render_enabled and not self.browser_failed:
            from .browser_render import Browser, find_edge
            if not find_edge():
                self.browser_failed = True
                log.info("Site scan: no Edge/Chrome found, raw HTML only")
                return None
            try:
                self.browser = Browser(temp_root=self.temp_root).__enter__()
            except Exception as e:  # noqa: BLE001
                self.browser_failed = True
                log.warning("Site scan: the browser could not start (%s), raw HTML only", e)
        return self.browser

    def _progress(self, label: str) -> None:
        done = len([p for p in self.pages])
        total = max(done + len(self._queue), 1)
        cap = self.max_pages
        percent = min(97.0, 100.0 * done / min(total, cap)) if done else 2.0
        self.report(percent=percent, label=label, pages=done, found=len(self.entries))

    # ------------------------------------------------------------------ one page
    def _scan_page(self, url: str, depth: int, listing_hint: bool) -> None:
        html, final = "", url
        try:
            final, html = fetch(url)
        except MediaLabError as e:
            if depth == 0 and not self.render_enabled:
                raise
            log.info("Site scan: %s could not be read raw (%s)", url, e.code)
        c, raw_entries = _parse(html, final) if html else (_Collector(final), [])
        title = " ".join(c.title.split())
        if depth == 0:
            self.start_title = title
        listing = bool(html) and _is_listing(title, html)
        if depth == 0:
            self.sites.add(_site(final))
        page_title = title or domain(final) or final
        self.pages.append({"url": final, "title": page_title})
        self._progress(page_title)

        if listing:  # a folder of files: its videos, and its sub-folders
            self.listing_pages += 1
            page_title = unquote(urlparse(final).path) or page_title
            self.pages[-1]["title"] = page_title
            for link in c.links:
                if _is_video_url(link):
                    self._add(link, page=final, page_title=page_title, referer=final, title=_name(link))
                elif link.endswith("/") and link.startswith(final) and link != final and \
                        depth < MAX_LISTING_DEPTH and self.listing_pages < MAX_LISTING_PAGES:
                    self._enqueue(link, depth + 1, listing=True)
            return

        for e in raw_entries:
            self._add(e["url"], page=final, page_title=page_title, referer=final, title=e["title"],
                      thumbnail=e["thumbnail"] or c.image, provider=e["provider"])
        links = list(c.links)
        frames = list(c.iframes)

        # the browser: for the first page always, for the others only when the raw HTML shows a player but no video
        want_render = depth == 0 or (not raw_entries and bool(_PLAYER_HINT.search(html or "")))
        if want_render and self.renders < MAX_RENDERS and not self._stopped():
            b = self._browser()
            if b is not None:
                self.renders += 1
                self.report(percent=None, label=page_title, pages=len(self.pages), found=len(self.entries),
                            step="render")
                try:
                    r = b.render(final, cancel=self.cancel)
                    self._use_render(r, page_title if title else (r.title or page_title), depth)
                    if depth == 0 and r.title and not self.start_title:
                        self.start_title = r.title
                    links += r.links
                    frames += r.frames
                except Exception as e:  # noqa: BLE001 - a page the browser cannot open: keep the raw result
                    log.info("Site scan: render of %s failed: %s", final, e)

        # frames without the browser: read their HTML (one level), with this page as Referer
        if self.browser is None:
            for src in dict.fromkeys(frames):
                if self._stopped() or _AD_HOST.search(urlparse(src).hostname or "") or _embed_supported(src):
                    continue
                try:
                    f_final, f_html = fetch(src, referer=final)
                except MediaLabError:
                    continue
                for e in extract(f_html, f_final)[1]:
                    self._add(e["url"], page=final, page_title=page_title, referer=f_final, title=e["title"],
                              thumbnail=e["thumbnail"] or c.image, provider=e["provider"])

        # players yt-dlp's generic engine knows (JW Player, Video.js, Twitter cards...)
        found_here = [e for e in self.entries if e["page"] == final]
        if not found_here and html and _PLAYER_HINT.search(html) and not self._stopped():
            for e in _ytdlp_generic(final):
                self._add(e["url"], page=final, page_title=page_title, referer=final, title=e.get("title"),
                          thumbnail=e.get("thumbnail") or c.image, provider="ytdlp")

        if depth < MAX_DEPTH:
            for link in dict.fromkeys(links):
                self._enqueue(link, depth + 1)

    def _use_render(self, r, page_title: str, depth: int) -> None:
        page = r.final_url or r.url
        posters = {v["src"]: v.get("poster") or None for v in r.videos}
        titles = {v["src"]: v.get("title") or None for v in r.videos}
        for v in r.videos:
            if v["src"].startswith(("http://", "https://")):
                self._add(v["src"], page=page, page_title=page_title, referer=v.get("frame") or page,
                          title=v.get("title"), thumbnail=v.get("poster") or r.image)
        if depth == 0 and r.final_url:
            self.sites.add(_site(r.final_url))
        for m in r.media:
            stream = _is_manifest(m["url"]) or "mpegurl" in m.get("mime", "") or "dash" in m.get("mime", "")
            self._add(m["url"], page=page, page_title=page_title, referer=m.get("referer") or page,
                      title=titles.get(m["url"]), thumbnail=posters.get(m["url"]) or r.image,
                      provider="ytdlp" if stream else "direct", stream=stream)
        if r.html:
            for e in _parse(r.html, page)[1]:
                self._add(e["url"], page=page, page_title=page_title, referer=page, title=e["title"],
                          thumbnail=e["thumbnail"] or r.image, provider=e["provider"])

    # ------------------------------------------------------------------ the whole site
    def run(self) -> dict[str, Any]:
        try:
            self.visited.add(self.start)
            heapq.heappush(self._queue, (0, -99, 0, self.start, 0, False))
            while self._queue and not self._stopped():
                _, _, _, url, depth, listing = heapq.heappop(self._queue)
                normal_pages = len(self.pages) - self.listing_pages
                if not listing and normal_pages >= self.max_pages:
                    continue
                try:
                    self._scan_page(url, depth, listing)
                except MediaLabError:
                    if depth == 0:
                        raise
        finally:
            if self.browser is not None:
                self.browser.__exit__(None, None, None)
                self.browser = None
        if self.cancel.is_set():
            from ..errors import Canceled
            raise Canceled()
        self.report(percent=98.0, label="", pages=len(self.pages), found=len(self.entries), step="check")
        _finish(self.entries)
        if not self.entries:
            raise MediaLabError("no_media", "No video was found on this site.",
                                "The videos may need an account, or be protected.")
        start_page = self.pages[0]["url"] if self.pages else self.start
        log.info("Site scan of %s: %d page(s), %d render(s), %d video(s)", start_page, len(self.pages),
                 self.renders, len(self.entries))
        return media_info(url=self.start, webpage_url=start_page, provider="page", platform="web",
                          platform_name=domain(start_page) or "Web", kind="playlist",
                          title=self.start_title or domain(start_page), entries=self.entries,
                          entry_count=len(self.entries), has_video=True, has_audio=True,
                          pages=self.pages)


def _ytdlp_generic(url: str) -> list[dict]:
    """Videos yt-dlp's generic extractor sees in a page (it knows many web players' configurations)."""
    try:
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "socket_timeout": 15,
                "force_generic_extractor": True, "extract_flat": "in_playlist", "noplaylist": False,
                "logger": _Quiet(), "playlistend": 100}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False, process=False)
    except Exception as e:  # noqa: BLE001 - nothing found is the normal case
        log.debug("generic extractor on %s: %s", url, e)
        return []
    items = list(info.get("entries") or []) if info and info.get("_type") == "playlist" else [info] if info else []
    out = []
    for it in items[:100]:
        if not isinstance(it, dict):
            continue
        u = it.get("url") or it.get("webpage_url")
        if isinstance(u, str) and u.startswith(("http://", "https://")) and _key(u) != _key(url):
            out.append({"url": u, "title": it.get("title"), "thumbnail": it.get("thumbnail")})
        elif it.get("formats") and _key(it.get("webpage_url") or url) == _key(url):
            out.append({"url": url, "title": it.get("title"), "thumbnail": it.get("thumbnail")})
    return out


class _Quiet:
    def debug(self, msg: str) -> None: ...
    def info(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...


_DRM_HLS = re.compile(r"#EXT-X-(?:SESSION-)?KEY:[^\n]*(METHOD=SAMPLE-AES|KEYFORMAT=\"(?!identity)[^\"]+\")", re.I)


def _finish(entries: list[dict]) -> None:
    """Read the stream manifests: drop the variants of a master playlist, mark protected (DRM) streams."""
    variants: set[str] = set()
    master_pages: set[str] = set()
    media_playlists: list[dict] = []  # a single rendition: drop it when its page has a master playlist
    manifests = [e for e in entries if e.get("stream")][:40]
    with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(8), headers={"User-Agent": UA}) as c:
        for e in manifests:
            try:
                r = c.get(e["url"], headers=referer_headers(e.get("referer")))
                text = r.text[:400_000] if r.status_code < 400 else ""
            except httpx.HTTPError:
                continue
            if "#EXTINF" in text and "#EXT-X-STREAM-INF" not in text:
                media_playlists.append(e)
            if "#EXT-X-STREAM-INF" in text or "#EXT-X-MEDIA:" in text:
                master_pages.add(e["page"])
                for line in text.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        variants.add(_key(urljoin(str(r.url), line)))
                    for m in re.finditer(r'URI="([^"]+)"', line):
                        variants.add(_key(urljoin(str(r.url), m.group(1))))
            if _DRM_HLS.search(text) or "<ContentProtection" in text:
                e["protected"] = True
    drop = {id(e) for e in media_playlists if e["page"] in master_pages}
    entries[:] = [e for e in entries
                  if id(e) not in drop and not (e.get("stream") and _key(e["url"]) in variants)]
    for e in entries:
        e.pop("stream", None)
    # titles: the video's own, else the page's (numbered when a page has several)
    per_page: dict[str, int] = {}
    for e in entries:
        per_page[e["page"]] = per_page.get(e["page"], 0) + 1
    seen: dict[str, int] = {}
    for e in entries:
        if e["title"]:
            continue
        seen[e["page"]] = seen.get(e["page"], 0) + 1
        base = e.get("page_title") or _name(e["url"])
        e["title"] = f"{base} — {seen[e['page']]}" if per_page[e["page"]] > 1 else base


def scan_site(url: str, task=None, temp_root: Path | None = None, render: bool = True) -> dict[str, Any]:
    """Background task body: the whole site, with progress."""
    report = (lambda **kw: task.update(kw.pop("percent", None), kw.pop("label", None), **kw)) if task else None
    return SiteScanner(url, report=report, cancel=task.cancel if task else None, render=render,
                       temp_root=temp_root).run()
