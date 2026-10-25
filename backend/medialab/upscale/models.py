"""AI model registry.

Each model declares its backend, native scales and categories. Categories
(General / Anime / Realistic / Face / Detail) are *tags*: the UI lists the
installed models for a category and says clearly when none is installed —
we never pretend a model exists or rank models without evidence.

Custom models:
* ncnn:  drop ``name.param`` + ``name.bin`` into ``<data>/models/ncnn`` and
         optionally ``name.json`` ({"name","scale","categories","description"}).
* torch: drop ``.pth`` / ``.safetensors`` into ``<data>/models/torch``
         (any architecture supported by spandrel: ESRGAN, SwinIR, HAT, DAT...).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .. import components
from ..config import paths

CATEGORIES = ("general", "anime", "realistic", "face", "detail")


@dataclass
class ModelInfo:
    id: str
    name: str
    backend: str  # ncnn | torch | classic
    scales: tuple[int, ...]
    categories: tuple[str, ...]
    description: str
    video_suited: bool = False
    source: str = ""
    license: str = ""
    files: list[str] = field(default_factory=list)
    installed: bool = False
    ncnn_name: str | None = None  # value for -n
    ncnn_dir: str | None = None
    path: str | None = None  # torch weights
    asset: str | None = None  # ONNX model id (see ai_assets)
    speed: str = ""  # fast | medium | slow (relative, for the UI)
    noncommercial: bool = False
    notes: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["scales"] = list(self.scales)
        d["categories"] = list(self.categories)
        return d


BUILTIN: list[ModelInfo] = [
    ModelInfo(
        id="realesrgan-x4plus", name="Real-ESRGAN x4plus", backend="ncnn", scales=(4,),
        categories=("general", "realistic", "face"), speed="medium",
        description="General-purpose model trained on synthetic real-world degradations (photos, live-action).",
        source="https://github.com/xinntao/Real-ESRGAN", license="BSD-3-Clause",
        files=["realesrgan-x4plus.param", "realesrgan-x4plus.bin"], ncnn_name="realesrgan-x4plus",
        notes="Can over-smooth fine textures; combine with Detail recovery if needed.",
    ),
    ModelInfo(
        id="realesrgan-x4plus-anime", name="Real-ESRGAN x4plus Anime", backend="ncnn", scales=(4,),
        categories=("anime",), speed="medium",
        description="Optimized for anime and illustrations (smaller network, clean lines).",
        source="https://github.com/xinntao/Real-ESRGAN", license="BSD-3-Clause",
        files=["realesrgan-x4plus-anime.param", "realesrgan-x4plus-anime.bin"], ncnn_name="realesrgan-x4plus-anime",
    ),
    ModelInfo(
        id="realesr-animevideov3", name="Real-ESRGAN AnimeVideo v3", backend="ncnn", scales=(2, 3, 4),
        categories=("anime",), video_suited=True, speed="fast",
        description="Compact model designed for anime video: fast, native 2x/3x/4x.",
        source="https://github.com/xinntao/Real-ESRGAN", license="BSD-3-Clause",
        files=[f"realesr-animevideov3-x{s}.{e}" for s in (2, 3, 4) for e in ("param", "bin")],
        ncnn_name="realesr-animevideov3",
    ),
    ModelInfo(
        id="realcugan-se", name="Real-CUGAN", backend="cugan", scales=(2, 3, 4),
        categories=("anime",), video_suited=True, speed="fast",
        description="Anime / cartoons: very clean lines and flat colors, native 2x/3x/4x (conservative mode).",
        source="https://github.com/bilibili/ailab", license="MIT", ncnn_name="models-se",
    ),
    ModelInfo(
        id="onnx:ultra_sharp_2_x4", name="4x-UltraSharp V2", backend="onnx", scales=(4,),
        categories=("detail", "general"), asset="ultra_sharp_2_x4", speed="slow", noncommercial=True,
        description="Maximum sharpness and fine textures (photos, renders, screenshots). Slower.",
        source="https://openmodeldb.info/models/4x-UltraSharpV2", license="CC BY-NC-SA 4.0",
    ),
    ModelInfo(
        id="onnx:ultra_sharp_x4", name="4x-UltraSharp", backend="onnx", scales=(4,),
        categories=("detail", "general"), asset="ultra_sharp_x4", speed="medium", noncommercial=True,
        description="Crisp details on compressed images; the classic detail model.",
        source="https://openmodeldb.info/models/4x-UltraSharp", license="CC BY-NC-SA 4.0",
    ),
    ModelInfo(
        id="onnx:real_hatgan_x4", name="Real-HAT-GAN", backend="onnx", scales=(4,),
        categories=("realistic", "detail", "face"), asset="real_hatgan_x4", speed="slow",
        description="Transformer model: the most natural result on real photos. Slowest.",
        source="https://github.com/XPixelGroup/HAT", license="MIT",
    ),
    ModelInfo(
        id="onnx:nomos8k_sc_x4", name="Nomos8k SC", backend="onnx", scales=(4,),
        categories=("realistic", "face"), asset="nomos8k_sc_x4", speed="medium",
        description="Photographs: natural skin and textures, removes JPEG artifacts.",
        source="https://openmodeldb.info/models/4x-Nomos8kSC", license="CC BY 4.0",
    ),
    ModelInfo(
        id="onnx:clear_reality_x4", name="ClearReality", backend="onnx", scales=(4,),
        categories=("realistic", "general"), asset="clear_reality_x4", speed="fast", video_suited=True,
        noncommercial=True, description="Very fast realistic model, good for long videos.",
        source="https://openmodeldb.info/models/4x-ClearRealityV1", license="CC BY-NC-SA 4.0",
    ),
    ModelInfo(
        id="onnx:span_kendata_x4", name="SPAN", backend="onnx", scales=(4,),
        categories=("general",), asset="span_kendata_x4", speed="fast", video_suited=True,
        description="Tiny and extremely fast general model; ideal for video previews and older GPUs.",
        source="https://github.com/hongyuanyu/SPAN", license="Apache-2.0",
    ),
    ModelInfo(
        id="onnx:real_esrgan_x2_fp16", name="Real-ESRGAN x2plus", backend="onnx", scales=(2,),
        categories=("general", "realistic"), asset="real_esrgan_x2_fp16", speed="fast", video_suited=True,
        description="Native 2x: best choice for a 2x upscale (no 4x-then-shrink).",
        source="https://github.com/xinntao/Real-ESRGAN", license="BSD-3-Clause",
    ),
    ModelInfo(
        id="classic-lanczos", name="Classic (Lanczos, no AI)", backend="classic", scales=(2, 3, 4, 8),
        categories=("general",), video_suited=True, installed=True, speed="fast",
        description="Traditional high-quality resampling. Instant, no GPU needed, adds no detail.",
        license="—",
    ),
]


def _ncnn_model_dirs() -> list[Path]:
    dirs = []
    exe = components.find_binary("realesrgan-ncnn-vulkan")
    if exe:
        dirs.append(Path(exe).parent / "models")
    dirs.append(paths().models / "ncnn")
    return dirs


def _custom_ncnn() -> list[ModelInfo]:
    out = []
    root = paths().models / "ncnn"
    if not root.exists():
        return out
    builtin_names = {m.ncnn_name for m in BUILTIN if m.ncnn_name}
    for param in sorted(root.glob("*.param")):
        stem = param.stem
        if stem in builtin_names or re.sub(r"-x\d$", "", stem) in builtin_names:
            continue
        if not param.with_suffix(".bin").exists():
            continue
        meta = {}
        side = param.with_suffix(".json")
        if side.exists():
            try:
                meta = json.loads(side.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
        m = re.search(r"(\d)x|x(\d)", stem.lower())
        scale = int(meta.get("scale") or (m.group(1) or m.group(2) if m else 4))
        out.append(ModelInfo(
            id=f"ncnn:{stem}", name=meta.get("name") or stem, backend="ncnn", scales=(scale,),
            categories=tuple(c for c in meta.get("categories", ["general"]) if c in CATEGORIES) or ("general",),
            description=meta.get("description") or "Custom ncnn model", installed=True,
            ncnn_name=stem, ncnn_dir=str(root), video_suited=bool(meta.get("video")),
            notes="Custom model — quality depends on the model you provided.",
        ))
    return out


def _custom_torch() -> list[ModelInfo]:
    root = paths().models / "torch"
    out = []
    if not root.exists():
        return out
    for f in sorted([*root.glob("*.pth"), *root.glob("*.safetensors"), *root.glob("*.pt")]):
        side = f.with_suffix(".json")
        meta = {}
        if side.exists():
            try:
                meta = json.loads(side.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
        m = re.search(r"(\d)x|x(\d)", f.stem.lower())
        scale = int(meta.get("scale") or (m.group(1) or m.group(2) if m else 4))
        lower = f.stem.lower()
        cats = meta.get("categories") or (["face"] if any(k in lower for k in ("gfpgan", "codeformer", "restoreformer"))
                                          else ["anime"] if "anime" in lower else ["detail"] if any(
                                              k in lower for k in ("ultrasharp", "detail", "hat", "dat", "swinir"))
                                          else ["general"])
        out.append(ModelInfo(
            id=f"torch:{f.stem}", name=meta.get("name") or f.stem, backend="torch", scales=(scale,),
            categories=tuple(c for c in cats if c in CATEGORIES) or ("general",),
            description=meta.get("description") or "PyTorch model loaded through spandrel",
            path=str(f), installed=True, video_suited=bool(meta.get("video")),
            notes="Scale/architecture detected when the model loads.",
        ))
    return out


def _cugan_dir() -> Path | None:
    exe = components.find_binary("realcugan-ncnn-vulkan")
    if exe and (Path(exe).parent / "models-se").is_dir():
        return Path(exe).parent / "models-se"
    return None


def list_models() -> list[ModelInfo]:
    from .. import ai_assets
    dirs = _ncnn_model_dirs()
    models: list[ModelInfo] = []
    for m in BUILTIN:
        m = ModelInfo(**{**asdict(m), "scales": m.scales, "categories": m.categories})
        if m.backend == "onnx":
            m.installed = ai_assets.path_of(m.asset or "") is not None
        elif m.backend == "cugan":
            d = _cugan_dir()
            m.installed = d is not None
            m.ncnn_dir = str(d) if d else None
        elif m.backend == "ncnn":
            for d in dirs:
                if all((d / f).exists() for f in m.files[:2]):
                    m.installed = True
                    m.ncnn_dir = str(d)
                    break
        models.append(m)
    models.extend(_custom_ncnn())
    models.extend(_custom_torch())
    return models


def get_model(model_id: str) -> ModelInfo | None:
    return next((m for m in list_models() if m.id == model_id), None)


def categories_summary() -> list[dict]:
    models = list_models()
    out = []
    for c in CATEGORIES:
        ms = [m for m in models if c in m.categories]
        out.append({"id": c, "installed": [m.id for m in ms if m.installed],
                    "available": [m.id for m in ms]})
    return out
