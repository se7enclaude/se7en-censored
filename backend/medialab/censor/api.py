"""Routes de l'API et tâche de fond de se7en Censored."""
from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path

import numpy as np
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from ..errors import Canceled, MediaLabError
from ..events import bus
from ..logging_setup import get_logger
from ..upscale import onnx_rt
from . import pipeline, render
from . import project as P
from . import track as T

log = get_logger("censor")


class NewProjectBody(BaseModel):
    source: str
    options: dict = {}
    output_dir: str | None = None
    timecodes: str | None = None  # analyser seulement ces passages
    margin: float | None = None


class PreviewTimecodesBody(BaseModel):
    text: str
    margin: float = 2.0


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
    ai_outside: bool | None = None
    point_margin: float | None = None


class ManualBody(BaseModel):
    manual: list[dict]


class FollowBody(BaseModel):
    f: int
    box: list[float]
    seconds: float = 10.0


class TimecodesBody(BaseModel):
    text: str
    margin: float | None = None
    ocr: bool = False


class SegmentsBody(BaseModel):
    segments: list[dict]


class HideBody(BaseModel):
    track: str


class UnhideBody(BaseModel):
    index: int


_running = [0]  # tâches « censor » en cours : la mémoire du GPU est rendue quand la dernière finit
_running_lock = threading.Lock()


def make_worker(settings):
    """Tâche « censor » : analyse d'une vidéo, ou export de la version floutée."""
    def worker(job: dict, jctx) -> dict:
        with _running_lock:
            _running[0] += 1
        try:
            return _work(job, jctx)
        finally:
            with _running_lock:
                _running[0] -= 1
                last = _running[0] == 0
            if last:
                try:
                    onnx_rt.release()
                except Exception:  # pragma: no cover
                    log.exception("AI release failed")

    def _work(job: dict, jctx) -> dict:
        s = settings.get()
        opts = dict(job.get("options") or {})
        pid = str(opts.get("project") or "")
        op = opts.get("op") or "analyze"

        def progress(frac: float, label: str, eta: float | None = None) -> None:
            jctx.progress({"stage": "processing", "label": label, "percent": frac * 100, "eta": eta, "speed": 0})
            bus.publish("censor_progress", {"project": pid, "op": op, "percent": frac * 100, "label": label,
                                            "eta": eta})

        try:
            if op == "preview":
                project = pipeline.build_preview(pid, jctx.cancel, progress)
                bus.publish("censor_project", {"project": pid})
                return {"file_path": None, "title": project["name"], "platform": "censor", "format": "aperçu",
                        "duration": (project.get("video") or {}).get("duration"), "meta": {"project": pid}}
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
            if P.exists(pid) and op == "preview":
                P.patch(pid, {"preview_job": None})
                bus.publish("censor_project", {"project": pid})
            elif P.exists(pid):
                P.patch(pid, {"status": "paused" if op == "analyze" else "review", "step": None})
                bus.publish("censor_project", {"project": pid})
            raise
        except MediaLabError as e:
            if not P.exists(pid):  # projet supprimé pendant la tâche : simple arrêt, pas une erreur
                raise Canceled() from None
            _failed(pid, op, {"code": e.code, "message": e.message, "hint": e.hint})
            raise
        except Exception as e:
            if not P.exists(pid):
                raise Canceled() from None
            log.exception("Censor job failed")
            _failed(pid, op, {"message": str(e), "hint": "Voir le journal."})
            raise
    return worker


def _failed(pid: str, op: str, error: dict) -> None:
    if op == "preview":  # le projet reste vérifiable image par image
        P.patch(pid, {"preview_job": None, "preview_error": error})
    else:
        P.patch(pid, {"status": "error" if op == "analyze" else "review", "step": None, "error": error})
    bus.publish("censor_project", {"project": pid})


ACTIVE_JOBS = ("waiting", "analyzing", "downloading", "processing")


