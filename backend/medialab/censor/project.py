"""Projets : une vidéo source, ses détections, les choix de l'utilisateur et la vidéo exportée (JSON dans <données>/censor)."""
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
from .track import DEFAULT_CLASSES

_lock = threading.RLock()

DEFAULT_OPTIONS = {
    "classes": list(DEFAULT_CLASSES), "sensitivity": 65, "margin": 0.2,
    "style": "blur", "shape": "ellipse", "strength": 70,
    "quality": "full",   # full : toutes les images ; fast : une sur deux
    "careful": False,    # analyse aussi l'image retournée (plus sûr, deux fois plus long)
    "verify": True,      # 2e vérification (CLIP) : les zones jugées non nues ne sont pas floutées
}
EDITABLE = {"classes", "sensitivity", "margin", "style", "shape", "strength", "quality", "careful", "verify"}


def root() -> Path:
    d = paths().data / "censor"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir(pid: str) -> Path:
    d = root() / "cache" / pid
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_output() -> Path:
    return Path.home() / "Videos" / "se7en Censored"


def output_dir(project: dict) -> Path:
    d = Path(project.get("output_dir") or default_output())
    d.mkdir(parents=True, exist_ok=True)
    return d


def _file(pid: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", pid or ""):
        raise MediaLabError("project_not_found", "Ce projet n'existe pas.")
    return root() / f"{pid}.json"


def video_info(path: Path) -> dict:
    info = ffmpeg.probe(path)
    if not info.get("has_video"):
        raise MediaLabError("no_video_stream", "Ce fichier n'a pas d'image.", "Choisissez une vidéo.")
    w, h = info.get("width"), info.get("height")
    if abs(int(info.get("rotation") or 0)) in (90, 270):
        w, h = h, w
    return {"path": str(path), "name": path.name, "size": info.get("size"), "duration": info.get("duration"),
            "width": w, "height": h, "fps": info.get("fps") or 25.0, "vcodec": info.get("vcodec"),
            "acodec": (info.get("audio") or [{}])[0].get("codec") if info.get("audio") else None,
            "is_hdr": bool(info.get("is_hdr"))}


def create(source: str, options: dict | None = None, output: str | None = None) -> dict:
    source = source.strip().strip('"')
    is_url = bool(re.match(r"https?://", source, re.I))
    if not is_url and not Path(source).is_file():
        raise MediaLabError("file_missing", "Fichier introuvable.", "Il a peut-être été déplacé ou supprimé.")
    now = time.time()
    opts = {**DEFAULT_OPTIONS, **{k: v for k, v in (options or {}).items() if k in EDITABLE and v is not None}}
    project = {
        "id": uuid.uuid4().hex[:12], "name": Path(source).stem if not is_url else "Vidéo en ligne",
        "created": now, "updated": now,
        "source": {"url": source if is_url else None, "path": None if is_url else str(Path(source).resolve())},
        "video": None, "status": "new", "step": None, "error": None, "notes": [],
        "options": opts, "output_dir": output, "manual": [], "hidden": [], "export": None, "stats": {},
        "thumb": None,
    }
    return save(project)


def load(pid: str) -> dict:
    f = _file(pid)
    with _lock:  # jamais pendant une écriture (Windows refuse de remplacer un fichier ouvert)
        if not f.exists():
            raise MediaLabError("project_not_found", "Ce projet n'existe pas.")
        return json.loads(f.read_text("utf-8"))


def save(project: dict) -> dict:
    project["updated"] = time.time()
    f = _file(project["id"])
    tmp = f.with_suffix(".tmp")
    with _lock:
        tmp.write_text(json.dumps(project, ensure_ascii=False), "utf-8")
        for attempt in range(20):  # un antivirus ou l'indexation peut tenir le fichier un instant
            try:
                os.replace(tmp, f)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    return project


def patch(pid: str, changes: dict) -> dict:
    with _lock:
        project = load(pid)
        project.update(changes)
        return save(project)


def delete(pid: str) -> None:
    _file(pid).unlink(missing_ok=True)
    shutil.rmtree(root() / "cache" / pid, ignore_errors=True)


def listing() -> list[dict]:
    out = []
    for f in root().glob("*.json"):
        try:
            with _lock:
                p = json.loads(f.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        v = p.get("video") or {}
        out.append({"id": p["id"], "name": p.get("name"), "updated": p.get("updated"), "status": p.get("status"),
                    "duration": v.get("duration"), "zones": (p.get("stats") or {}).get("zones"),
                    "thumb": p.get("thumb"), "url": (p.get("source") or {}).get("url"),
                    "exported": bool((p.get("export") or {}).get("file"))})
    return sorted(out, key=lambda x: x.get("updated") or 0, reverse=True)
