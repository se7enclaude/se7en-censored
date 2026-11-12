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


# ------------------------------------------------------------------ processus enfants
_job = None  # gardé ouvert toute la vie du processus : sa fermeture (même forcée) arrête les enfants
BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB


def kill_children_on_exit() -> bool:
    """Windows : rattache se7en à un Job Object « tuer à la fermeture ». Si se7en est fermé de force,
    ffmpeg, whisper-cli, llama-server… s'arrêtent avec lui au lieu de rester orphelins. Sans effet ailleurs ;
    un échec n'empêche jamais le démarrage."""
    global _job
    if sys.platform != "win32" or _job is not None:
        return False
    try:
        from ctypes import wintypes

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount",
                                                          "OtherOperationCount", "ReadTransferCount",
                                                          "WriteTransferCount", "OtherTransferCount")]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObject")
        info = EXTENDED_LIMIT()
        # KILL_ON_JOB_CLOSE (0x2000) ; BREAKAWAY_OK (0x800) : l'assistant de mise à jour et l'Explorateur en sortent
        info.BasicLimitInformation.LimitFlags = 0x2000 | 0x800
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # 9 : ExtendedLimit
            err = ctypes.get_last_error()
            k32.CloseHandle(job)
            raise OSError(err, "SetInformationJobObject")
        if not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
            err = ctypes.get_last_error()
            k32.CloseHandle(job)
            raise OSError(err, "AssignProcessToJobObject")
        _job = job
        return True
    except Exception as e:  # noqa: BLE001 — jamais bloquant
        log.info("Child processes will not be stopped with se7en (job object unavailable: %s)", e)
        return False


def explorer(args: str) -> None:
    """Lance l'Explorateur hors du job de se7en (sinon il serait fermé avec se7en)."""
    exe = os.path.join(os.environ.get("SystemRoot", "C:" + os.sep + "Windows"), "explorer.exe")
    cmd = f'"{exe}" {args}'
    try:
        subprocess.Popen(cmd, creationflags=BREAKAWAY, close_fds=True)
    except OSError:  # breakaway refusé par un job parent
        subprocess.Popen(cmd, close_fds=True)


def open_path(path: str, reveal: bool = False) -> None:
    p = Path(path)
    if not p.exists():
        raise MediaLabError("file_missing", "This file or folder no longer exists.",
                            "It may have been moved or deleted.")
    if sys.platform == "win32":
        if reveal and p.is_file():
            explorer(f'/select,"{p}"')
        elif p.is_file():
            # par l'Explorateur, hors du « job » de se7en : le lecteur ouvert reste ouvert quand se7en se ferme
            try:
                explorer(f'"{p}"')
            except OSError:
                os.startfile(str(p))  # noqa: S606 — user-initiated
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
