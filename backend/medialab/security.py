"""Local API protection and at-rest encryption of sensitive data.

* The API only listens on 127.0.0.1 and every request must carry the random
  per-session token (header ``X-MediaLab-Token`` or ``?token=`` for WebSocket /
  media URLs). This stops other web pages open in a browser from driving the
  local API (CSRF / DNS-rebinding), since they cannot know the token.
* Cookies files are encrypted with Windows DPAPI (bound to the current Windows
  user). Passwords are never requested nor stored.
"""
from __future__ import annotations

import ctypes
import hmac
import json
import os
import secrets
import sys
from ctypes import wintypes
from pathlib import Path

from .config import paths

TOKEN_HEADER = "X-MediaLab-Token"


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def tokens_equal(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a, b)


def write_session_file(port: int, token: str, shell: bool = True) -> Path:
    """Lets local integrations (CLI, After Effects script) find the running API. ``shell``: the se7en app
    with its window (a server / dev instance is not "se7en already open")."""
    p = paths().session
    p.write_text(json.dumps({"port": port, "token": token, "pid": os.getpid(), "shell": shell}), "utf-8")
    return p


def clear_session_file() -> None:
    try:
        paths().session.unlink()
    except OSError:
        pass


def read_session_file() -> dict | None:
    try:
        return json.loads(paths().session.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ----------------------------------------------------------------- DPAPI
class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


_ENTROPY = b"MediaLab-cookies-v1"


def protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("Encrypted storage is only implemented with Windows DPAPI")
    out = _Blob()
    ent = _blob(_ENTROPY)
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(_blob(data)), None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("Encrypted storage is only implemented with Windows DPAPI")
    out = _Blob()
    ent = _blob(_ENTROPY)
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(_blob(data)), None, ctypes.byref(ent), None, None, 0x1, ctypes.byref(out)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)
