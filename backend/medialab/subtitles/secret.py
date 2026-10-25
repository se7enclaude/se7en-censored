"""The Claude API key: stored encrypted with Windows DPAPI (only this Windows account can read it), in its own file
outside the settings, and never sent back to the interface."""
from __future__ import annotations

from ..config import paths
from ..security import protect, unprotect


def _file():
    d = paths().data / "secrets"
    d.mkdir(parents=True, exist_ok=True)
    return d / "claude.key"


def set_key(key: str) -> None:
    key = key.strip()
    if key:
        _file().write_bytes(protect(key.encode("utf-8")))
    else:
        _file().unlink(missing_ok=True)


def get_key() -> str:
    f = _file()
    if not f.exists():
        return ""
    try:
        return unprotect(f.read_bytes()).decode("utf-8")
    except Exception:  # another Windows account / damaged file
        return ""


def status() -> dict:
    k = get_key()
    return {"configured": bool(k), "hint": f"…{k[-4:]}" if len(k) > 8 else None}
