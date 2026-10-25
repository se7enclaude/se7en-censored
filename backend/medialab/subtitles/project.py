"""Subtitle projects: everything needed to resume work later, saved as JSON in <data>/subtitles.

A project keeps the video reference, the detected / chosen languages, the generation settings, the recognized
transcript (``original``), the working cues (after correction, translation and manual edits) and the style.
Writes are atomic (temporary file + rename): a crash never leaves a half-written project.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

from ..config import paths
from ..errors import MediaLabError
from ..media import ffmpeg
from . import styles

VERSION = 1
_lock = threading.Lock()
EDITABLE = {"name", "cues", "style", "source_lang", "target_lang", "engine", "options", "original_lang"}


def root() -> Path:
    d = paths().data / "subtitles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir(pid: str) -> Path:
    d = root() / "cache" / pid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(pid: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", pid or ""):
        raise MediaLabError("project_not_found", "This subtitle project does not exist.")
    return root() / f"{pid}.json"


def video_info(path: Path) -> dict:
    info = ffmpeg.probe(path)
    if not info.get("has_audio"):
        raise MediaLabError("no_audio_stream", "This file has no sound track.",
                            "Subtitles are made from the voice: choose a video with sound.")
    w, h = info.get("width"), info.get("height")
    if abs(int(info.get("rotation") or 0)) in (90, 270):
        w, h = h, w  # phone videos: FFmpeg shows (and burns into) the rotated picture
    return {"path": str(path), "name": path.name, "size": info.get("size"), "duration": info.get("duration"),
            "width": w, "height": h, "fps": info.get("fps"), "bit_depth": info.get("bit_depth"),
            "vcodec": info.get("vcodec"), "acodec": (info.get("audio") or [{}])[0].get("codec"),
            "format": path.suffix.lstrip(".").upper(), "has_video": bool(info.get("has_video")),
            "is_hdr": bool(info.get("is_hdr"))}


def create(path: Path, default_preset: str = "netflix") -> dict:
    path = Path(path).resolve()
    if not path.is_file():
        raise MediaLabError("file_missing", "File not found.", "It may have been moved or deleted.")
    now = time.time()
    project = {
        "id": uuid.uuid4().hex[:12], "version": VERSION, "name": path.stem, "created": now, "updated": now,
        "video": video_info(path), "detected": None, "source_lang": None, "target_lang": None,
        "engine": None, "options": {"polish": True, "glossary": ""}, "status": "new",
        "cues": [], "original": [], "original_lang": None,
        "style": styles.resolve({"preset": default_preset}),
    }
    save(project)
    return project


def load(pid: str) -> dict:
    f = _file(pid)
    if not f.exists():
        raise MediaLabError("project_not_found", "This subtitle project does not exist.")
    return json.loads(f.read_text("utf-8"))


def save(project: dict) -> dict:
    project["updated"] = time.time()
    f = _file(project["id"])
    tmp = f.with_suffix(".tmp")
    with _lock:
        tmp.write_text(json.dumps(project, ensure_ascii=False), "utf-8")
        os.replace(tmp, f)
    return project


def update(pid: str, patch: dict) -> dict:
    project = load(pid)
    for k, v in patch.items():
        if k in EDITABLE and v is not None:
            project[k] = v
    if "cues" in patch:
        project["cues"] = sorted(_clean_cues(project["cues"]), key=lambda c: (c["start"], c["end"]))
        if project["cues"] and project.get("status") == "new":
            project["status"] = "ready"
    return save(project)


def _clean_cues(cues: list) -> list[dict]:
    out = []
    for c in cues or []:
        try:
            start, end = float(c["start"]), float(c["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(c.get("text", "")).replace("\r", "")
        if end <= start:
            end = start + 0.5
        out.append({"id": str(c.get("id") or uuid.uuid4().hex[:10]), "start": round(max(0.0, start), 3),
                    "end": round(end, 3), "text": text, "words": c.get("words") or [],
                    "speaker": c.get("speaker")})
    return out


def delete(pid: str) -> None:
    _file(pid).unlink(missing_ok=True)
    shutil.rmtree(root() / "cache" / pid, ignore_errors=True)


def listing() -> list[dict]:
    out = []
    for f in root().glob("*.json"):
        try:
            p = json.loads(f.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        v = p.get("video") or {}
        out.append({"id": p["id"], "name": p.get("name"), "updated": p.get("updated"), "status": p.get("status"),
                    "cues": len(p.get("cues") or []), "video": v.get("path"), "duration": v.get("duration"),
                    "exists": Path(v.get("path") or "").is_file(), "source_lang": p.get("source_lang"),
                    "target_lang": p.get("target_lang")})
    return sorted(out, key=lambda x: x.get("updated") or 0, reverse=True)
