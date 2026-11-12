"""ONNX Runtime sessions on DirectML (any DirectX 12 GPU: NVIDIA, AMD, Intel), CPU fallback.

Sessions are cached per model file; ``release()`` frees the video memory when
nothing uses the AI anymore. DirectML runs one inference at a time per
session, so every run goes through the session's lock.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..errors import MediaLabError
from ..logging_setup import get_logger

log = get_logger("gpu")

_sessions: dict[tuple[str, str], tuple[Any, threading.Lock]] = {}
_lock = threading.Lock()
_last_used = [0.0]
_OOM = ("e_outofmemory", "887a0005", "887a0006", "out of memory", "not enough memory", "device removed",
        "dxgi_error", "failed to allocate")


def _ort():
    try:
        import onnxruntime as ort
        return ort
    except Exception:  # pragma: no cover - broken install
        return None


def available() -> tuple[bool, str]:
    ort = _ort()
    if ort is None:
        return False, "ONNX Runtime is missing from this build."
    provs = ort.get_available_providers()
    if "DmlExecutionProvider" in provs:
        return True, "DirectML (GPU)"
    return True, "CPU (slow)"


def uses_gpu(device: str = "auto") -> bool:
    ort = _ort()
    return bool(ort) and device != "cpu" and "DmlExecutionProvider" in ort.get_available_providers()


def session(path: Path, device: str = "auto"):
    """Cached InferenceSession + its lock. ``device``: auto | cpu."""
    ort = _ort()
    if ort is None:
        raise MediaLabError("backend_missing", "The AI engine (ONNX Runtime) is missing.", "Reinstall se7en.")
    kind = "cpu" if not uses_gpu(device) else "dml"
    key = (str(path), kind)
    with _lock:
        if key in _sessions:
            _last_used[0] = time.monotonic()
            return _sessions[key]
        so = ort.SessionOptions()
        so.log_severity_level = 3
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        t0 = time.perf_counter()
        sess = None
        if kind == "dml":
            so.enable_mem_pattern = False  # required by DirectML
            so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            try:
                sess = ort.InferenceSession(str(path), sess_options=so, providers=[
                    ("DmlExecutionProvider", {"performance_preference": "high_performance", "device_filter": "gpu"}),
                    "CPUExecutionProvider"])
            except Exception as e:
                log.warning("DirectML could not load %s (%s); using the CPU", path.name, e)
                kind = "cpu"
                key = (str(path), kind)
                so = ort.SessionOptions()
                so.log_severity_level = 3
        if sess is None:
            try:
                sess = ort.InferenceSession(str(path), sess_options=so, providers=["CPUExecutionProvider"])
            except Exception as e:
                raise MediaLabError("model_load_failed", f"The AI model {path.name} could not be loaded.",
                                    "The file may be damaged: reinstall the AI modules in Settings › Components.",
                                    False, str(e)) from e
        log.info("AI model %s loaded on %s in %.1fs", path.name, "GPU (DirectML)" if kind == "dml" else "CPU",
                 time.perf_counter() - t0)
        _sessions[key] = (sess, threading.Lock())
        _last_used[0] = time.monotonic()
        return _sessions[key]


_gpu_lock = threading.Lock()  # DirectML: one inference at a time on the device (concurrent runs fail)


def run(path: Path, feeds: dict[str, np.ndarray], device: str = "auto") -> list[np.ndarray]:
    sess, lock = session(path, device)
    with (_gpu_lock if uses_gpu(device) else lock):
        try:
            return sess.run(None, feeds)
        except Exception as e:
            if _is_oom(e):
                raise MediaLabError("gpu_out_of_memory", "The GPU ran out of video memory.",
                                    "se7en retries automatically with smaller tiles.", True, _text(e)[-1500:]) from e
            raise MediaLabError("ai_failed", "The AI model failed on this image.", "Retry; if it persists, choose "
                                "another model.", True, _text(e)[-1500:]) from e


def _text(e: BaseException) -> str:
    try:
        return str(e)
    except Exception:  # some localized driver messages are not valid UTF-8
        return repr(e)


def _is_oom(e: BaseException) -> bool:
    low = _text(e).lower()
    return any(k in low for k in _OOM)


_force_cpu: set[str] = set()  # modèles repassés sur le processeur après un décrochage du GPU (jusqu'à release())


def _drop(path: Path) -> None:
    """Retire du cache les sessions de ce modèle (une session plantée ne se relève pas)."""
    with _lock:
        for k in [k for k in _sessions if k[0] == str(path)]:
            _sessions.pop(k, None)


def infer(path: Path, feeds: dict[str, np.ndarray], device: str = "auto",
          outputs: list[str] | None = None) -> list[np.ndarray]:
    """``sess.run`` robuste : si le GPU décroche, la session est recréée et l'inférence refaite une fois,
    puis sur le processeur en dernier recours. Lève une ``MediaLabError`` claire sinon."""
    dev = "cpu" if str(path) in _force_cpu else device
    last: BaseException | None = None
    for attempt in range(3):
        if attempt == 2:
            if not uses_gpu(dev):
                break  # déjà sur le processeur : rien de plus à tenter
            dev = "cpu"
        try:
            sess, lock = session(path, dev)
            with (_gpu_lock if uses_gpu(dev) else lock):
                out = sess.run(outputs, feeds)
            if attempt == 2:
                log.warning("AI model %s moved to the CPU after a GPU failure", Path(path).name)
                _force_cpu.add(str(path))
            return out
        except MediaLabError as e:
            if e.code != "model_load_failed":
                raise
            last = e
        except Exception as e:  # session morte (GPU décroché, message Windows non UTF-8…)
            last = e
            log.warning("AI model %s failed (attempt %d): %s", Path(path).name, attempt + 1, _text(e)[-300:])
        _drop(path)
    if isinstance(last, MediaLabError):
        raise last
    if last is not None and _is_oom(last):
        raise MediaLabError("gpu_out_of_memory", "La carte graphique n'a plus assez de mémoire.",
                            "Ferme les autres logiciels qui l'utilisent (jeux, montage), puis relance.", True,
                            _text(last)[-1500:]) from last
    raise MediaLabError("ai_failed", "L'IA a échoué sur cette vidéo, même après une nouvelle tentative.",
                        "Relance ; si ça continue, redémarre l'appli ou choisis le processeur dans les réglages.",
                        True, _text(last)[-1500:] if last else "") from last


def input_meta(path: Path, device: str = "auto") -> list:
    sess, _ = session(path, device)
    return sess.get_inputs()


def release(idle_seconds: float = 0) -> int:
    """Unload sessions (frees VRAM). With ``idle_seconds``, only when unused for that long."""
    with _lock:
        if idle_seconds and time.monotonic() - _last_used[0] < idle_seconds:
            return 0
        n = len(_sessions)
        _sessions.clear()
        _force_cpu.clear()
    if n:
        log.info("Released %d AI model(s) from memory", n)
    return n


# ------------------------------------------------------------------ image helpers
def to_nchw(rgb: np.ndarray) -> np.ndarray:
    """HxWx3 uint8 -> 1x3xHxW float32 in 0..1."""
    return np.ascontiguousarray((rgb.astype(np.float32) / 255.0).transpose(2, 0, 1)[None])


def from_nchw(t: np.ndarray) -> np.ndarray:
    """1x3xHxW float (0..1) -> HxWx3 uint8."""
    return (np.clip(t[0].transpose(1, 2, 0), 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
