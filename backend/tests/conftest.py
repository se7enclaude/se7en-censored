import os
import sys
import tempfile
from pathlib import Path

# Isolated data dir for the whole test session (must be set before importing medialab).
_TMP = tempfile.mkdtemp(prefix="medialab-test-")
os.environ["MEDIALAB_DATA_DIR"] = _TMP
os.environ["SE7ENCENSORED_DATA_DIR"] = _TMP  # le nom lu par config.paths()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return Path(_TMP)


def _find_ffmpeg() -> str | None:
    """FFmpeg for the media tests: $SE7EN_TEST_FFMPEG, the user's se7en install, the build, or PATH."""
    import glob
    import shutil
    cands = [os.environ.get("SE7EN_TEST_FFMPEG", "")]
    local = os.environ.get("LOCALAPPDATA", "")
    cands += glob.glob(os.path.join(local, "MediaLab", "components", "ffmpeg", "**", "ffmpeg.exe"), recursive=True)
    root = Path(__file__).resolve().parents[2]
    cands += glob.glob(str(root / "dist" / "se7en" / "bin" / "ffmpeg" / "**" / "ffmpeg.exe"), recursive=True)
    cands.append(shutil.which("ffmpeg") or "")
    for c in cands:
        if c and Path(c).is_file() and Path(c).with_name("ffprobe" + Path(c).suffix).is_file():
            return c
    return None


FFMPEG = _find_ffmpeg()


@pytest.fixture
def ffmpeg_bin():
    if not FFMPEG:
        pytest.skip("FFmpeg not available")
    from medialab import components
    components.set_overrides(ffmpeg=FFMPEG)
    return FFMPEG


def _asset_dir() -> Path | None:
    """ONNX models for the AI tests: $SE7EN_TEST_ASSETS, the build cache, or the packaged app."""
    root = Path(__file__).resolve().parents[2]
    for c in (os.environ.get("SE7EN_TEST_ASSETS", ""), root / "build" / "assets", root / "dist" / "se7en" / "bin" / "ai"):
        if c and Path(c).is_dir() and any(Path(c).glob("*.onnx")):
            return Path(c)
    return None


@pytest.fixture(scope="session")
def ai_models():
    """Link the ONNX models into the test data dir (skips when they are not available locally)."""
    src = _asset_dir()
    if not src:
        pytest.skip("AI models not available")
    from medialab.config import paths
    dst = paths().models / "onnx"
    dst.mkdir(parents=True, exist_ok=True)
    for f in [*src.glob("*.onnx"), *src.glob("*.rnnn")]:
        t = dst / f.name
        if not t.exists():
            try:
                os.link(f, t)
            except OSError:
                import shutil
                shutil.copy2(f, t)
    return dst
