"""Upscale engine logic that does not need a GPU (fake backend)."""
import threading
from pathlib import Path

import pytest
from PIL import Image

from medialab.errors import MediaLabError
from medialab.settings import Settings
from medialab.upscale import engine
from medialab.upscale.backends.base import BackendParams
from medialab.upscale.backends.classic import ClassicBackend


def test_oom_retry_halves_tile_until_success():
    tiles = []

    def fn(p: BackendParams):
        tiles.append(p.tile)
        if p.tile > 100:
            raise MediaLabError("gpu_out_of_memory", "The GPU ran out of video memory.", retryable=True)

    final = engine.with_oom_retry(fn, BackendParams(tile=400), "test")
    assert tiles[0] == 400 and tiles[-1] <= 100
    assert all(b < a for a, b in zip(tiles, tiles[1:]))
    assert final.tile == tiles[-1]


def test_oom_retry_does_not_hide_other_errors():
    def fn(_p):
        raise MediaLabError("model_missing", "no model")

    with pytest.raises(MediaLabError) as e:
        engine.with_oom_retry(fn, BackendParams(tile=256), "test")
    assert e.value.code == "model_missing"


def test_oom_retry_gives_up_at_minimum_tile():
    def fn(_p):
        raise MediaLabError("gpu_out_of_memory", "oom", retryable=True)

    with pytest.raises(MediaLabError):
        engine.with_oom_retry(fn, BackendParams(tile=64), "test")


def test_strategy_reduces_tile():
    s = Settings()
    s.ai.tile_size = 400
    model = engine.get_model("classic-lanczos")
    assert engine.resolve_params(s, model, {"reduce_tile": 1}).tile < 400


class _FlakyBackend(ClassicBackend):
    """Fails with OOM on large tiles, like a GPU with little VRAM."""
    id = "flaky"
    calls: list[int] = []

    def upscale_file(self, model, src, dst, scale, params, cancel, on_progress=None):
        self.calls.append(params.tile)
        if params.tile > 128:
            raise MediaLabError("gpu_out_of_memory", "oom", retryable=True)
        super().upscale_file(model, src, dst, scale, params, cancel, on_progress)


def test_image_pipeline_recovers_from_oom(tmp_path: Path, monkeypatch):
    flaky = _FlakyBackend()
    flaky.calls = []
    model = engine.get_model("classic-lanczos")
    monkeypatch.setattr(engine, "resolve_model", lambda _id: (model, flaky))
    s = Settings()
    s.ai.tile_size = 512
    src = tmp_path / "in.png"
    Image.new("RGB", (40, 30), (10, 120, 200)).save(src)
    dst = tmp_path / "out.png"
    res = engine.process_image(src, dst, {"model": "classic-lanczos", "scale": 2}, s, threading.Event(), lambda f, l: None)
    assert res["width"] == 80 and res["height"] == 60
    assert flaky.calls[0] == 512 and flaky.calls[-1] <= 128
    with Image.open(dst) as im:
        assert im.size == (80, 60)


def test_custom_resolution_and_alpha(tmp_path: Path):
    src = tmp_path / "alpha.png"
    Image.new("RGBA", (64, 36), (255, 0, 0, 128)).save(src)
    dst = tmp_path / "alpha_out.png"
    res = engine.process_image(src, dst, {"model": "classic-lanczos", "width": 256, "height": 256, "keep_aspect": True},
                               Settings(), threading.Event(), lambda f, l: None)
    assert (res["width"], res["height"]) == (256, 144)
    with Image.open(dst) as im:
        assert im.mode == "RGBA" and im.getpixel((10, 10))[3] in range(120, 136)
