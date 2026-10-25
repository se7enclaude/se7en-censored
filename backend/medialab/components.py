"""External components: detection, one-click install and updates.

Components are resolved in this order:
  1. explicit path in Settings › Advanced
  2. bundled next to the executable:   <app>/bin/<component>/...
  3. installed by Media Lab:           <data>/components/<component>/...
  4. system PATH

All downloads come from the projects' official GitHub releases over HTTPS and
are verified against the published SHA-256 checksums when the project
publishes them.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from .config import app_root, paths
from .errors import MediaLabError
from .events import bus
from .logging_setup import get_logger

log = get_logger("app")

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
EXE = ".exe" if sys.platform == "win32" else ""


@dataclass
class ComponentSpec:
    id: str
    name: str
    description: str
    binaries: tuple[str, ...]
    required_for: str
    optional: bool = False


SPECS: dict[str, ComponentSpec] = {
    "ffmpeg": ComponentSpec("ffmpeg", "FFmpeg", "Merging, conversion, metadata, video encoding",
                            ("ffmpeg", "ffprobe"), "download, upscale"),
    "realesrgan": ComponentSpec("realesrgan", "Real-ESRGAN (ncnn-Vulkan)",
                                "AI upscaling on any Vulkan GPU (NVIDIA, AMD, Intel)",
                                ("realesrgan-ncnn-vulkan",), "upscale"),
    "deno": ComponentSpec("deno", "Deno (JavaScript runtime)",
                          "Required by the engine for complete YouTube format lists", ("deno",), "download"),
    "realcugan": ComponentSpec("realcugan", "Real-CUGAN (ncnn-Vulkan)", "Anime / cartoon upscaling, 2x-4x",
                               ("realcugan-ncnn-vulkan",), "upscale"),
    "rife": ComponentSpec("rife", "RIFE (ncnn-Vulkan)", "Frame interpolation: smoother videos (30 → 60 fps)",
                          ("rife-ncnn-vulkan",), "upscale"),
    "whisper": ComponentSpec("whisper", "Whisper (whisper.cpp)", "Speech recognition for automatic subtitles",
                             ("whisper-cli", "whisper-vad-speech-segments"), "subtitles"),
    "llama": ComponentSpec("llama", "llama.cpp (Vulkan)", "Local language model: subtitle correction and translation",
                           ("llama-server",), "subtitles"),
    "aipack": ComponentSpec("aipack", "Détecteur (IA)", "Le modèle qui repère les zones à flouter (NudeNet, ~100 Mo)",
                            (), "upscale"),
    "ytdlp": ComponentSpec("ytdlp", "yt-dlp engine update", "Latest extractors without reinstalling se7en",
                           (), "download", optional=True),
    "aria2": ComponentSpec("aria2", "aria2", "Optional multi-connection downloader for direct files",
                           ("aria2c",), "download", optional=True),
}

_settings_overrides: dict[str, str] = {}
_cache: dict[str, str | None] = {}
_install_lock = threading.Lock()


def set_overrides(ffmpeg: str = "", realesrgan: str = "", aria2c: str = "") -> None:
    _settings_overrides.update({"ffmpeg": ffmpeg, "realesrgan-ncnn-vulkan": realesrgan, "aria2c": aria2c})
    _cache.clear()


def _search_dirs(component: str) -> list[Path]:
    from .config import shared_data_dirs
    return [app_root() / "bin" / component, paths().components / component,
            *(d / "components" / component for d in shared_data_dirs())]


def _find_in(root: Path, filename: str, depth: int = 4) -> Path | None:
    if not root.exists():
        return None
    direct = root / filename
    if direct.is_file():
        return direct
    for dirpath, dirnames, filenames in os.walk(root):
        if Path(dirpath).relative_to(root).parts.__len__() > depth:
            dirnames[:] = []
            continue
        if filename in filenames:
            return Path(dirpath) / filename
    return None


def find_binary(binary: str) -> str | None:
    if binary in _cache and (_cache[binary] is None or Path(_cache[binary]).exists()):
        return _cache[binary]
    result: str | None = None
    override = _settings_overrides.get(binary if binary != "ffprobe" else "ffmpeg", "")
    if override:
        p = Path(override)
        if p.is_dir():
            p = p / f"{binary}{EXE}"
        elif binary == "ffprobe":
            p = p.with_name(f"ffprobe{EXE}")
        if p.is_file():
            result = str(p)
    if not result:
        component = next((c for c, s in SPECS.items() if binary in s.binaries), binary)
        for d in _search_dirs(component):
            found = _find_in(d, f"{binary}{EXE}")
            if found:
                result = str(found)
                break
    if not result:
        result = shutil.which(binary)
    _cache[binary] = result
    return result


def require(binary: str) -> str:
    path = find_binary(binary)
    if not path:
        comp = "ffmpeg" if binary in ("ffmpeg", "ffprobe") else binary
        raise MediaLabError(
            "ffmpeg_missing" if comp == "ffmpeg" else "component_missing",
            f"{binary} is not installed.",
            "Install it from Settings › Components (one click).",
            False,
            extra={"component": "realesrgan" if "realesrgan" in binary else comp},
        )
    return path


def ffmpeg_dir() -> str | None:
    p = find_binary("ffmpeg")
    return str(Path(p).parent) if p else None


def _version_of(binary_path: str, args: list[str], pattern: str) -> str | None:
    try:
        out = subprocess.run([binary_path, *args], capture_output=True, text=True, timeout=15,
                             creationflags=CREATE_NO_WINDOW, errors="replace")
        m = re.search(pattern, (out.stdout or "") + (out.stderr or ""))
        return m.group(1) if m else "installed"
    except (OSError, subprocess.SubprocessError):
        return None


def ytdlp_version() -> str | None:
    try:
        import yt_dlp.version
        return yt_dlp.version.__version__
    except Exception:
        return None


def status() -> list[dict]:
    items = []
    ff = find_binary("ffmpeg")
    fp = find_binary("ffprobe")
    items.append({
        **_spec_dict("ffmpeg"),
        "installed": bool(ff and fp),
        "path": ff,
        "version": _version_of(ff, ["-version"], r"ffmpeg version (\S+)") if ff else None,
    })
    rs = find_binary("realesrgan-ncnn-vulkan")
    items.append({**_spec_dict("realesrgan"), "installed": bool(rs), "path": rs,
                  "version": "20220424" if rs else None})
    rt = js_runtime()
    deno_item = _spec_dict("deno")
    if rt and rt[0] != "deno":
        deno_item["name"] = f"JavaScript runtime ({ {'node': 'Node.js', 'bun': 'Bun'}.get(rt[0], rt[0]) } detected)"
    items.append({**deno_item, "installed": bool(rt), "path": rt[1] if rt else None,
                  "version": (_version_of(rt[1], ["--version"], r"(?:deno\s+)?v?(\d+\.\d+\.\d+)") if rt else None),
                  "runtime": rt[0] if rt else None})
    engine_zip = paths().components / "ytdlp" / "yt-dlp.zip"
    items.append({**_spec_dict("ytdlp"), "installed": True,
                  "path": str(engine_zip) if engine_zip.exists() else "bundled",
                  "version": ytdlp_version()})
    for cid, binary, version in (("realcugan", "realcugan-ncnn-vulkan", "20220728"),
                                 ("rife", "rife-ncnn-vulkan", "20221029 · v4.6"),
                                 ("whisper", "whisper-cli", "b5130"), ("llama", "llama-server", "b11284")):
        b = find_binary(binary)
        items.append({**_spec_dict(cid), "installed": bool(b), "path": b, "version": version if b else None})
    from . import ai_assets
    st = ai_assets.status()
    mine = [i for i in st["items"] if i["purpose"] in ai_assets.REQUIRED]  # le détecteur de se7en Censored seulement
    items.append({**_spec_dict("aipack"), "installed": st["installed"], "path": None,
                  "version": f"{sum(1 for i in mine if i['installed'])}/{len(mine)}",
                  "size": sum(i["size"] for i in mine), "models": mine})
    ar = find_binary("aria2c")
    items.append({**_spec_dict("aria2"), "installed": bool(ar), "path": ar,
                  "version": _version_of(ar, ["--version"], r"aria2 version (\S+)") if ar else None})
    return items


def _spec_dict(cid: str) -> dict:
    s = SPECS[cid]
    return {"id": s.id, "name": s.name, "description": s.description,
            "required_for": s.required_for, "optional": s.optional}


# -------------------------------------------------------------- installers
GITHUB_API = "https://api.github.com/repos/{repo}/releases/latest"
REALESRGAN_URL = ("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/"
                  "realesrgan-ncnn-vulkan-20220424-windows.zip")
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
YTDLP_SUMS = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/SHA2-256SUMS"
ARIA2_URL = "https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip"


def _client() -> httpx.Client:
    return httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30, read=120),
                        headers={"User-Agent": "MediaLab-ComponentManager"})


_progress_cb: Callable[[int, int], None] | None = None  # set by install() while it holds the lock


def _download(url: str, dest: Path, cid: str, progress: Callable[[int, int], None] | None = None) -> str:
    """Stream ``url`` to ``dest``; returns the SHA-256 of the file."""
    progress = progress or _progress_cb
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    tmp = dest.with_suffix(dest.suffix + ".part")
    with _client() as c, c.stream("GET", url) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                bus.publish("component", {"id": cid, "state": "downloading", "downloaded": done, "total": total})
                if progress:
                    progress(done, total)
    os.replace(tmp, dest)
    return h.hexdigest()


def _checksum_from(text: str, filename: str) -> str | None:
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == filename:
            return parts[0].lower()
    return None


def _extract(zip_path: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for member in z.infolist():
            # Zip-slip protection
            dest = (target / member.filename).resolve()
            if not str(dest).startswith(str(target.resolve())):
                raise MediaLabError("bad_archive", "Downloaded archive contains unsafe paths.")
        z.extractall(target)


def is_installed(cid: str) -> bool:
    if cid == "ffmpeg":
        return bool(find_binary("ffmpeg") and find_binary("ffprobe"))
    if cid == "deno":
        return js_runtime() is not None
    if cid == "aipack":
        from . import ai_assets
        return ai_assets.status()["installed"]
    spec = SPECS.get(cid)
    return bool(spec and spec.binaries and all(find_binary(b) for b in spec.binaries))


def ensure(cid: str, progress: Callable[[int, int], None] | None = None) -> bool:
    """Install a missing component on demand (waits if another install is running).

    Returns True when the component is available afterwards. Never raises for
    network problems: callers decide whether the component is mandatory.
    """
    if is_installed(cid):
        return True
    try:
        install(cid, progress=progress, wait=True)
    except MediaLabError as e:
        log.warning("On-demand install of %s failed: %s", cid, e.message)
    _cache.clear()
    return is_installed(cid)


def install(cid: str, progress: Callable[[int, int], None] | None = None, wait: bool = False) -> dict:
    global _progress_cb
    if cid not in SPECS:
        raise MediaLabError("unknown_component", f"Unknown component {cid}")
    if not _install_lock.acquire(blocking=wait, timeout=1800 if wait else -1):
        raise MediaLabError("busy", "Another component is being installed.", "Wait for it to finish.", True)
    if wait and is_installed(cid):  # installed by whoever held the lock
        _install_lock.release()
        return next(s for s in status() if s["id"] == cid)
    _progress_cb = progress
    try:
        bus.publish("component", {"id": cid, "state": "starting"})
        log.info("Installing component %s", cid)
        {"ffmpeg": _install_ffmpeg, "realesrgan": _install_realesrgan, "deno": _install_deno,
         "ytdlp": _install_ytdlp, "aria2": _install_aria2, "realcugan": _install_realcugan,
         "rife": _install_rife, "aipack": _install_aipack, "whisper": _install_whisper,
         "llama": _install_llama}[cid]()
        _cache.clear()
        bus.publish("component", {"id": cid, "state": "installed"})
        log.info("Component %s installed", cid)
        return next(s for s in status() if s["id"] == cid)
    except MediaLabError as e:
        bus.publish("component", {"id": cid, "state": "failed", "error": e.to_dict()})
        raise
    except Exception as e:  # network / zip errors
        log.exception("Component %s install failed", cid)
        err = MediaLabError("component_install_failed", f"Could not install {SPECS[cid].name}.",
                            "Check your internet connection and retry.", True, str(e))
        bus.publish("component", {"id": cid, "state": "failed", "error": err.to_dict()})
        raise err from e
    finally:
        _progress_cb = None
        _install_lock.release()


def _install_ffmpeg() -> None:
    with _client() as c:
        rel = c.get(GITHUB_API.format(repo="BtbN/FFmpeg-Builds")).json()
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    # Newest *release branch* GPL build (NVENC/QSV/AMF, zimg, libplacebo included), "shared" flavour:
    # ffmpeg + ffprobe share their DLLs (~150 MB on disk instead of ~480 MB).
    release = sorted((n for n in assets if re.fullmatch(r"ffmpeg-n[\d.]+-latest-win64-gpl-shared-[\d.]+\.zip", n)),
                     key=lambda n: [int(x) for x in re.findall(r"\d+", n.split("-gpl-shared-")[1])], reverse=True)
    name = release[0] if release else "ffmpeg-master-latest-win64-gpl-shared.zip"
    if name not in assets:
        raise MediaLabError("component_install_failed", "No FFmpeg build found for Windows x64.")
    zip_path = paths().temp / name
    digest = _download(assets[name], zip_path, "ffmpeg")
    sums_url = assets.get("checksums.sha256")
    if sums_url:
        with _client() as c:
            expected = _checksum_from(c.get(sums_url).text, name)
        if expected and expected != digest:
            raise MediaLabError("checksum_mismatch", "FFmpeg download is corrupted (checksum mismatch).",
                                "Retry the installation.", True)
    bus.publish("component", {"id": "ffmpeg", "state": "extracting"})
    target = paths().components / "ffmpeg"
    _extract_members(zip_path, target, lambda rel: rel.startswith("bin/") and not rel.startswith("bin/ffplay")
                     or rel in ("LICENSE.txt",))
    zip_path.unlink(missing_ok=True)


def _install_realesrgan() -> None:
    zip_path = paths().temp / "realesrgan.zip"
    _download(REALESRGAN_URL, zip_path, "realesrgan")
    bus.publish("component", {"id": "realesrgan", "state": "extracting"})
    target = paths().components / "realesrgan"
    _extract(zip_path, target)
    for demo in ("input.jpg", "input2.jpg", "onepiece_demo.mp4", "vcomp140d.dll"):  # sample files, debug DLL
        (target / demo).unlink(missing_ok=True)
    zip_path.unlink(missing_ok=True)


REALCUGAN_URL = ("https://github.com/nihui/realcugan-ncnn-vulkan/releases/download/20220728/"
                 "realcugan-ncnn-vulkan-20220728-windows.zip")
REALCUGAN_SHA = "c6e08d46c11704b1e3a1ada9ddd591cb5005f52f132136c8633ba25def400e01"
RIFE_URL = "https://github.com/nihui/rife-ncnn-vulkan/releases/download/20221029/rife-ncnn-vulkan-20221029-windows.zip"
RIFE_SHA = "d8e4d772d26cd8006ef0ad0bc82eb191b53c68677d1ae2f42506d74cbbbea606"


def _extract_members(zip_path: Path, target: Path, keep: Callable[[str], bool]) -> None:
    """Extract only the needed files of an archive, flattening its top-level folder."""
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for member in z.infolist():
            parts = member.filename.split("/", 1)
            rel = parts[1] if len(parts) == 2 else parts[0]
            if not rel or member.is_dir() or not keep(rel):
                continue
            dest = (target / rel).resolve()
            if not str(dest).startswith(str(target.resolve())):
                raise MediaLabError("bad_archive", "Downloaded archive contains unsafe paths.")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with z.open(member) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)


def _verified_zip(url: str, sha: str, cid: str) -> Path:
    zip_path = paths().temp / f"{cid}.zip"
    digest = _download(url, zip_path, cid)
    if digest != sha:
        zip_path.unlink(missing_ok=True)
        raise MediaLabError("checksum_mismatch", f"{SPECS[cid].name} download is corrupted (checksum mismatch).",
                            "Retry the installation.", True)
    return zip_path


def _install_realcugan() -> None:
    z = _verified_zip(REALCUGAN_URL, REALCUGAN_SHA, "realcugan")
    bus.publish("component", {"id": "realcugan", "state": "extracting"})
    _extract_members(z, paths().components / "realcugan",
                     lambda rel: rel.startswith("models-se/") or rel in ("realcugan-ncnn-vulkan.exe", "vcomp140.dll", "LICENSE"))
    z.unlink(missing_ok=True)


def _install_rife() -> None:
    z = _verified_zip(RIFE_URL, RIFE_SHA, "rife")
    bus.publish("component", {"id": "rife", "state": "extracting"})
    _extract_members(z, paths().components / "rife",
                     lambda rel: rel.startswith("rife-v4.6/") or rel in ("rife-ncnn-vulkan.exe", "vcomp140.dll", "LICENSE"))
    z.unlink(missing_ok=True)


# Official ggml-org builds. whisper.cpp publishes no Windows Vulkan build: the CPU + OpenBLAS one runs everywhere.
WHISPER_URL = "https://github.com/ggml-org/whisper.cpp/releases/download/b5130/whisper-blas-bin-x64.zip"
WHISPER_SHA = "55c06d09e8b9b6cfb2b0b47ddedc71803054f0e48be1f41848b3141c06c703a9"
LLAMA_URL = "https://github.com/ggml-org/llama.cpp/releases/download/b11284/llama-b11284-bin-win-vulkan-x64.zip"
LLAMA_SHA = "69c70371c2c9fe3e80859cdbb48596aed3e4265627e470a90d5a9f66dd6779d5"


def _install_whisper() -> None:
    z = _verified_zip(WHISPER_URL, WHISPER_SHA, "whisper")
    bus.publish("component", {"id": "whisper", "state": "extracting"})
    _extract_members(z, paths().components / "whisper", lambda rel: (
        rel in ("whisper-cli.exe", "whisper-vad-speech-segments.exe")
        or (rel.endswith(".dll") and not rel.startswith(("parakeet", "llama", "SDL2")))))
    z.unlink(missing_ok=True)


def _install_llama() -> None:
    z = _verified_zip(LLAMA_URL, LLAMA_SHA, "llama")
    bus.publish("component", {"id": "llama", "state": "extracting"})
    _extract_members(z, paths().components / "llama",
                     lambda rel: rel == "llama-server.exe" or rel.endswith(".dll") or rel.upper().startswith("LICENSE"))
    z.unlink(missing_ok=True)


def _install_aipack() -> None:
    from . import ai_assets
    ai_assets.install_missing(_download, progress=_progress_cb)


def _install_ytdlp() -> None:
    target = paths().components / "ytdlp" / "yt-dlp.zip"
    tmp = target.with_name("yt-dlp.download")
    digest = _download(YTDLP_URL, tmp, "ytdlp")
    with _client() as c:
        expected = _checksum_from(c.get(YTDLP_SUMS).text, "yt-dlp")
    if expected and expected != digest:
        tmp.unlink(missing_ok=True)
        raise MediaLabError("checksum_mismatch", "yt-dlp download is corrupted (checksum mismatch).",
                            "Retry the update.", True)
    if not zipfile.is_zipfile(tmp):
        tmp.unlink(missing_ok=True)
        raise MediaLabError("bad_archive", "Downloaded yt-dlp engine is not valid.")
    os.replace(tmp, target)
    bus.publish("component", {"id": "ytdlp", "state": "restart_required"})


DENO_URL = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"


def _sha256_in(text: str) -> str | None:
    """First SHA-256 hex digest in a checksum file (``sha256sum`` or PowerShell ``Get-FileHash`` layout)."""
    m = re.search(r"([0-9a-fA-F]{64})", text or "")
    return m.group(1).lower() if m else None


def _pinned(url: str) -> str:
    """Resolve a ``releases/latest/download`` link to its versioned URL, so the file and its
    checksum always come from the same release."""
    try:
        with httpx.Client(follow_redirects=False, timeout=20, headers={"User-Agent": "MediaLab-ComponentManager"}) as c:
            r = c.head(url)
            loc = r.headers.get("location", "")
            if r.status_code in (301, 302, 307, 308) and "/releases/download/" in loc:
                return loc
    except httpx.HTTPError:
        pass
    return url


def _install_deno() -> None:
    zip_path = paths().temp / "deno.zip"
    url = _pinned(DENO_URL)
    digest = _download(url, zip_path, "deno")
    try:
        with _client() as c:
            r = c.get(url + ".sha256sum")
            expected = _sha256_in(r.text) if r.status_code == 200 else None
    except httpx.HTTPError:
        expected = None
    if expected and expected != digest:
        raise MediaLabError("checksum_mismatch", "Deno download is corrupted (checksum mismatch).", "Retry.", True)
    _extract(zip_path, paths().components / "deno")
    zip_path.unlink(missing_ok=True)


def js_runtime() -> tuple[str, str] | None:
    """JavaScript runtime for yt-dlp's YouTube challenge solver: Deno preferred, Node accepted."""
    deno = find_binary("deno")
    if deno:
        return "deno", deno
    for name in ("node", "bun"):
        p = shutil.which(name)
        if p:
            return name, p
    return None


def _install_aria2() -> None:
    zip_path = paths().temp / "aria2.zip"
    _download(ARIA2_URL, zip_path, "aria2")
    _extract(zip_path, paths().components / "aria2")
    zip_path.unlink(missing_ok=True)


def activate_engine_override() -> None:
    """Put a downloaded yt-dlp zipapp in front of the bundled one.

    Must run before anything imports ``yt_dlp``.
    """
    engine = paths().components / "ytdlp" / "yt-dlp.zip"
    if engine.exists() and zipfile.is_zipfile(engine) and "yt_dlp" not in sys.modules:
        sys.path.insert(0, str(engine))
