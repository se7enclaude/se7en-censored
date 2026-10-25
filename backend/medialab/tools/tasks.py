"""Short background tasks that are not queue jobs (scans): progress over the event bus."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from typing import Any, Callable

from ..errors import Canceled, MediaLabError, classify
from ..events import bus
from ..logging_setup import get_logger

log = get_logger("app")


class Task:
    def __init__(self, kind: str):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind
        self.state = "running"  # running | done | failed | canceled
        self.progress: dict[str, Any] = {"percent": None, "label": None}
        self.result: Any = None
        self.error: dict | None = None
        self.cancel = threading.Event()
        self.started = time.time()
        self._last = 0.0

    def to_dict(self, with_result: bool = True) -> dict:
        d = {"id": self.id, "kind": self.kind, "state": self.state, "progress": self.progress,
             "error": self.error, "started": self.started}
        if with_result:
            d["result"] = self.result
        return d

    def update(self, percent: float | None = None, label: str | None = None, **extra: Any) -> None:
        if self.cancel.is_set():
            raise Canceled()
        self.progress = {"percent": percent, "label": label, **extra}
        now = time.monotonic()
        if now - self._last > 0.15:
            self._last = now
            bus.publish("task", self.to_dict(with_result=False))


_tasks: dict[str, Task] = {}
_lock = threading.Lock()


def start(kind: str, fn: Callable[[Task], Any]) -> Task:
    task = Task(kind)
    with _lock:
        # keep only the latest few finished tasks in memory
        done = [t for t in _tasks.values() if t.state != "running"]
        for t in sorted(done, key=lambda t: t.started)[:-10]:
            _tasks.pop(t.id, None)
        _tasks[task.id] = task

    def run() -> None:
        try:
            task.result = fn(task)
            task.state = "done"
        except Canceled:
            task.state = "canceled"
        except MediaLabError as e:
            task.state, task.error = "failed", e.to_dict()
        except Exception as e:  # noqa: BLE001
            log.error("Task %s crashed: %s\n%s", kind, e, traceback.format_exc())
            task.state, task.error = "failed", classify(e).to_dict()
        bus.publish("task", task.to_dict(with_result=False))

    threading.Thread(target=run, name=f"task:{kind}", daemon=True).start()
    bus.publish("task", task.to_dict(with_result=False))
    return task


def get(task_id: str) -> Task:
    t = _tasks.get(task_id)
    if not t:
        raise MediaLabError("not_found", "This task no longer exists.")
    return t


def cancel(task_id: str) -> None:
    get(task_id).cancel.set()
