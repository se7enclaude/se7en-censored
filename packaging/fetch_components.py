"""Bundle every component into dist/se7en/bin so se7en works right after install, offline.

    backend\\.venv\\Scripts\\python packaging\\fetch_components.py

  bin/ffmpeg      FFmpeg (shared GPL build: NVENC / Quick Sync / AMF)
  bin/deno        JavaScript runtime used by yt-dlp for YouTube
  bin/realesrgan  Real-ESRGAN ncnn-Vulkan + models
  bin/realcugan   Real-CUGAN ncnn-Vulkan (anime)
  bin/rife        RIFE v4.6 (frame interpolation)
  bin/ai          ONNX models (faces, background, colorization, detail upscalers)

Everything is downloaded from the official GitHub releases, checked against its
SHA-256 (pinned in medialab/ai_assets.py and medialab/components.py), and cached
in build/component-cache so later builds do not download it again.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
DIST = ROOT / "dist" / "se7en-censored"
BIN = DIST / "bin"
CACHE = ROOT / "build" / "component-cache"
COMPONENTS = ("ffmpeg", "deno")  # FFmpeg (lecture, export NVENC) ; Deno pour les liens YouTube
# Large subtitle models are downloaded by se7en the first time they are needed (keeps the installer small)
NOT_BUNDLED = {"speech", "llm"}
# se7en Censored n'utilise que le détecteur de nudité (les autres modèles servent à se7en Download)
BUNDLED_AI = {"nudenet_640m"}


def main() -> int:
    if not DIST.exists():
        print("Run PyInstaller first (dist/se7en missing).")
        return 1
    CACHE.mkdir(parents=True, exist_ok=True)
    os.environ["SE7ENCENSORED_DATA_DIR"] = str(CACHE / "data")
    from medialab import ai_assets, components

    for cid in COMPONENTS:
        src = CACHE / "data" / "components" / cid
        if not (src.exists() and any(src.rglob("*.exe"))):
            print(f"Downloading {cid}…", flush=True)
            components.install(cid)
        else:
            print(f"{cid}: cached", flush=True)
        dst = BIN / cid
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst)
        print(f"  -> {dst}  ({sum(f.stat().st_size for f in dst.rglob('*') if f.is_file()) / 1e6:.0f} MB)", flush=True)

    # ONNX models: reuse local copies (build/assets from development) when their checksum matches.
    ai_dst = BIN / "ai"
    ai_dst.mkdir(parents=True, exist_ok=True)
    local = [ROOT / "build" / "assets", CACHE / "data" / "models" / "onnx"]
    for a in ai_assets.ASSETS.values():
        if a.id not in BUNDLED_AI:
            continue
        target = ai_dst / a.file
        if target.exists() and target.stat().st_size == a.size:
            continue
        found = next((d / a.file for d in local if (d / a.file).is_file() and ai_assets.verify_file(d / a.file, a.sha256)), None)
        if not found:
            print(f"Downloading {a.file}…", flush=True)
            tmp = Path(tempfile.mkdtemp()) / a.file
            digest = components._download(a.url, tmp, "aipack")
            if digest != a.sha256:
                print(f"Checksum mismatch for {a.file}")
                return 2
            found = tmp
        shutil.copy2(found, target)
    total = sum(f.stat().st_size for f in ai_dst.iterdir())
    print(f"AI models -> {ai_dst}  ({total / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
