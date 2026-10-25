"""SQLite persistence for jobs (queue), history and presets."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .config import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    position REAL NOT NULL,
    title TEXT,
    source TEXT NOT NULL,
    platform TEXT,
    thumbnail TEXT,
    options TEXT NOT NULL DEFAULT '{}',
    progress TEXT NOT NULL DEFAULT '{}',
    result TEXT,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS history (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT,
    platform TEXT,
    source TEXT,
    thumbnail TEXT,
    file_path TEXT,
    folder TEXT,
    resolution TEXT,
    format TEXT,
    size INTEGER,
    duration REAL,
    options TEXT,
    meta TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at DESC);

CREATE TABLE IF NOT EXISTS fingerprints (
    path TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    mtime REAL NOT NULL,
    quick TEXT,
    full TEXT,
    dhash TEXT,
    vsig TEXT,
    duration REAL,
    width INTEGER,
    height INTEGER,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS presets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'download',
    platforms TEXT NOT NULL DEFAULT '[]',
    options TEXT NOT NULL DEFAULT '{}',
    auto_apply INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
"""

JSON_COLUMNS = {"options", "progress", "result", "error", "meta", "platforms"}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for k in JSON_COLUMNS & out.keys():
        if out[k] is not None:
            try:
                out[k] = json.loads(out[k])
            except (TypeError, json.JSONDecodeError):
                pass
    if "auto_apply" in out:
        out["auto_apply"] = bool(out["auto_apply"])
    return out


def _encode(values: dict[str, Any]) -> dict[str, Any]:
    return {k: (json.dumps(v) if k in JSON_COLUMNS and v is not None else v) for k, v in values.items()}


