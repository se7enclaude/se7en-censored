"""Routes de l'API et tâche de fond de se7en Censored."""
from __future__ import annotations

import os
import threading
from pathlib import Path

import numpy as np
from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from ..errors import Canceled, MediaLabError
from ..events import bus
from ..logging_setup import get_logger
from . import pipeline, render
from . import project as P
from . import track as T

log = get_logger("censor")


class NewProjectBody(BaseModel):
    source: str
    options: dict = {}
    output_dir: str | None = None


class OptionsBody(BaseModel):
    classes: list[str] | None = None
    sensitivity: int | None = None
    margin: float | None = None
    style: str | None = None
    shape: str | None = None
    strength: int | None = None
    quality: str | None = None
    careful: bool | None = None
    verify: bool | None = None


class ManualBody(BaseModel):
    manual: list[dict]


class FollowBody(BaseModel):
    f: int
    box: list[float]
    seconds: float = 10.0


class HideBody(BaseModel):
    track: str


class UnhideBody(BaseModel):
    index: int


def make_worker(settings):
    """Tâche « censor » : analyse d'une vidéo, ou export de la version floutée."""
    def worker(job: dict, jctx) -> dict:
        s = settings.get()
        opts = dict(job.get("options") or {})
        pid = str(opts.get("project") or "")
        op = opts.get("op") or "analyze"

        def progress(frac: float, label: str, eta: float | None = None) -> None:
            jctx.progress({"stage": "processing", "label": label, "percent": frac * 100, "eta": eta, "speed": 0})
            bus.publish("censor_progress", {"project": pid, "op": op, "percent": frac * 100, "label": label,
                                            "eta": eta})

        try:
            if op == "export":
                project = pipeline.export(pid, s, jctx.cancel, progress)
                bus.publish("censor_project", {"project": pid})
                ex = project.get("export") or {}
                return {"file_path": ex.get("file"), "title": project["name"], "platform": "censor",
                        "format": Path(ex.get("file") or "").suffix.lstrip(".").upper(),
                        "duration": (project.get("video") or {}).get("duration"), "meta": {"project": pid}}
            project = pipeline.analyze(pid, s, jctx, progress)
            bus.publish("censor_project", {"project": pid})
            return {"file_path": None, "title": project["name"], "platform": "censor",
                    "format": f"{(project.get('stats') or {}).get('zones', 0)} zones",
                    "duration": (project.get("video") or {}).get("duration"), "meta": {"project": pid}}
        except Canceled:
            P.patch(pid, {"status": "paused" if op == "analyze" else "review", "step": None})
            bus.publish("censor_project", {"project": pid})
            raise
        except MediaLabError as e:
            P.patch(pid, {"status": "error" if op == "analyze" else "review", "step": None,
                          "error": {"code": e.code, "message": e.message, "hint": e.hint}})
            bus.publish("censor_project", {"project": pid})
            raise
        except Exception as e:
            log.exception("Censor job failed")
            P.patch(pid, {"status": "error" if op == "analyze" else "review", "step": None,
                          "error": {"message": str(e), "hint": "Voir le journal."}})
            bus.publish("censor_project", {"project": pid})
            raise
    return worker


