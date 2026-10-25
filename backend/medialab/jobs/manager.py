"""Job queue shared by downloads and upscales.

* persisted in SQLite — the queue survives restarts; unfinished jobs are kept
* separate concurrency pools (downloads vs GPU jobs)
* priority + manual ordering, pause / resume / cancel / retry / remove
* automatic retries with alternative strategies proposed by the provider
* resume: downloads keep their partial files, upscales keep checkpoints
"""
from __future__ import annotations

import shutil
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import paths
from ..db import Database
from ..errors import Canceled, MediaLabError, classify
from ..events import bus
from ..logging_setup import get_logger
from ..settings import SettingsStore
from .. import thumbs

log = get_logger("app")

ACTIVE = ("analyzing", "downloading", "processing")
UNFINISHED = ("waiting", "paused", "failed", *ACTIVE)
MAX_AUTO_RETRIES = 3


@dataclass
class _Runtime:
    thread: threading.Thread
    cancel: threading.Event = field(default_factory=threading.Event)
    reason: str = "canceled"  # canceled | paused | removed
    last_persist: float = 0.0
    status: str = ""
    progress: dict[str, Any] = field(default_factory=dict)


Worker = Callable[[dict, "JobContext"], dict]


@dataclass
class JobContext:
    job: dict
    cancel: threading.Event
    progress: Callable[[dict], None]
    set_status: Callable[[str], None]
    temp_dir: Path
    attempt: int
    strategy: dict