class Database:
    def __init__(self, path: Path | None = None):
        self.path = path or paths().db
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def _exec(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    def _one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        # Fetch while holding the lock: the connection is shared by worker threads.
        with self._lock:
            return self.conn.execute(sql, params).fetchone()

    def _all(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, params).fetchall()

    # ------------------------------------------------------------------ jobs
    def insert_job(self, job: dict[str, Any]) -> dict[str, Any]:
        now = time.time()
        row = {
            "id": job.get("id") or new_id(),
            "kind": job["kind"],
            "status": job.get("status", "waiting"),
            "priority": job.get("priority", 0),
            "position": job.get("position", now),
            "title": job.get("title"),
            "source": job["source"],
            "platform": job.get("platform"),
            "thumbnail": job.get("thumbnail"),
            "options": job.get("options", {}),
            "progress": job.get("progress", {}),
            "result": job.get("result"),
            "error": job.get("error"),
            "attempts": 0,
            "created_at": now,
            "updated_at": now,
        }
        enc = _encode(row)
        cols = ",".join(enc)
        marks = ",".join(f":{k}" for k in enc)
        self._exec(f"INSERT INTO jobs ({cols}) VALUES ({marks})", enc)
        return row

    def update_job(self, job_id: str, **values: Any) -> None:
        if not values:
            return
        values["updated_at"] = time.time()
        enc = _encode(values)
        sets = ",".join(f"{k}=:{k}" for k in enc)
        enc["_id"] = job_id
        self._exec(f"UPDATE jobs SET {sets} WHERE id=:_id", enc)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM jobs WHERE id=?", (job_id,))
        return _decode(row) if row else None

    def list_jobs(self, statuses: list[str] | None = None) -> list[dict[str, Any]]:
        if statuses:
            marks = ",".join("?" * len(statuses))
            rows = self._all(
                f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY priority DESC, position ASC",
                tuple(statuses),
            )
        else:
            rows = self._all("SELECT * FROM jobs ORDER BY priority DESC, position ASC")
        return [_decode(r) for r in rows]

    def delete_job(self, job_id: str) -> None:
        self._exec("DELETE FROM jobs WHERE id=?", (job_id,))

    def delete_jobs_by_status(self, statuses: list[str]) -> int:
        marks = ",".join("?" * len(statuses))
        return self._exec(f"DELETE FROM jobs WHERE status IN ({marks})", tuple(statuses)).rowcount

    # --------------------------------------------------------------- history
    def add_history(self, item: dict[str, Any]) -> dict[str, Any]:
        row = {
            "id": item.get("id") or new_id(),
            "kind": item["kind"],
            "title": item.get("title"),
            "platform": item.get("platform"),
            "source": item.get("source"),
            "thumbnail": item.get("thumbnail"),
            "file_path": item.get("file_path"),
            "folder": item.get("folder"),
            "resolution": item.get("resolution"),
            "format": item.get("format"),
            "size": item.get("size"),
            "duration": item.get("duration"),
            "options": item.get("options"),
            "meta": item.get("meta"),
            "created_at": item.get("created_at") or time.time(),
        }
        enc = _encode(row)
        cols = ",".join(enc)
        marks = ",".join(f":{k}" for k in enc)
        self._exec(f"INSERT OR REPLACE INTO history ({cols}) VALUES ({marks})", enc)
        return row

    def list_history(self, query: str = "", kind: str = "", platform: str = "",
                     limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        sql = "SELECT * FROM history WHERE 1=1"
        params: list[Any] = []
        if query:
            sql += " AND (title LIKE ? OR platform LIKE ? OR file_path LIKE ?)"
            q = f"%{query}%"
            params += [q, q, q]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        if platform:
            sql += " AND platform=?"
            params.append(platform)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        return [_decode(r) for r in self._all(sql, tuple(params))]

    def find_history_by_source(self, *sources: str) -> list[dict[str, Any]]:
        """Previous downloads of the same link (duplicate warning)."""
        wanted = [x for x in dict.fromkeys(sources) if x]
        if not wanted:
            return []
        marks = ",".join("?" * len(wanted))
        rows = self._all(f"SELECT * FROM history WHERE kind='download' AND source IN ({marks}) "
                         "ORDER BY created_at DESC LIMIT 5", tuple(wanted))
        return [_decode(r) for r in rows]

    def all_history(self) -> list[dict[str, Any]]:
        return [_decode(r) for r in self._all("SELECT * FROM history ORDER BY created_at DESC")]

    # ----------------------------------------------------------- fingerprints
    def get_fingerprint(self, path: str, size: int, mtime: float) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM fingerprints WHERE path=? AND size=? AND abs(mtime-?)<0.01", (path, size, mtime))
        return dict(row) if row else None

    def put_fingerprint(self, path: str, size: int, mtime: float, **values: Any) -> None:
        old = self.get_fingerprint(path, size, mtime) or {}
        row = {"path": path, "size": size, "mtime": mtime, "quick": None, "full": None, "dhash": None,
               "vsig": None, "duration": None, "width": None, "height": None,
               **{k: v for k, v in old.items() if k in ("quick", "full", "dhash", "vsig", "duration", "width", "height")},
               **values, "updated_at": time.time()}
        cols = ",".join(row)
        marks = ",".join(f":{k}" for k in row)
        self._exec(f"INSERT OR REPLACE INTO fingerprints ({cols}) VALUES ({marks})", row)

    def get_history(self, item_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM history WHERE id=?", (item_id,))
        return _decode(row) if row else None

    def delete_history(self, item_id: str) -> None:
        self._exec("DELETE FROM history WHERE id=?", (item_id,))

    def clear_history(self, older_than: float | None = None) -> int:
        if older_than:
            return self._exec("DELETE FROM history WHERE created_at < ?", (older_than,)).rowcount
        return self._exec("DELETE FROM history").rowcount

    # --------------------------------------------------------------- presets
    def list_presets(self) -> list[dict[str, Any]]:
        return [_decode(r) for r in self._all("SELECT * FROM presets ORDER BY created_at")]

    def get_preset(self, preset_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM presets WHERE id=?", (preset_id,))
        return _decode(row) if row else None

    def upsert_preset(self, preset: dict[str, Any]) -> dict[str, Any]:
        row = {
            "id": preset.get("id") or new_id(),
            "name": preset["name"],
            "kind": preset.get("kind", "download"),
            "platforms": preset.get("platforms", []),
            "options": preset.get("options", {}),
            "auto_apply": 1 if preset.get("auto_apply", True) else 0,
            "created_at": preset.get("created_at") or time.time(),
        }
        enc = _encode(row)
        cols = ",".join(enc)
        marks = ",".join(f":{k}" for k in enc)
        self._exec(f"INSERT OR REPLACE INTO presets ({cols}) VALUES ({marks})", enc)
        return _decode_dict(row)

    def delete_preset(self, preset_id: str) -> None:
        self._exec("DELETE FROM presets WHERE id=?", (preset_id,))


def _decode_dict(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    if "auto_apply" in out:
        out["auto_apply"] = bool(out["auto_apply"])
    return out
