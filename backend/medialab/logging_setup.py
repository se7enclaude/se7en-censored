"""Logging: rotating file + in-memory ring buffer streamed to the Logs page.

Categories (logger names): medialab.app, medialab.download, medialab.process,
medialab.gpu. Warnings/errors of every category are visible in the UI.
"""
from __future__ import annotations

import collections
import logging
import logging.handlers
import threading
import time

from .config import paths
from .events import bus

CATEGORIES = ("app", "download", "process", "gpu")


def get_logger(category: str) -> logging.Logger:
    return logging.getLogger(f"medialab.{category}")


class RingBufferHandler(logging.Handler):
    def __init__(self, capacity: int = 5000):
        super().__init__()
        self.records: collections.deque = collections.deque(maxlen=capacity)
        self._lock2 = threading.Lock()
        self._seq = 0

    def emit(self, record: logging.LogRecord) -> None:
        try:
            category = record.name.split(".", 1)[1].split(".")[0] if "." in record.name else "app"
            with self._lock2:
                self._seq += 1
                entry = {
                    "seq": self._seq,
                    "ts": record.created,
                    "level": record.levelname.lower(),
                    "category": category if category in CATEGORIES else "app",
                    "message": record.getMessage(),
                    "job_id": getattr(record, "job_id", None),
                }
                if record.exc_info:
                    entry["exception"] = logging.Formatter().formatException(record.exc_info)
                self.records.append(entry)
            bus.publish("log", entry)
        except Exception:
            self.handleError(record)

    def snapshot(self, since: int = 0, level: str | None = None, category: str | None = None) -> list[dict]:
        order = ["debug", "info", "warning", "error", "critical"]
        min_idx = order.index(level) if level in order else 0
        with self._lock2:
            items = list(self.records)
        return [
            r for r in items
            if r["seq"] > since
            and order.index(r["level"]) >= min_idx
            and (not category or r["category"] == category)
        ]

    def clear(self) -> None:
        with self._lock2:
            self.records.clear()


ring = RingBufferHandler()
_configured = False


def setup_logging(debug: bool = False) -> None:
    global _configured
    root = logging.getLogger("medialab")
    if not _configured:
        fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        fh = logging.handlers.RotatingFileHandler(
            paths().logs / "medialab.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
        root.addHandler(ring)
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(logging.INFO)
        root.addHandler(sh)
        root.propagate = False
        _configured = True
    set_debug(debug)


def set_debug(debug: bool) -> None:
    logging.getLogger("medialab").setLevel(logging.DEBUG if debug else logging.INFO)


class Timer:
    """Context manager logging the processing time of a step."""

    def __init__(self, logger: logging.Logger, label: str, **extra):
        self.logger, self.label, self.extra = logger, label, extra

    def __enter__(self):
        self.t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.elapsed = time.perf_counter() - self.t0
        self.logger.info("%s took %.2fs", self.label, self.elapsed, extra=self.extra)
        return False