class JobManager:
    def __init__(self, db: Database, settings: SettingsStore):
        self.db = db
        self.settings = settings
        self._workers: dict[str, Worker] = {}
        self._pools: dict[str, Callable[[], int]] = {}
        self._running: dict[str, _Runtime] = {}
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        self._stop = False
        self._scheduler: threading.Thread | None = None
        self._recover()

    # ----------------------------------------------------------- setup
    def register_worker(self, kind: str, worker: Worker, concurrency: Callable[[], int]) -> None:
        self._workers[kind] = worker
        self._pools[kind] = concurrency

    def start(self) -> None:
        self._scheduler = threading.Thread(target=self._schedule_loop, name="job-scheduler", daemon=True)
        self._scheduler.start()

    def shutdown(self) -> None:
        with self._lock:
            self._stop = True
            for rt in self._running.values():
                rt.reason = "paused"  # keep partial work for next launch
                rt.cancel.set()
            self._wake.notify_all()
        for rt in list(self._running.values()):
            rt.thread.join(timeout=5)

    def _recover(self) -> None:
        for job in self.db.list_jobs(list(ACTIVE)):
            self.db.update_job(job["id"], status="paused",
                               progress={**(job.get("progress") or {}), "stage": "interrupted",
                                         "label": "Interrupted — press Resume to continue"})
            log.info("Job %s was interrupted by a shutdown; kept as paused", job["id"])

    # ----------------------------------------------------------- public API
    def add(self, kind: str, source: str, options: dict, *, title: str | None = None,
            platform: str | None = None, thumbnail: str | None = None, priority: int = 0,
            start_paused: bool = False) -> dict:
        if kind not in self._workers:
            raise MediaLabError("unknown_job_kind", f"Unknown job type {kind}")
        job = self.db.insert_job({
            "kind": kind, "source": source, "options": options, "title": title, "platform": platform,
            "thumbnail": thumbnail, "priority": priority, "status": "paused" if start_paused else "waiting",
            "progress": {"stage": "waiting"},
        })
        self._emit(job["id"])
        self._notify()
        return self.db.get_job(job["id"])

    def list(self) -> list[dict]:
        jobs = self.db.list_jobs()
        with self._lock:
            for j in jobs:
                rt = self._running.get(j["id"])
                if rt and rt.progress:
                    j["progress"] = rt.progress
        return jobs

    def get(self, job_id: str) -> dict:
        job = self.db.get_job(job_id)
        if not job:
            raise MediaLabError("not_found", "This task no longer exists.")
        with self._lock:
            rt = self._running.get(job_id)
            if rt and rt.progress:
                job["progress"] = rt.progress
        return job

    def pause(self, job_id: str) -> dict:
        with self._lock:
            job = self.get(job_id)
            rt = self._running.get(job_id)
            if rt:
                rt.reason = "paused"
                rt.cancel.set()
            elif job["status"] in ("waiting", "failed"):
                self.db.update_job(job_id, status="paused")
                self._emit(job_id)
        return self.get(job_id)

    def resume(self, job_id: str) -> dict:
        job = self.get(job_id)
        if job["status"] in ("paused", "failed", "canceled"):
            self.db.update_job(job_id, status="waiting", error=None,
                               progress={**(job.get("progress") or {}), "stage": "waiting", "label": None})
            self._emit(job_id)
            self._notify()
        return self.get(job_id)

    def retry(self, job_id: str) -> dict:
        self.db.update_job(job_id, attempts=0)
        job = self.get(job_id)
        opts = dict(job.get("options") or {})
        opts.pop("_strategy", None)
        self.db.update_job(job_id, options=opts)
        return self.resume(job_id)

    def cancel(self, job_id: str) -> dict:
        with self._lock:
            job = self.get(job_id)
            rt = self._running.get(job_id)
            if rt:
                rt.reason = "canceled"
                rt.cancel.set()
            elif job["status"] not in ("completed", "canceled"):
                self.db.update_job(job_id, status="canceled", finished_at=time.time())
                self._cleanup_temp(job_id)
                self._emit(job_id)
        return self.get(job_id)

    def remove(self, job_id: str) -> None:
        with self._lock:
            rt = self._running.get(job_id)
            if rt:
                rt.reason = "removed"
                rt.cancel.set()
            else:
                self._cleanup_temp(job_id)
                self.db.delete_job(job_id)
        bus.publish("job_removed", {"id": job_id})

    def set_priority(self, job_id: str, priority: int) -> dict:
        self.db.update_job(job_id, priority=max(-10, min(10, int(priority))))
        self._emit(job_id)
        self._notify()
        return self.get(job_id)

    def move(self, job_id: str, direction: str) -> dict:
        """Reorder among jobs of the same priority: up | down | top | bottom."""
        job = self.get(job_id)
        peers = [j for j in self.db.list_jobs(["waiting", "paused"]) if j["priority"] == job["priority"]]
        ids = [j["id"] for j in peers]
        if job_id not in ids:
            return job
        i = ids.index(job_id)
        if direction == "top":
            pos = (peers[0]["position"] - 1) if peers else job["position"]
        elif direction == "bottom":
            pos = peers[-1]["position"] + 1
        elif direction == "up" and i > 0:
            pos = (peers[i - 1]["position"] + (peers[i - 2]["position"] if i > 1 else peers[i - 1]["position"] - 2)) / 2
        elif direction == "down" and i < len(peers) - 1:
            nxt = peers[i + 2]["position"] if i + 2 < len(peers) else peers[i + 1]["position"] + 2
            pos = (peers[i + 1]["position"] + nxt) / 2
        else:
            return job
        self.db.update_job(job_id, position=pos)
        self._emit(job_id)
        bus.publish("queue_reordered", None)
        return self.get(job_id)

    def bulk(self, action: str, kind: str | None = None) -> int:
        kinds = {k.strip() for k in (kind or "").split(",") if k.strip()}
        jobs = [j for j in self.db.list_jobs() if not kinds or j["kind"] in kinds]
        n = 0
        for j in jobs:
            s = j["status"]
            if action == "pause_all" and s in ("waiting", *ACTIVE):
                self.pause(j["id"]); n += 1
            elif action == "resume_all" and s == "paused":
                self.resume(j["id"]); n += 1
            elif action == "retry_failed" and s == "failed":
                self.retry(j["id"]); n += 1
            elif action == "clear_finished" and s in ("completed", "canceled"):
                self.db.delete_job(j["id"]); bus.publish("job_removed", {"id": j["id"]}); n += 1
            elif action == "cancel_all" and s in ("waiting", "paused", *ACTIVE):
                self.cancel(j["id"]); n += 1
        return n

    def stats(self) -> dict:
        jobs = self.db.list_jobs()
        by: dict[str, int] = {}
        for j in jobs:
            by[j["status"]] = by.get(j["status"], 0) + 1
        with self._lock:
            speed = sum((rt.progress.get("speed") or 0) for rt in self._running.values())
        return {"by_status": by, "running": len(self._running), "total_speed": speed}

    # ----------------------------------------------------------- scheduling
    def _notify(self) -> None:
        with self._lock:
            self._wake.notify_all()

    def _schedule_loop(self) -> None:
        while True:
            with self._lock:
                if self._stop:
                    return
                self._start_ready()
                self._wake.wait(timeout=2.0)

    def _start_ready(self) -> None:
        running_by_kind: dict[str, int] = {}
        for jid, rt in self._running.items():
            k = rt.thread.name.split(":", 1)[0]
            running_by_kind[k] = running_by_kind.get(k, 0) + 1
        for job in self.db.list_jobs(["waiting"]):
            kind = job["kind"]
            limit = max(1, int(self._pools.get(kind, lambda: 1)()))
            if running_by_kind.get(kind, 0) >= limit or job["id"] in self._running:
                continue
            if job.get("options", {}).get("_not_before", 0) > time.time():
                continue
            running_by_kind[kind] = running_by_kind.get(kind, 0) + 1
            t = threading.Thread(target=self._run, args=(job["id"],), name=f"{kind}:{job['id']}", daemon=True)
            self._running[job["id"]] = _Runtime(thread=t)
            self.db.update_job(job["id"], status="analyzing" if kind == "download" else "processing",
                               started_at=time.time(), error=None)
            t.start()

    # ----------------------------------------------------------- execution
    def _run(self, job_id: str) -> None:
        rt = self._running[job_id]
        job = self.db.get_job(job_id)
        assert job
        worker = self._workers[job["kind"]]
        attempt = int(job.get("attempts") or 0)
        temp_dir = paths().temp / "jobs" / job_id
        temp_dir.mkdir(parents=True, exist_ok=True)
        self._emit(job_id)

        def progress(p: dict) -> None:
            if rt.cancel.is_set():
                raise Canceled(rt.reason)
            stage = p.get("stage")
            status = {"downloading": "downloading", "processing": "processing"}.get(stage)
            merged = {**rt.progress, **p}
            if stage and stage != rt.progress.get("stage") and "label" not in p:
                merged.pop("label", None)
            rt.progress = merged
            now = time.monotonic()
            if status and rt.status != status:
                rt.status = status
                self.db.update_job(job_id, status=status)
            if now - rt.last_persist > 2:
                rt.last_persist = now
                self.db.update_job(job_id, progress=merged)
            bus.publish("job_progress", {"id": job_id, "status": status, "progress": merged})

        def set_status(status: str) -> None:
            self.db.update_job(job_id, status=status)
            self._emit(job_id)

        strategy = (job.get("options") or {}).get("_strategy") or {}
        ctx = JobContext(job=job, cancel=rt.cancel, progress=progress, set_status=set_status,
                         temp_dir=temp_dir, attempt=attempt, strategy=strategy)
        try:
            result = worker(job, ctx)
            self._complete(job_id, result)
        except Canceled:
            self._stopped(job_id, rt.reason)
        except BaseException as e:  # noqa: BLE001 — every failure must be reported, never swallowed
            err = classify(e)
            if not isinstance(e, MediaLabError):
                log.error("Job %s crashed: %s\n%s", job_id, e, traceback.format_exc(), extra={"job_id": job_id})
            self._failed(job_id, err, attempt)
        finally:
            with self._lock:
                self._running.pop(job_id, None)
                self._wake.notify_all()

    def _complete(self, job_id: str, result: dict) -> None:
        job = self.db.get_job(job_id)
        thumb = job.get("thumbnail")
        if result.get("thumbnail_url"):
            thumb = thumbs.cache_remote(result["thumbnail_url"]) or thumb
        if not thumb and result.get("file_path"):
            thumb = thumbs.cache_local(result.get("output_path") or result["file_path"])
        s = self.settings.get()
        if s.privacy.keep_history:
            fp = result.get("output_path") or result.get("file_path")
            self.db.add_history({
                "kind": job["kind"], "title": result.get("title") or job.get("title"),
                "platform": result.get("platform") or job.get("platform"), "source": job["source"],
                "thumbnail": thumb, "file_path": fp,
                "folder": str(Path(fp).parent) if fp and Path(fp).is_file() else fp,
                "resolution": result.get("resolution"), "format": result.get("format"),
                "size": result.get("size"), "duration": result.get("duration"),
                "options": {k: v for k, v in (job.get("options") or {}).items() if not k.startswith("_")},
                "meta": result.get("meta"),
            })
            bus.publish("history_changed", None)
        # Mark completed only once the history entry exists (UI and API stay consistent).
        progress = {"stage": "completed", "percent": 100.0}
        self.db.update_job(job_id, status="completed", result=result, progress=progress,
                           finished_at=time.time(), thumbnail=thumb, error=None,
                           title=result.get("title") or job.get("title"))
        self._cleanup_temp(job_id)
        log.info("Job %s completed: %s", job_id, result.get("output_path") or result.get("file_path"),
                 extra={"job_id": job_id})
        self._emit(job_id)
        bus.publish("notify", {"kind": "success", "job_id": job_id, "job_kind": job["kind"],
                               "title": result.get("title") or job.get("title"),
                               "file_path": result.get("output_path") or result.get("file_path")})

    def _stopped(self, job_id: str, reason: str) -> None:
        if reason == "removed":
            self._cleanup_temp(job_id)
            self.db.delete_job(job_id)
            return
        if reason == "paused":
            job = self.db.get_job(job_id)
            self.db.update_job(job_id, status="paused",
                               progress={**(job.get("progress") or {}), "stage": "paused", "speed": 0, "eta": None})
            log.info("Job %s paused", job_id, extra={"job_id": job_id})
        else:
            self.db.update_job(job_id, status="canceled", finished_at=time.time(),
                               progress={"stage": "canceled"})
            self._cleanup_temp(job_id)
            log.info("Job %s canceled", job_id, extra={"job_id": job_id})
        self._emit(job_id)

    def _failed(self, job_id: str, err: MediaLabError, attempt: int) -> None:
        job = self.db.get_job(job_id)
        opts = dict(job.get("options") or {})
        strategy = None
        if err.retryable and attempt < MAX_AUTO_RETRIES:
            strategy = self._fallback_for(job, err.code, attempt)
        if strategy is not None:
            delay = 60 if err.code == "rate_limited" else min(30, 2 ** (attempt + 1))
            opts["_strategy"] = strategy
            opts["_not_before"] = time.time() + delay
            label = strategy.get("label") or "Retrying"
            self.db.update_job(job_id, status="waiting", attempts=attempt + 1, options=opts,
                               error=err.to_dict(),
                               progress={**(job.get("progress") or {}), "stage": "retrying",
                                         "label": f"{label} in {delay}s (attempt {attempt + 2}/{MAX_AUTO_RETRIES + 1})",
                                         "speed": 0})
            log.warning("Job %s failed (%s: %s) — auto-retry: %s", job_id, err.code, err.message, label,
                        extra={"job_id": job_id})
            self._emit(job_id)
            threading.Timer(delay + 0.1, self._notify).start()
            return
        self.db.update_job(job_id, status="failed", error=err.to_dict(), finished_at=time.time(),
                           progress={**(job.get("progress") or {}), "stage": "failed", "speed": 0, "eta": None})
        log.error("Job %s failed: [%s] %s — %s", job_id, err.code, err.message, err.detail[:500],
                  extra={"job_id": job_id})
        self._emit(job_id)
        bus.publish("notify", {"kind": "error", "job_id": job_id, "job_kind": job["kind"],
                               "title": job.get("title"), "error": err.to_dict()})

    def _fallback_for(self, job: dict, code: str, attempt: int) -> dict | None:
        if job["kind"] == "download":
            from ..downloader.registry import registry
            try:
                provider = registry.get((job.get("options") or {}).get("provider") or "ytdlp")
            except MediaLabError:
                return None
            return provider.fallback_strategies(code, attempt)
        if job["kind"] == "upscale":
            if code in ("gpu_out_of_memory", "upscaler_crashed"):
                return {"reduce_tile": attempt + 1, "label": "Retrying with smaller tiles (less VRAM)"}
            if code == "encoder_failed":
                return {"cpu_encoder": True, "label": "Retrying with the CPU encoder"}
            if code in ("ffmpeg_failed", "unknown"):
                return {"label": "Resuming from the last checkpoint"} if attempt == 0 else None
        return None

    def _cleanup_temp(self, job_id: str) -> None:
        shutil.rmtree(paths().temp / "jobs" / job_id, ignore_errors=True)
        shutil.rmtree(paths().checkpoints / job_id, ignore_errors=True)

    def _emit(self, job_id: str) -> None:
        job = self.db.get_job(job_id)
        if job:
            with self._lock:
                rt = self._running.get(job_id)
                if rt and rt.progress:
                    job["progress"] = rt.progress
            bus.publish("job", job)
