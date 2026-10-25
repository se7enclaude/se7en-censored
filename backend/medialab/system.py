"""OS integration: open files/folders, native dialogs, clipboard, taskbar flash."""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
from pathlib import Path

from .errors import MediaLabError
from .logging_setup import get_logger

log = get_logger("app")

# Set by the desktop shell when a pywebview window exists.
window = None
quit_requested = threading.Event()
_quit_callbacks: list = []


def on_quit(cb) -> None:
    _quit_callbacks.append(cb)


def request_quit(delay: float = 0.0) -> None:
    """Close se7en (window or browser mode) — used after starting an update."""
    def go() -> None:
        quit_requested.set()
        for cb in list(_quit_callbacks):
            try:
                cb()
            except Exception as e:  # noqa: BLE001
                log.debug("quit callback failed: %s", e)
    threading.Timer(delay, go).start() if delay else go()


def open_path(path: str, reveal: bool = False) -> None:
    p = Path(path)
    if not p.exists():
        raise MediaLabError("file_missing", "This file or folder no longer exists.",
                            "It may have been moved or deleted.")
    if sys.platform == "win32":
        if reveal and p.is_file():
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            os.startfile(str(p))  # noqa: S606 — user-initiated
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R" if reveal else "", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p.parent if reveal else p)])


def pick_folder(initial: str = "") -> str | None:
    if window is not None:
        import webview
        res = window.create_file_dialog(webview.FileDialog.FOLDER, directory=initial or "")
        return res[0] if res else None
    return _tk_dialog("folder", initial)


def _pattern(exts) -> str:
    return ";".join(f"*{e}" for e in sorted(exts))


def file_types(kind: str) -> tuple[str, ...]:
    from .media.ffmpeg import AUDIO_EXTS, IMAGE_EXTS, SUB_EXTS, VIDEO_EXTS
    groups = {
        "media": ("Images and videos", IMAGE_EXTS | VIDEO_EXTS),
        "video": ("Videos", VIDEO_EXTS),
        "audio": ("Audio", AUDIO_EXTS),
        "image": ("Images", IMAGE_EXTS),
        "av": ("Videos and audio", VIDEO_EXTS | AUDIO_EXTS),
        "any": ("Media files", IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS | SUB_EXTS),
        "playable": ("Media files", IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS),
    }
    if kind in groups:
        label, exts = groups[kind]
        return (f"{label} ({_pattern(exts)})", "All files (*.*)")
    return {
        "cookies": ("Cookies (*.txt)", "All files (*.*)"),
        "exe": ("Programs (*.exe)", "All files (*.*)"),
    }.get(kind, ("All files (*.*)",))


def pick_files(kind: str = "media", initial: str = "", multiple: bool = True) -> list[str]:
    types = file_types(kind)
    multiple = multiple and kind not in ("cookies", "exe")
    if window is not None:
        import webview
        try:  # pywebview only accepts "Words (*.ext;*.ext)" filters: never let a bad label kill the dialog
            from webview.util import parse_file_type
            for t in types:
                parse_file_type(t)
        except (ImportError, ValueError):
            types = ("All files (*.*)",)
        res = window.create_file_dialog(webview.FileDialog.OPEN, directory=initial or "",
                                        allow_multiple=multiple, file_types=types)
        return list(res or [])
    r = _tk_dialog("files", initial)
    r = list(r or [])
    return r if multiple else r[:1]


def _tk_dialog(mode: str, initial: str):
    """Fallback for browser dev mode (no pywebview window)."""
    result: list = []

    def run() -> None:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        if mode == "folder":
            result.append(filedialog.askdirectory(initialdir=initial or None) or None)
        else:
            result.append(filedialog.askopenfilenames(initialdir=initial or None) or [])
        root.destroy()

    t = threading.Thread(target=run)
    t.start()
    t.join()
    return result[0] if result else None


def clipboard_text() -> str:
    if sys.platform != "win32":
        return ""
    CF_UNICODETEXT = 13
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    u32.GetClipboardData.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not u32.OpenClipboard(None):
        return ""
    try:
        h = u32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        ptr = k32.GlobalLock(h)
        try:
            return ctypes.wstring_at(ptr)[:20000] if ptr else ""
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def flash_taskbar(title: str = "se7en") -> None:
    """Flash the taskbar button when a task finishes while the app is in the background."""
    if sys.platform != "win32":
        return
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, title)
        if hwnd and ctypes.windll.user32.GetForegroundWindow() != hwnd:
            class FLASHWINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p), ("dwFlags", ctypes.c_uint),
                            ("uCount", ctypes.c_uint), ("dwTimeout", ctypes.c_uint)]
            info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x3 | 0xC, 3, 0)  # ALL | TIMERNOFG
            ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception as e:
        log.debug("flash failed: %s", e)
