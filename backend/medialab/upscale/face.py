"""Face restoration: detect → align → restore (GFPGAN / CodeFormer) → paste back.

* detection: YuNet (OpenCV Zoo, MIT) gives a box and 5 landmarks per face;
* alignment: similarity transform of the landmarks onto the FFHQ 512x512
  template the restoration models were trained on;
* restoration: GFPGAN v1.4 or CodeFormer (ONNX, DirectML GPU / CPU);
* paste back: the restored face is warped back and blended with a feathered
  mask, so only the face changes and there is no visible edge.

``strength`` blends the restored face with the original one (identity and
skin texture are better kept below 1.0).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageFilter

from .. import ai_assets
from ..errors import MediaLabError
from ..logging_setup import get_logger
from . import onnx_rt

log = get_logger("gpu")

TEMPLATE_512 = np.array([[192.98138, 239.94708], [318.90277, 240.1936], [256.63416, 314.01935],
                         [201.26117, 371.41043], [313.08905, 371.15118]], dtype=np.float64)
DET_SIZE = 640
_mask_cache: dict[int, Image.Image] = {}
_lock = threading.Lock()


@dataclass
class Face:
    box: tuple[float, float, float, float]  # x1, y1, x2, y2
    landmarks: np.ndarray  # 5x2 (right eye, left eye, nose, right mouth, left mouth) in image pixels
    score: float


def models() -> dict:
    return {"gfpgan": ai_assets.path_of("gfpgan_1.4") is not None,
            "codeformer": ai_assets.path_of("codeformer") is not None,
            "detector": ai_assets.path_of("yunet") is not None}


def available() -> tuple[bool, str]:
    m = models()
    if not m["detector"] or not (m["gfpgan"] or m["codeformer"]):
        return False, "Face models are not installed (Settings › Components › AI modules)."
    ok, info = onnx_rt.available()
    return ok, f"GFPGAN / CodeFormer · {info}" if ok else info


# ------------------------------------------------------------------ detection
def _nms(boxes: np.ndarray, scores: np.ndarray, iou: float) -> list[int]:
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size:
        i = order[0]
        keep.append(int(i))
        xx1 = np.maximum(boxes[i, 0], boxes[order[1:], 0])
        yy1 = np.maximum(boxes[i, 1], boxes[order[1:], 1])
        xx2 = np.minimum(boxes[i, 2], boxes[order[1:], 2])
        yy2 = np.minimum(boxes[i, 3], boxes[order[1:], 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_o = (boxes[order[1:], 2] - boxes[order[1:], 0]) * (boxes[order[1:], 3] - boxes[order[1:], 1])
        ratio = inter / np.maximum(1e-6, area_i + area_o - inter)
        order = order[1:][ratio <= iou]
    return keep


def detect(rgb: np.ndarray, threshold: float = 0.6, device: str = "auto") -> list[Face]:
    """Faces in an RGB uint8 image (any size), strongest first."""
    path = ai_assets.require("yunet")
    h, w = rgb.shape[:2]
    scale = DET_SIZE / max(h, w)
    nh, nw = max(1, round(h * scale)), max(1, round(w * scale))
    small = np.asarray(Image.fromarray(rgb).resize((nw, nh), Image.Resampling.BILINEAR))
    canvas = np.zeros((DET_SIZE, DET_SIZE, 3), dtype=np.uint8)
    canvas[:nh, :nw] = small
    bgr = canvas[:, :, ::-1].astype(np.float32).transpose(2, 0, 1)[None]
    outs = onnx_rt.run(path, {"input": np.ascontiguousarray(bgr)}, device)
    boxes, scores, kpss = [], [], []
    for i, stride in enumerate((8, 16, 32)):
        cls, obj, bbox, kps = outs[i][0], outs[3 + i][0], outs[6 + i][0], outs[9 + i][0]
        cols = DET_SIZE // stride
        score = np.sqrt(np.clip(cls[:, 0], 0, 1) * np.clip(obj[:, 0], 0, 1))
        idx = np.where(score >= threshold)[0]
        if not idx.size:
            continue
        r, c = idx // cols, idx % cols
        cx = (c + bbox[idx, 0]) * stride
        cy = (r + bbox[idx, 1]) * stride
        bw = np.exp(bbox[idx, 2]) * stride
        bh = np.exp(bbox[idx, 3]) * stride
        boxes.append(np.stack([cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2], axis=1))
        scores.append(score[idx])
        k = kps[idx].reshape(-1, 5, 2).copy()
        k[:, :, 0] = (k[:, :, 0] + c[:, None]) * stride
        k[:, :, 1] = (k[:, :, 1] + r[:, None]) * stride
        kpss.append(k)
    if not boxes:
        return []
    b, s, k = np.concatenate(boxes), np.concatenate(scores), np.concatenate(kpss)
    keep = _nms(b, s, 0.3)
    faces = [Face(tuple((b[i] / scale).tolist()), k[i] / scale, float(s[i])) for i in keep]
    return sorted(faces, key=lambda f: -f.score)


# ------------------------------------------------------------------ alignment
def similarity(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """2x3 similarity transform (rotation, uniform scale, translation) mapping src -> dst (Umeyama)."""
    n = src.shape[0]
    mu_s, mu_d = src.mean(0), dst.mean(0)
    s_c, d_c = src - mu_s, dst - mu_d
    cov = d_c.T @ s_c / n
    u, sig, vt = np.linalg.svd(cov)
    d = np.eye(2)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        d[1, 1] = -1
    rot = u @ d @ vt
    var = (s_c ** 2).sum() / n
    scale = (sig * np.diag(d)).sum() / var
    t = mu_d - scale * rot @ mu_s
    return np.hstack([scale * rot, t[:, None]])


def _invert(m: np.ndarray) -> np.ndarray:
    full = np.vstack([m, [0, 0, 1]])
    return np.linalg.inv(full)[:2]


def _mask(size: int) -> Image.Image:
    with _lock:
        if size not in _mask_cache:
            m = Image.new("L", (size, size), 0)
            border = int(size * 0.08)
            m.paste(255, (border, border, size - border, size - border))
            _mask_cache[size] = m.filter(ImageFilter.GaussianBlur(size * 0.05))
        return _mask_cache[size]


def is_grayscale(rgb: np.ndarray, tolerance: int = 10) -> bool:
    """True for black & white pictures (also sepia-free scans stored as RGB)."""
    sample = rgb[:: max(1, rgb.shape[0] // 128), :: max(1, rgb.shape[1] // 128)].astype(np.int16)
    spread = sample.max(axis=2) - sample.min(axis=2)
    return float(np.percentile(spread, 98)) <= tolerance


# ------------------------------------------------------------------ restoration
def _restore_crop(crop: np.ndarray, model: str, fidelity: float, device: str) -> np.ndarray:
    x = (crop.astype(np.float32) / 255.0 - 0.5) / 0.5
    x = np.ascontiguousarray(x.transpose(2, 0, 1)[None])
    if model == "codeformer":
        path = ai_assets.require("codeformer")
        y = onnx_rt.run(path, {"input": x, "weight": np.array(fidelity, dtype=np.float64)}, device)[0]
    else:
        path = ai_assets.require("gfpgan_1.4")
        y = onnx_rt.run(path, {"input": x}, device)[0]
    y = np.clip(y[0].transpose(1, 2, 0), -1, 1)
    return ((y * 0.5 + 0.5) * 255.0 + 0.5).astype(np.uint8)


def enhance(rgb: np.ndarray, strength: float = 0.8, model: str = "gfpgan", fidelity: float = 0.6,
            device: str = "auto", min_face: int = 18) -> np.ndarray:
    """Restore every face of an RGB uint8 image; returns a new array (unchanged if no face)."""
    ok, why = available()
    if not ok:
        raise MediaLabError("face_unavailable", "Face enhancement is not available.", why,
                            extra={"component": "aipack"})
    if model == "codeformer" and not models()["codeformer"]:
        model = "gfpgan"
    if model == "gfpgan" and not models()["gfpgan"]:
        model = "codeformer"
    faces = detect(rgb, device=device)
    if not faces:
        return rgb
    gray_source = is_grayscale(rgb)
    img = Image.fromarray(rgb)
    size = 512
    mask = _mask(size)
    strength = max(0.0, min(1.0, float(strength)))
    for f in faces:
        x1, y1, x2, y2 = f.box
        if min(x2 - x1, y2 - y1) < min_face:
            continue
        m = similarity(f.landmarks.astype(np.float64), TEMPLATE_512)  # image -> face
        inv = _invert(m)  # face -> image
        crop = img.transform((size, size), Image.Transform.AFFINE, data=tuple(inv.flatten()),
                             resample=Image.Resampling.BICUBIC)
        restored = Image.fromarray(_restore_crop(np.asarray(crop.convert("RGB")), model, fidelity, device))
        if gray_source:  # the models add colors (blue eyes...) to black & white photos: keep it B&W
            restored = restored.convert("L").convert("RGB")
        # Region of the image covered by the face square.
        corners = np.array([[0, 0, 1], [size, 0, 1], [0, size, 1], [size, size, 1]], dtype=np.float64) @ inv.T
        rx1, ry1 = np.floor(corners.min(0)).astype(int)
        rx2, ry2 = np.ceil(corners.max(0)).astype(int)
        rx1, ry1 = max(0, rx1), max(0, ry1)
        rx2, ry2 = min(img.width, rx2), min(img.height, ry2)
        if rx2 <= rx1 or ry2 <= ry1:
            continue
        # image(region) -> face: shift by the region origin
        shift = m.copy()
        shift[:, 2] += m[:, :2] @ np.array([rx1, ry1], dtype=np.float64)
        rw, rh = rx2 - rx1, ry2 - ry1
        face_back = restored.transform((rw, rh), Image.Transform.AFFINE, data=tuple(shift.flatten()),
                                       resample=Image.Resampling.BICUBIC)
        alpha = mask.transform((rw, rh), Image.Transform.AFFINE, data=tuple(shift.flatten()),
                               resample=Image.Resampling.BILINEAR)
        if strength < 1.0:
            alpha = alpha.point(lambda v: int(v * strength))
        region = img.crop((rx1, ry1, rx2, ry2))
        region.paste(face_back, (0, 0), alpha)
        img.paste(region, (rx1, ry1))
    return np.asarray(img)
