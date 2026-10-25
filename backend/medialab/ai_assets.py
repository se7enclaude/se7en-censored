"""AI model files shipped with se7en (or downloaded once when missing).

Every file comes from a public GitHub release and is checked against the
SHA-256 pinned below before use. Search order for a model file:

  1. bundled next to the program:   <app>/bin/ai/<file>
  2. downloaded by se7en:            <data>/models/onnx/<file>

Licenses are the ones published by each model's authors; models marked
``noncommercial`` may only be used for personal / non-commercial purposes.
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from .config import app_root, paths
from .errors import MediaLabError
from .events import bus
from .logging_setup import get_logger

log = get_logger("app")
FF = "https://github.com/facefusion/facefusion-assets/releases/download/"


@dataclass(frozen=True)
class Asset:
    id: str
    file: str
    url: str
    sha256: str
    size: int
    purpose: str  # upscale | face | detect | background | colorize | audio | speech | llm
    license: str
    author: str
    noncommercial: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


ASSETS: dict[str, Asset] = {a.id: a for a in [
    Asset("ultra_sharp_x4", "ultra_sharp_x4.onnx", FF + "models-3.0.0/ultra_sharp_x4.onnx",
          "c83e0bf4fe1051ec7f2330614aa2edef0ad589d59c718934897a93459b180dd1", 67002795, "upscale",
          "CC BY-NC-SA 4.0", "Kim2091 (4x-UltraSharp)", True),
    Asset("ultra_sharp_2_x4", "ultra_sharp_2_x4.onnx", FF + "models-3.3.0/ultra_sharp_2_x4.onnx",
          "6c0201e3403745f39a9aa5273c50ed084cf6c4af5a71a4654c30252cf19bf0a5", 51800517, "upscale",
          "CC BY-NC-SA 4.0", "Kim2091 (4x-UltraSharpV2)", True),
    Asset("nomos8k_sc_x4", "nomos8k_sc_x4.onnx", FF + "models-3.0.0/nomos8k_sc_x4.onnx",
          "7747a7dd0b115761cf9c34bbf67bb1e98218ff5b459492a13215c8deb046a9d0", 67002465, "upscale",
          "CC BY 4.0", "Helaman (4xNomos8kSC)"),
    Asset("real_hatgan_x4", "real_hatgan_x4.onnx", FF + "models-3.0.0/real_hatgan_x4.onnx",
          "5e7d652fa82b16ad60b825a1c290bd020c082a45b7cbd617b9cb321934ee707f", 160689081, "upscale",
          "MIT", "XPixel Group (Real-HAT-GAN)"),
    Asset("clear_reality_x4", "clear_reality_x4.onnx", FF + "models-3.0.0/clear_reality_x4.onnx",
          "bbce9d5a653281cfae07788d620bd4ec45712709bdf6349a06a893159efd97ce", 1718947, "upscale",
          "CC BY-NC-SA 4.0", "Kim2091 (4x-ClearRealityV1)", True),
    Asset("span_kendata_x4", "span_kendata_x4.onnx", FF + "models-3.0.0/span_kendata_x4.onnx",
          "7478c40953a5902efd785ef1fe8e07a8bb7e7ee9b1977273550813ceefae6348", 1718947, "upscale",
          "Apache-2.0", "hongyuanyu (SPAN) / Kendata"),
    Asset("real_esrgan_x2_fp16", "real_esrgan_x2_fp16.onnx", FF + "models-3.0.0/real_esrgan_x2_fp16.onnx",
          "0218a7177bccafaad11bcee4fcb3aa42a74c8551da195eede6c62645fa8bdcc7", 36146329, "upscale",
          "BSD-3-Clause", "Xintao Wang (Real-ESRGAN x2plus)"),
    Asset("gfpgan_1.4", "gfpgan_1.4.onnx", FF + "models-3.0.0/gfpgan_1.4.onnx",
          "accc4757b26bdb89b32b4d3500d4f79c9dff97c1dd7c7104bf9dcb95e3311385", 340299087, "face",
          "Apache-2.0", "Tencent ARC (GFPGAN v1.4)"),
    Asset("codeformer", "codeformer.onnx", FF + "models-3.0.0/codeformer.onnx",
          "21710e7ab61c82683576c428e9c1b6fe1ed419586b7b39e394c3449c294b550f", 376951650, "face",
          "S-Lab License 1.0", "Shangchen Zhou (CodeFormer)", True),
    Asset("yunet", "yunet_2023_mar.onnx", FF + "models-3.4.0/yunet_2023_mar.onnx",
          "490d79bc15c0e958d91c999c566c6a338b310ead96e0274dd004e59dd6abecb9", 232475, "detect",
          "MIT", "OpenCV Zoo (YuNet)"),
    Asset("isnet_general", "isnet_general.onnx", FF + "models-3.5.0/isnet_general.onnx",
          "8cbb4b9b7f44d99b39d21752daf7164228058cd96d678ab254023c576155ed97", 178682734, "background",
          "Apache-2.0", "Xuebin Qin (IS-Net / DIS)"),
    Asset("deoldify_artistic", "deoldify_artistic.onnx", FF + "models-3.0.0/deoldify_artistic.onnx",
          "9ac296cf05fecbdb604f50211f632b722402bc1f9a96ee5a8987b01c0c3c688f", 255044725, "colorize",
          "MIT", "Jason Antic (DeOldify)"),
    # ---- audio: voice / music separation (MDX-Net) and speech denoising (RNNoise, used by FFmpeg arnndn)
    Asset("kim_vocal_2", "Kim_Vocal_2.onnx",
          "https://github.com/TRvlvr/model_repo/releases/download/all_public_uvr_models/Kim_Vocal_2.onnx",
          "ce74ef3b6a6024ce44211a07be9cf8bc6d87728cc852a68ab34eb8e58cde9c8b", 66759214, "audio",
          "UVR model repository (personal use)", "KimberleyJSN (Kim Vocal 2, Ultimate Vocal Remover)", True),
    Asset("rnnoise_lq", "lq.rnnn",
          "https://raw.githubusercontent.com/GregorR/rnnoise-models/master/leavened-quisling-2018-08-31/lq.rnnn",
          "1957528b752799fddf06270bc5469af7cf54c3badc358544ae2abed730943ff9", 297041, "audio",
          "Public domain (not copyrightable)", "Gregor Richards (rnnoise-models, leavened-quisling)"),
    # ---- subtitles: speech recognition (Whisper large-v3-turbo, 8-bit), voice activity detection, local language model
    Asset("whisper_turbo", "ggml-large-v3-turbo-q8_0.bin",
          "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q8_0.bin",
          "317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1", 874188075, "speech",
          "MIT", "OpenAI (Whisper large-v3-turbo) / ggml-org"),
    Asset("silero_vad", "ggml-silero-v6.2.0.bin",
          "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin",
          "2aa269b785eeb53a82983a20501ddf7c1d9c48e33ab63a41391ac6c9f7fb6987", 885098, "speech",
          "MIT", "Silero Team (Silero VAD v6.2)"),
    Asset("ministral_8b", "Ministral-3-8B-Instruct-2512-Q4_K_M.gguf",
          "https://huggingface.co/mistralai/Ministral-3-8B-Instruct-2512-GGUF/resolve/main/"
          "Ministral-3-8B-Instruct-2512-Q4_K_M.gguf",
          "33e7a72cf5e6e2cfc2f2847075acc013d68bba023e35310cef86b5cf8fdca761", 5198911904, "llm",
          "Apache-2.0", "Mistral AI (Ministral 3 8B Instruct)"),
    # ---- se7en Censored : détecteur de nudité (copie du modèle publié par NudeNet, dans la release « models »)
    Asset("nudenet_640m", "nudenet_640m.onnx",
          "https://github.com/se7enclaude/se7en-censored/releases/download/models/nudenet_640m.onnx",
          "04fe3d77980780c1f8297dc6d7f942fd5b3abe6942a188f742a85241e4f634eb", 103538690, "nsfw",
          "AGPL-3.0", "notAI-tech (NudeNet v3.4, détecteur 640m)"),
]}

_lock = threading.Lock()


def _dirs() -> list[Path]:
    from .config import shared_data_dirs
    return [app_root() / "bin" / "ai", paths().models / "onnx", *(d / "models" / "onnx" for d in shared_data_dirs())]


def path_of(asset_id: str) -> Path | None:
    a = ASSETS.get(asset_id)
    if not a:
        return None
    for d in _dirs():
        p = d / a.file
        if p.is_file() and p.stat().st_size == a.size:
            return p
    return None


def require(asset_id: str) -> Path:
    p = path_of(asset_id)
    if not p:
        a = ASSETS[asset_id]
        raise MediaLabError("model_missing", f"The AI model “{a.author}” is not installed.",
                            "Install the AI modules from Settings › Components (one click).",
                            extra={"component": "aipack"})
    return p


def ensure(asset_id: str, progress: Callable[[int, int], None] | None = None) -> Path:
    """Path of a model, downloading (and verifying) just this one when missing: copies updated from an older
    version get new models the first time a tool needs them."""
    p = path_of(asset_id)
    if p:
        return p
    from . import components
    a = ASSETS[asset_id]
    with _lock:
        if (p := path_of(asset_id)):
            return p
        target = paths().models / "onnx"
        target.mkdir(parents=True, exist_ok=True)
        tmp = target / (a.file + ".download")
        log.info("Downloading AI model %s (%.0f MB)", a.file, a.size / 1e6)
        try:
            digest = components._download(a.url, tmp, "aipack-file", progress)
        except Exception as e:
            tmp.unlink(missing_ok=True)
            raise MediaLabError("model_download_failed", f"The AI model {a.file} could not be downloaded.",
                                "Check your internet connection and retry.", True, str(e)) from e
        if digest != a.sha256:
            tmp.unlink(missing_ok=True)
            raise MediaLabError("checksum_mismatch", f"The AI model {a.file} is corrupted (checksum mismatch).",
                                "Retry.", True)
        tmp.replace(target / a.file)
        return target / a.file


# se7en Censored n'a besoin que du détecteur : les autres modèles (hérités de se7en Download) ne sont jamais proposés
REQUIRED = {"nsfw"}
ON_DEMAND = {p for p in {a.purpose for a in ASSETS.values()} if p not in REQUIRED}


def status() -> dict:
    items = [{**a.to_dict(), "installed": path_of(a.id) is not None} for a in ASSETS.values()]
    missing = [i for i in items if not i["installed"] and i["purpose"] not in ON_DEMAND]
    return {"items": items, "installed": not missing, "missing_bytes": sum(i["size"] for i in missing),
            "total_bytes": sum(i["size"] for i in items)}


def install_missing(download: Callable[[str, Path, str, Callable[[int, int], None] | None], str],
                    progress: Callable[[int, int], None] | None = None) -> None:
    """Download every missing model (verified). ``download`` streams a URL and returns its SHA-256."""
    with _lock:
        todo = [a for a in ASSETS.values() if path_of(a.id) is None and a.purpose not in ON_DEMAND]
        total = sum(a.size for a in todo)
        done = 0
        target = paths().models / "onnx"
        target.mkdir(parents=True, exist_ok=True)
        for a in todo:
            base = done

            def cb(d: int, _t: int, base=base) -> None:
                bus.publish("component", {"id": "aipack", "state": "downloading", "downloaded": base + d,
                                          "total": total, "file": a.file})
                if progress:
                    progress(base + d, total)

            tmp = target / (a.file + ".download")
            digest = download(a.url, tmp, "aipack-file", cb)
            if digest != a.sha256:
                tmp.unlink(missing_ok=True)
                raise MediaLabError("checksum_mismatch", f"The AI model {a.file} is corrupted (checksum mismatch).",
                                    "Retry the installation.", True)
            tmp.replace(target / a.file)
            done += a.size
            log.info("AI model installed: %s", a.file)


def verify_file(path: Path, sha256: str) -> bool:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(4 << 20):
            h.update(chunk)
    return h.hexdigest() == sha256
