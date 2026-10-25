"""Filesystem layout.

Installed mode:  data in %LOCALAPPDATA%\\MediaLab
Portable mode:   a file named ``portable.flag`` next to the executable (or the
                 SE7ENCENSORED_PORTABLE env var) keeps all data in ``<app>/data``.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import APP_ID


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_root() -> Path:
    """Directory containing the executable (frozen) or the repo root (dev)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # backend/medialab/config.py -> repo root
    return Path(__file__).resolve().parents[2]


def resource_root() -> Path:
    """Where bundled read-only resources live (PyInstaller _MEIPASS or repo)."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else app_root()


def is_portable() -> bool:
    return bool(os.environ.get("SE7ENCENSORED_PORTABLE")) or (app_root() / "portable.flag").exists()


@dataclass(frozen=True)
class Paths:
    data: Path
    db: Path
    settings: Path
    logs: Path
    cache: Path
    temp: Path
    thumbnails: Path
    components: Path
    models: Path
    plugins: Path
    checkpoints: Path
    session: Path

    def ensure(self) -> None:
        for p in (self.data, self.logs, self.cache, self.temp, self.thumbnails,
                  self.components, self.models, self.plugins, self.checkpoints):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def paths() -> Paths:
    override = os.environ.get("SE7ENCENSORED_DATA_DIR")
    if override:
        data = Path(override)
    elif is_portable():
        data = app_root() / "data"
    else:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        data = Path(base) / APP_ID
    p = Paths(
        data=data,
        db=data / "medialab.db",
        settings=data / "settings.json",
        logs=data / "logs",
        cache=data / "cache",
        temp=data / "temp",
        thumbnails=data / "cache" / "thumbnails",
        components=data / "components",
        models=data / "models",
        plugins=data / "plugins",
        checkpoints=data / "checkpoints",
        session=data / "session.json",
    )
    if not _writable(p):
        # Portable folder copied somewhere read-only (Program Files, network share...):
        # keep working with the per-user data folder instead of crashing.
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return _make_paths(Path(base) / APP_ID)
    return p


def _writable(p: "Paths") -> bool:
    try:
        p.ensure()
        probe = p.data / ".write-test"
        probe.write_text("ok", "utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _make_paths(data: Path) -> "Paths":
    p = Paths(
        data=data, db=data / "medialab.db", settings=data / "settings.json", logs=data / "logs",
        cache=data / "cache", temp=data / "temp", thumbnails=data / "cache" / "thumbnails",
        components=data / "components", models=data / "models", plugins=data / "plugins",
        checkpoints=data / "checkpoints", session=data / "session.json",
    )
    p.ensure()
    return p


def shared_data_dirs() -> list[Path]:
    """Dossiers de données d'autres applis se7en (moteurs et modèles réutilisables, en lecture seule)."""
    from . import SHARED_APP_ID
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    d = Path(base) / SHARED_APP_ID
    return [d] if d.is_dir() and d.resolve() != paths().data.resolve() else []


def frontend_dist() -> Path:
    for candidate in (resource_root() / "frontend" / "dist", app_root() / "frontend" / "dist"):
        if (candidate / "index.html").exists():
            return candidate
    return resource_root() / "frontend" / "dist"
