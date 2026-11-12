"""Sessions ONNX qui plantent (GPU décroché) et cache des détections abîmé."""
import json

import numpy as np
import pytest

from medialab.errors import MediaLabError
from medialab.upscale import onnx_rt


class FakeSession:
    def __init__(self, fails: int, exc: Exception) -> None:
        self.fails, self.exc, self.calls = fails, exc, 0

    def run(self, outputs, feeds):
        self.calls += 1
        if self.calls <= self.fails:
            raise self.exc
        return [np.ones(2, np.float32)]


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """``session()`` simulé : une session neuve par création, GPU pour « auto », processeur pour « cpu »."""
    made: list[tuple[str, FakeSession]] = []
    plan: dict[str, list[FakeSession]] = {}

    def session(path, device="auto"):
        kind = "cpu" if device == "cpu" else "dml"
        key = (str(path), kind)
        with onnx_rt._lock:
            if key not in onnx_rt._sessions:
                s = plan[kind].pop(0)
                made.append((kind, s))
                onnx_rt._sessions[key] = (s, onnx_rt.threading.Lock())
            return onnx_rt._sessions[key]

    monkeypatch.setattr(onnx_rt, "session", session)
    monkeypatch.setattr(onnx_rt, "uses_gpu", lambda device="auto": device != "cpu")
    onnx_rt.release()
    yield tmp_path / "model.onnx", plan, made
    onnx_rt.release()


def test_dead_session_is_recreated_and_retried(fake):
    path, plan, made = fake
    bad = UnicodeDecodeError("utf-8", b"\xe9", 0, 1, "invalid continuation byte")  # message Windows non UTF-8
    plan["dml"] = [FakeSession(99, bad), FakeSession(0, bad)]
    out = onnx_rt.infer(path, {"x": np.zeros(1)})
    assert out[0].tolist() == [1, 1]
    assert [k for k, _ in made] == ["dml", "dml"]  # la session morte a été retirée du cache puis recréée
    assert onnx_rt._sessions[(str(path), "dml")][0] is made[1][1]


def test_falls_back_to_cpu_then_stays_there(fake):
    path, plan, made = fake
    plan["dml"] = [FakeSession(99, RuntimeError("DXGI_ERROR_DEVICE_HUNG")), FakeSession(99, RuntimeError("hung"))]
    plan["cpu"] = [FakeSession(0, RuntimeError())]
    assert onnx_rt.infer(path, {"x": np.zeros(1)})[0].tolist() == [1, 1]
    assert [k for k, _ in made] == ["dml", "dml", "cpu"]
    onnx_rt.infer(path, {"x": np.zeros(1)})  # suite de la tâche : directement sur le processeur
    assert len(made) == 3
    onnx_rt.release()
    assert not onnx_rt._sessions and not onnx_rt._force_cpu


def test_clear_french_error_when_everything_fails(fake):
    path, plan, _ = fake
    plan["cpu"] = [FakeSession(99, RuntimeError("boom")), FakeSession(99, RuntimeError("boom"))]
    with pytest.raises(MediaLabError) as e:
        onnx_rt.infer(path, {"x": np.zeros(1)}, "cpu")
    assert e.value.code == "ai_failed" and "IA" in e.value.message
    assert (str(path), "cpu") not in onnx_rt._sessions  # rien de mort ne reste en cache


def test_detection_meta_is_atomic_and_damaged_cache_ignored(tmp_path):
    from medialab.censor import detect
    meta = {"v": detect.DETECT_VERSION, "fps": 25}
    detect._save(tmp_path, [(0, 1, 0.9, [0.1, 0.1, 0.5, 0.5])], meta)
    assert not list(tmp_path.glob("*.tmp*"))
    det, m = detect.load(tmp_path)
    assert m == meta and det["f"].tolist() == [0]
    (tmp_path / "detections.json").write_text("", "utf-8")  # coupure pendant l'écriture (ancienne version)
    assert detect.load(tmp_path) is None
    (tmp_path / "detections.json").write_text(json.dumps(meta), "utf-8")
    (tmp_path / "detections.npz").write_bytes(b"PK\x03\x04 abime")
    assert detect.load(tmp_path) is None
