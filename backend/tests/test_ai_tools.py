"""AI tools on real models (skipped when the ONNX models are not available locally)."""
import threading
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from medialab.settings import Settings
from medialab.upscale import background, colorize, engine, face

PORTRAIT = Path(__file__).parent / "data" / "portrait_public_domain.jpg"


def test_similarity_transform_recovers_a_known_transform():
    rng = np.random.default_rng(3)
    src = rng.uniform(0, 500, (5, 2))
    angle, scale, t = 0.3, 1.7, np.array([12.0, -40.0])
    rot = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    dst = (scale * rot @ src.T).T + t
    m = face.similarity(src, dst)
    assert np.allclose(m[:, :2], scale * rot, atol=1e-6) and np.allclose(m[:, 2], t, atol=1e-6)


def test_grayscale_detection():
    gray = np.repeat(np.asarray(Image.open(PORTRAIT).convert("L"))[:, :, None], 3, axis=2)
    assert face.is_grayscale(gray)
    color = np.zeros((64, 64, 3), np.uint8)
    color[..., 0] = 200
    assert not face.is_grayscale(color)


def test_face_detection_and_restoration(ai_models):
    rgb = np.asarray(Image.open(PORTRAIT).convert("RGB"))
    faces = face.detect(rgb)
    assert len(faces) == 1 and faces[0].score > 0.8
    x1, y1, x2, y2 = faces[0].box
    assert 100 < (x1 + x2) / 2 < 400 and 60 < (y1 + y2) / 2 < 400  # the face is in the upper middle
    small = Image.fromarray(rgb).resize((125, 161)).resize((500, 644))
    out = face.enhance(np.asarray(small), 1.0, "gfpgan")
    assert out.shape == rgb.shape
    diff = np.abs(out.astype(int) - np.asarray(small).astype(int)).mean(axis=2)
    assert diff[int(y1):int(y2), int(x1):int(x2)].mean() > 3  # the face changed...
    assert diff[:40, :40].mean() < 0.5  # ...the corner did not
    assert face.is_grayscale(out)  # a black & white photo stays black & white


def test_background_removal(ai_models):
    im = Image.open(PORTRAIT)
    out = background.remove(im)
    assert out.mode == "RGBA" and out.size == im.size
    a = np.asarray(out.getchannel("A"))
    assert a[5:30, 5:30].mean() < 60 and a[250:350, 200:300].mean() > 200  # corner removed, face kept
    solid = background.remove(im, "color", "#F22B2B")
    assert solid.mode == "RGB" and solid.getpixel((5, 5))[0] > 180


def test_colorize(ai_models):
    out = colorize.colorize(Image.open(PORTRAIT))
    arr = np.asarray(out).astype(int)
    assert out.size == Image.open(PORTRAIT).size
    assert (arr.max(axis=2) - arr.min(axis=2)).mean() > 4  # it has colors now


def test_onnx_upscale_x4_and_ops_only_job(ai_models, tmp_path):
    s = Settings()
    src = tmp_path / "in.png"
    Image.open(PORTRAIT).convert("RGB").crop((100, 60, 260, 220)).save(src)
    res = engine.process_image(src, tmp_path / "x4.png", {"model": "onnx:span_kendata_x4", "scale": 4}, s,
                               threading.Event(), lambda *_: None)
    assert (res["width"], res["height"]) == (640, 640)
    res = engine.process_image(src, tmp_path / "nobg.png", {"scale": 1, "remove_bg": True}, s,
                               threading.Event(), lambda *_: None)
    assert (res["width"], res["height"]) == (160, 160) and res["operations"] == ["background"]
    with Image.open(tmp_path / "nobg.png") as im:
        assert im.mode == "RGBA"


def test_models_are_listed_with_licenses(ai_models):
    from medialab.upscale.models import list_models
    ms = {m.id: m for m in list_models()}
    assert ms["onnx:ultra_sharp_x4"].installed and ms["onnx:ultra_sharp_x4"].noncommercial
    assert "face" in ms["onnx:nomos8k_sc_x4"].categories