def register(app: FastAPI, S) -> None:
    def start(pid: str, op: str) -> dict:
        project = P.load(pid)
        src = project["source"].get("path") or project["source"].get("url") or ""
        P.patch(pid, {"status": "queued" if op == "analyze" else "exporting", "error": None})
        job = S.jobs.add("censor", src, {"op": op, "project": pid}, title=project["name"], platform="censor")
        P.patch(pid, {"job": job["id"]})
        bus.publish("censor_project", {"project": pid})
        return job

    def cancel_job(project: dict) -> None:
        if project.get("job"):
            try:
                S.jobs.cancel(project["job"])
            except MediaLabError:
                pass

    @app.get("/api/censor/catalog")
    async def catalog():
        return {"classes": [{"id": c, "label": label, "default": on} for c, label, on in T.CLASSES],
                "defaults": P.DEFAULT_OPTIONS, "output_dir": str(P.default_output())}

    @app.get("/api/censor/projects")
    async def listing():
        return {"projects": await run_in_threadpool(P.listing)}

    @app.post("/api/censor/projects")
    async def create(body: NewProjectBody):
        project = await run_in_threadpool(P.create, body.source, body.options, body.output_dir)
        job = start(project["id"], "analyze")
        return {"project": P.load(project["id"]), "job": job}

    @app.get("/api/censor/projects/{pid}")
    async def get(pid: str):
        return P.load(pid)

    @app.delete("/api/censor/projects/{pid}")
    async def delete(pid: str):
        cancel_job(P.load(pid))
        P.delete(pid)
        return {"ok": True}

    @app.post("/api/censor/projects/{pid}/analyze")
    async def reanalyze(pid: str):
        return {"job": start(pid, "analyze")}

    @app.post("/api/censor/projects/{pid}/cancel")
    async def cancel(pid: str):
        project = P.load(pid)
        cancel_job(project)
        P.patch(pid, {"status": "paused" if project.get("status") in ("queued", "analyzing") else "review",
                      "step": None})
        return {"ok": True}

    @app.patch("/api/censor/projects/{pid}/options")
    async def options(pid: str, body: OptionsBody):
        def run():
            project = P.load(pid)
            changes = {k: v for k, v in body.model_dump().items() if v is not None}
            if "classes" in changes:
                changes["classes"] = [c for c in changes["classes"] if c in T.CLASS_IDS]
            project["options"] = {**project["options"], **changes}
            if project.get("status") in ("review", "done"):
                project["stats"] = {**(project.get("stats") or {}), **_cov(project)}
            return P.save(project)
        return await run_in_threadpool(run)

    def _cov(project: dict) -> dict:
        tracks, meta = pipeline.zones(project)
        return T.coverage(tracks, project.get("manual") or [], project.get("hidden") or [], float(meta["fps"]),
                          int(meta["frames"]))

    @app.get("/api/censor/projects/{pid}/zones")
    async def zones(pid: str):
        def run():
            project = P.load(pid)
            tracks, meta = pipeline.zones(project)
            hidden = project.get("hidden") or []
            return {"fps": meta["fps"], "frames": meta["frames"],
                    "tracks": [{**t.to_dict(), "hidden": T.hidden_match(t, hidden)} for t in tracks],
                    "manual": project.get("manual") or [], "hidden": hidden, "cuts": meta.get("cuts", []),
                    "refined": bool(meta.get("refined")), "stats": _cov(project)}
        return await run_in_threadpool(run)

    @app.put("/api/censor/projects/{pid}/manual")
    async def manual(pid: str, body: ManualBody):
        def run():
            clean = []
            for z in body.manual:
                keys = [{"f": int(k["f"]), "box": [float(np.clip(v, 0, 1)) for v in k["box"]][:4]}
                        for k in z.get("keys") or [] if len(k.get("box") or []) == 4]
                if keys:
                    zone = {"id": str(z.get("id") or len(clean)), "keys": sorted(keys, key=lambda k: k["f"])}
                    if z.get("fixed"):
                        zone["fixed"] = True  # zone posée telle quelle, sans suivi du mouvement
                    clean.append(zone)
            project = P.load(pid)
            project["manual"] = clean
            project["stats"] = {**(project.get("stats") or {}), **_cov(project)}
            return P.save(project)
        return await run_in_threadpool(run)

    @app.post("/api/censor/projects/{pid}/follow")
    async def follow(pid: str, body: FollowBody):
        """Suit à l'image une zone tracée à la main : renvoie ses images clés (le client les enregistre)."""
        if len(body.box) != 4:
            raise MediaLabError("invalid", "Zone invalide.")
        seconds = max(0.5, min(60.0, body.seconds))
        return await run_in_threadpool(pipeline.follow_zone, P.load(pid), body.f, body.box, seconds)

    @app.post("/api/censor/projects/{pid}/hide")
    async def hide(pid: str, body: HideBody):
        def run():
            project = P.load(pid)
            tracks, _ = pipeline.zones(project)
            t = next((t for t in tracks if t.id == body.track), None)
            if t is None:
                raise MediaLabError("zone_not_found", "Cette zone n'existe plus.", "Rechargez le projet.")
            project["hidden"] = [*(project.get("hidden") or []), {"cls": t.cls, "f0": t.f0, "f1": t.f1}]
            project["stats"] = {**(project.get("stats") or {}), **_cov(project)}
            return P.save(project)
        return await run_in_threadpool(run)

    @app.post("/api/censor/projects/{pid}/unhide")
    async def unhide(pid: str, body: UnhideBody):
        def run():
            project = P.load(pid)
            hidden = project.get("hidden") or []
            if 0 <= body.index < len(hidden):
                hidden.pop(body.index)
            project["hidden"] = hidden
            project["stats"] = {**(project.get("stats") or {}), **_cov(project)}
            return P.save(project)
        return await run_in_threadpool(run)

    @app.get("/api/censor/projects/{pid}/frame")
    async def frame(pid: str, f: int = 0, blur: bool = True, zoom: bool = False):
        """Une image de la vidéo (n° ``f``), floutée comme à l'export ou telle quelle ; ``zoom`` : gros plan sur sa
        plus grande zone (aperçu du niveau de flou)."""
        def run():
            project = P.load(pid)
            src = pipeline.source_path(project)
            tracks, meta = pipeline.zones(project)
            fb = T.FrameBoxes(tracks, project.get("manual") or [], project.get("hidden") or [])
            idx = max(0, min(int(meta["frames"]) - 1, int(f)))
            o = project["options"]
            if zoom:
                return render.sample(src, project["video"], float(meta["fps"]), idx, fb.at(idx), style=o["style"],
                                     shape=o["shape"], strength=int(o["strength"]))
            return render.preview(src, project["video"], float(meta["fps"]), idx, fb.at(idx), style=o["style"],
                                  shape=o["shape"], strength=int(o["strength"]), blur=blur)
        data = await run_in_threadpool(run)
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/censor/projects/{pid}/thumb")
    async def thumb(pid: str):
        p = P.load(pid).get("thumb")
        if not p or not Path(p).is_file():
            raise MediaLabError("no_thumbnail", "Pas d'aperçu.")
        return FileResponse(p, media_type="image/jpeg")

    @app.post("/api/censor/projects/{pid}/export")
    async def export(pid: str):
        project = P.load(pid)
        if project.get("status") not in ("review", "done"):
            raise MediaLabError("not_analyzed", "Attendez la fin de l'analyse.")
        return {"job": start(pid, "export")}

    @app.post("/api/censor/projects/{pid}/open-folder")
    async def open_folder(pid: str):
        project = P.load(pid)
        f = (project.get("export") or {}).get("file")
        if f and Path(f).exists():
            import subprocess
            threading.Thread(target=lambda: subprocess.run(["explorer", "/select,", f]), daemon=True).start()
            return {"path": f}
        d = P.output_dir(project)
        threading.Thread(target=lambda: os.startfile(str(d)), daemon=True).start()  # noqa: S606
        return {"path": str(d)}