def register(app: FastAPI, S) -> None:
    def start(pid: str, op: str) -> dict:
        project = P.load(pid)
        if project.get("status") in ("queued", "analyzing", "exporting") and _job_active(project):
            raise MediaLabError("busy", "Une analyse ou un export est déjà en cours sur ce projet.",
                                "Attends qu'il finisse, ou arrête-le.")
        src = project["source"].get("path") or project["source"].get("url") or ""
        P.patch(pid, {"status": "queued" if op == "analyze" else "exporting", "error": None})
        job = S.jobs.add("censor", src, {"op": op, "project": pid}, title=project["name"], platform="censor")
        P.patch(pid, {"job": job["id"]})
        bus.publish("censor_project", {"project": pid})
        return job

    def _job_active(project: dict) -> bool:
        try:
            return bool(project.get("job")) and S.jobs.get(project["job"]).get("status") in ACTIVE_JOBS
        except MediaLabError:
            return False

    def recover() -> None:
        """Au lancement : un projet resté « en cours » (appli fermée pendant une analyse ou un export) redevient
        reprenable, et les fichiers de travail d'un export interrompu sont effacés."""
        for item in P.listing():
            if item.get("status") not in ("queued", "analyzing", "exporting"):
                continue
            try:
                project = P.load(item["id"])
                if _job_active(project):
                    continue
                exporting = project.get("status") == "exporting"
                P.patch(project["id"], {"status": "review" if exporting else "paused", "step": None})
                if exporting and project["source"].get("path"):
                    for f in render.leftovers(Path(project["source"]["path"]), P.output_dir(project)):
                        shutil.rmtree(f, ignore_errors=True) if f.is_dir() else f.unlink(missing_ok=True)
                        log.info("Removed leftover of an interrupted export: %s", f)
            except Exception:  # noqa: BLE001 - un projet abîmé ne doit pas empêcher le lancement
                log.exception("Could not recover censor project %s", item.get("id"))

    def cancel_job(project: dict) -> None:
        if project.get("job"):
            try:
                S.jobs.cancel(project["job"])
            except MediaLabError:
                pass

    recover()

    @app.get("/api/censor/catalog")
    async def catalog():
        return {"classes": [{"id": c, "label": label, "default": on} for c, label, on in T.CLASSES],
                "defaults": P.DEFAULT_OPTIONS, "output_dir": str(P.default_output())}

    @app.post("/api/censor/timecodes")
    async def preview_timecodes(body: PreviewTimecodesBody):
        """Aperçu des timecodes avant de créer le projet (cadence encore inconnue : 25 images/s pour HH:MM:SS:FF)."""
        from . import timecodes as TC
        return TC.parse(body.text[:500_000], 25.0, None, max(0.0, min(60.0, body.margin)))

    @app.post("/api/censor/timecodes/image")
    async def preview_timecodes_image(request: Request, margin: float = 2.0):
        """Lit les timecodes d'une capture d'écran avant de créer le projet (sur ce PC)."""
        data = await request.body()
        if not data or len(data) > 40_000_000:
            raise MediaLabError("bad_image", "Image vide ou trop grande.", "Colle une capture d'écran.")

        def run():
            from . import ocr
            from . import timecodes as TC
            lines = ocr.Reader().read(data)
            return {"lines": lines, **TC.parse("\n".join(lines), 25.0, None, max(0.0, min(60.0, margin)), ocr=True)}
        return await run_in_threadpool(run)

    @app.get("/api/censor/projects")
    async def listing():
        return {"projects": await run_in_threadpool(P.listing)}

    @app.post("/api/censor/projects")
    async def create(body: NewProjectBody):
        project = await run_in_threadpool(P.create, body.source, body.options, body.output_dir, body.timecodes,
                                          body.margin)
        job = start(project["id"], "analyze")
        return {"project": P.load(project["id"]), "job": job}

    @app.get("/api/censor/projects/{pid}")
    async def get(pid: str):
        return P.load(pid)

    @app.post("/api/censor/projects/{pid}/preview")
    async def preview(pid: str):
        """Copie d'aperçu d'un projet analysé avant qu'elle existe : préparée à part, le projet reste vérifiable."""
        project = P.load(pid)
        pv = project.get("preview") or {}
        if pv.get("direct") or (pv.get("path") and Path(pv["path"]).is_file()):
            return {"preview": pv}
        job_id = project.get("preview_job")
        if job_id:
            try:
                if S.jobs.get(job_id).get("status") in ACTIVE_JOBS:
                    return {"job": job_id}
            except MediaLabError:
                pass
        src = project["source"].get("path") or ""
        job = S.jobs.add("censor", src, {"op": "preview", "project": pid}, title=project["name"], platform="censor")
        P.patch(pid, {"preview_job": job["id"], "preview_error": None})
        return {"job": job["id"]}

    @app.delete("/api/censor/projects/{pid}")
    async def delete(pid: str):
        project = P.load(pid)
        cancel_job(project)
        P.delete(pid)
        return {"ok": True}

    @app.post("/api/censor/projects/{pid}/analyze")
    async def reanalyze(pid: str, all: bool = False):
        """Relance l'analyse : les passages pas encore analysés, ou toute la vidéo (``all``) ; ce qui l'a déjà été
        est gardé."""
        if all:
            project = P.load(pid)
            if project.get("scope_only"):
                P.patch(pid, {"scope_only": False, "options": {**project["options"], "ai_outside": True}})
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
            out = {"options": project["options"]}
            if project.get("status") in ("review", "done"):
                out["stats"] = {**(project.get("stats") or {}), **_cov(project)}
            return P.patch(pid, out)
        return await run_in_threadpool(run)

    def _cov(project: dict) -> dict:
        tracks, meta = pipeline.zones(project)
        return pipeline.coverage(project, tracks, meta)

    @app.get("/api/censor/projects/{pid}/zones")
    async def zones(pid: str):
        def run():
            project = P.load(pid)
            tracks, meta = pipeline.zones(project)
            hidden = project.get("hidden") or []
            return {"fps": meta["fps"], "frames": meta["frames"],
                    "tracks": [{**t.to_dict(), "hidden": T.hidden_match(t, hidden),
                                "validated": T.hidden_match(t, project.get("validated") or [])} for t in tracks],
                    "manual": project.get("manual") or [], "hidden": hidden, "cuts": meta.get("cuts", []),
                    "segments": T.clean_segments(project.get("segments")),
                    "refined": bool(meta.get("refined")), "stats": _cov(project),
                    "scope_only": bool(project.get("scope_only")), "unanalysed": pipeline.unanalysed(project, meta)}
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
            return P.patch(pid, {"manual": clean, "stats": {**(project.get("stats") or {}), **_cov(project)}})
        return await run_in_threadpool(run)

    def _timing(project: dict) -> tuple[float, int]:
        loaded = pipeline.detect.load(P.cache_dir(project["id"]))
        meta = loaded[1] if loaded else {}
        video = project.get("video") or {}
        fps = float(meta.get("fps") or video.get("fps") or 25.0)
        return fps, int(meta.get("frames") or round(float(video.get("duration") or 0) * fps))

    def _read_timecodes(project: dict, text: str, margin: float | None, ocr: bool) -> dict:
        from . import timecodes as TC
        fps, frames = _timing(project)
        m = float(project["options"].get("point_margin", 2.0) if margin is None else margin)
        res = TC.parse(text, fps, frames / fps if frames else None, max(0.0, min(60.0, m)), ocr=ocr)
        for r in res["ranges"]:
            r["f0"], r["f1"] = TC.to_frames(r["start"], r["end"], fps, frames or None)
        return res

    @app.post("/api/censor/projects/{pid}/timecodes")
    async def timecodes(pid: str, body: TimecodesBody):
        """Lit des timecodes dans un texte collé ou un fichier (.txt, .srt, .csv) : plages à vérifier avant de les
        appliquer, et lignes ignorées avec la raison."""
        return await run_in_threadpool(_read_timecodes, P.load(pid), body.text[:500_000], body.margin, body.ocr)

    @app.post("/api/censor/projects/{pid}/timecodes/image")
    async def timecodes_image(pid: str, request: Request, margin: float | None = None):
        """Lit les timecodes d'une capture d'écran, sur ce PC (corps de la requête : l'image)."""
        data = await request.body()
        if not data or len(data) > 40_000_000:
            raise MediaLabError("bad_image", "Image vide ou trop grande.", "Colle une capture d'écran.")

        def run():
            from . import ocr
            lines = ocr.Reader().read(data)
            return {"lines": lines, **_read_timecodes(P.load(pid), "\n".join(lines), margin, True)}
        return await run_in_threadpool(run)

    @app.put("/api/censor/projects/{pid}/segments")
    async def segments(pid: str, body: SegmentsBody):
        def run():
            project = P.load(pid)
            _, frames = _timing(project)
            clean = []
            for i, z in enumerate(T.clean_segments(body.segments)):
                f1 = min(z["f1"], frames - 1) if frames else z["f1"]
                if f1 < z["f0"]:
                    continue
                clean.append({"id": str(z.get("id") or i), "f0": z["f0"], "f1": f1, "mode": z["mode"],
                              "label": str(z.get("label") or "")[:200], "validated": bool(z.get("validated"))})
            project["segments"] = clean
            return P.patch(pid, {"segments": clean, "stats": {**(project.get("stats") or {}), **_cov(project)}})
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
                raise MediaLabError("zone_not_found", "Cette zone n'existe plus.", "Recharge le projet.")
            project["hidden"] = [*(project.get("hidden") or []), {"cls": t.cls, "f0": t.f0, "f1": t.f1}]
            return P.patch(pid, {"hidden": project["hidden"],
                                 "stats": {**(project.get("stats") or {}), **_cov(project)}})
        return await run_in_threadpool(run)

    @app.post("/api/censor/projects/{pid}/unhide")
    async def unhide(pid: str, body: UnhideBody):
        def run():
            project = P.load(pid)
            hidden = project.get("hidden") or []
            if 0 <= body.index < len(hidden):
                hidden.pop(body.index)
            project["hidden"] = hidden
            return P.patch(pid, {"hidden": hidden, "stats": {**(project.get("stats") or {}), **_cov(project)}})
        return await run_in_threadpool(run)

    @app.post("/api/censor/projects/{pid}/validate")
    async def validate(pid: str, body: HideBody):
        """Zone de l'IA vérifiée et gardée : elle reste floutée et quitte la liste « à vérifier »."""
        def run():
            project = P.load(pid)
            tracks, _ = pipeline.zones(project)
            t = next((t for t in tracks if t.id == body.track), None)
            if t is None:
                raise MediaLabError("zone_not_found", "Cette zone n'existe plus.", "Recharge le projet.")
            done = [v for v in project.get("validated") or [] if not T.hidden_match(t, [v])]
            return P.patch(pid, {"validated": [*done, {"cls": t.cls, "f0": t.f0, "f1": t.f1}]})
        return await run_in_threadpool(run)

    @app.post("/api/censor/projects/{pid}/unvalidate")
    async def unvalidate(pid: str, body: HideBody):
        def run():
            project = P.load(pid)
            tracks, _ = pipeline.zones(project)
            t = next((t for t in tracks if t.id == body.track), None)
            keep = [v for v in project.get("validated") or [] if t is None or not T.hidden_match(t, [v])]
            return P.patch(pid, {"validated": keep})
        return await run_in_threadpool(run)

    @app.get("/api/censor/projects/{pid}/frame")
    async def frame(pid: str, f: int = 0, blur: bool = True, zoom: bool = False):
        """Une image de la vidéo (n° ``f``), floutée comme à l'export ou telle quelle ; ``zoom`` : gros plan sur sa
        plus grande zone (aperçu du niveau de flou)."""
        def run():
            project = P.load(pid)
            src = pipeline.source_path(project)
            tracks, meta = pipeline.zones(project)
            fb = pipeline.frame_boxes(project, tracks)
            idx = max(0, min(int(meta["frames"]) - 1, int(f)))
            o = project["options"]
            if zoom:
                return render.sample(src, project["video"], float(meta["fps"]), idx, fb.at(idx), style=o["style"],
                                     shape=o["shape"], strength=int(o["strength"]))
            pv = project.get("preview") or {}
            if pv.get("path") and Path(pv["path"]).is_file():
                # la copie d'aperçu : bien plus rapide à lire que l'original 4K, et mêmes couleurs qu'à la lecture
                src, vid = Path(pv["path"]), {"width": pv["width"], "height": pv["height"]}
            else:
                vid = project["video"]
            return render.preview(src, vid, float(meta["fps"]), idx, fb.at(idx), style=o["style"],
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
        if project.get("status") == "exporting" and _job_active(project):
            raise MediaLabError("busy", "L'export est déjà en cours.")
        if project.get("status") not in ("review", "done"):
            raise MediaLabError("not_analyzed", "Attends la fin de l'analyse.")
        return {"job": start(pid, "export")}

    @app.post("/api/censor/projects/{pid}/open-folder")
    async def open_folder(pid: str):
        project = P.load(pid)
        f = (project.get("export") or {}).get("file")
        if f and Path(f).exists():
            from ..system import explorer
            explorer(f'/select,"{f}"')  # hors du job de se7en : la fenêtre reste ouverte après se7en
            return {"path": f}
        d = P.output_dir(project)
        threading.Thread(target=lambda: os.startfile(str(d)), daemon=True).start()  # noqa: S606
        return {"path": str(d)}
