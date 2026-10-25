"""Thread-safe event bus bridging worker threads to WebSocket clients."""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = threading.Lock()
        self._sync_listeners: list = []

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        with self._lock:
            self._subscribers.add(q)
        return q

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    def add_listener(self, cb) -> None:
        """Synchronous in-process listener (used by the CLI waiter and tests)."""
        self._sync_listeners.append(cb)

    def publish(self, type_: str, data: Any = None) -> None:
        event = {"type": type_, "data": data, "ts": time.time()}
        for cb in list(self._sync_listeners):
            try:
                cb(event)
            except Exception:  # listeners must never break publishers
                pass
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                loop.call_soon_threadsafe(self._put, q, event)
            except RuntimeError:
                pass

    @staticmethod
    def _put(q: asyncio.Queue, event: dict) -> None:
        if q.full():
            try:
                q.get_nowait()  # drop oldest; clients resync from REST anyway
            except asyncio.QueueEmpty:
                pass
        q.put_nowait(event)


bus = EventBus()
