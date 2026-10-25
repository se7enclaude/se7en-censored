"""Duplicate finder.

* exact    — same bytes: grouped by size, then a quick hash (first/last 64 KB),
             then a full BLAKE2 hash only for the remaining candidates.
* similar  — images: 64-bit difference hash (dHash), robust to resizing and
             recompression; videos: same duration (±1.5 s) and matching dHash
             of three frames. Nothing is compared by name only.

Fingerprints are cached in the database (path + size + mtime), so a second
scan of the same folders is fast. The scan never deletes anything: it returns
groups with a suggested file to keep; the user decides.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from ..components import CREATE_NO_WINDOW, find_binary
from ..db import Database
from ..logging_setup import get_logger
from ..media import ffmpeg
from ..media.images import open_image
from .. import thumbs
from .tasks import Task

log = get_logger("app")
SKIP_DIRS = {".medialab-parts", "$recycle.bin", "system volume information", "node_modules", ".git", "__pycache__",
             "windows", "program files", "program files (x86)", "appdata"}
QUICK = 64 * 1024


def _walk(folders: list[str], kinds: set[str], recursive: bool, min_size: int, task: Task) -> list[dict]:
    files: dict[str, dict] = {}
    for root in folders:
        base = Path(root)
        if not base.is_dir():
            continue
        for dirpath, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")] if recursive else []
            for n in names:
                p = Path(dirpath) / n
                kind = ffmpeg.file_kind(p)
                if kind not in kinds or ".se7en-part" in n:
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_size < min_size:
                    continue
                key = os.path.normcase(str(p.resolve()))
                files[key] = {"path": str(p), "size": st.st_size, "mtime": st.st_mtime, "kind": kind}
            if len(files) % 200 == 0:
                task.update(None, "listing", found=len(files))
    return list(files.values())


def _quick_hash(path: str, size: int) -> str:
    h = hashlib.blake2b(digest_size=16)
    h.update(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(QUICK))
        if size > 2 * QUICK:
            f.seek(-QUICK, os.SEEK_END)
            h.update(f.read(QUICK))
    return h.hexdigest()


def _full_hash(path: str, task: Task) -> str:
    h = hashlib.blake2b(digest_size=20)
    with open(path, "rb") as f:
        while chunk := f.read(4 << 20):
            if task.cancel.is_set():
                task.update()  # raises Canceled
            h.update(chunk)
    return h.hexdigest()


def dhash_image(im) -> int:
    """Difference hash: 8x8 comparisons of neighbouring pixels on a 9x8 grayscale thumbnail."""
    from PIL import Image
    g = im.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = list(g.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return bits


def _image_sig(f: dict) -> tuple[int | None, int | None, int | None]:
    try:
        with open_image(f["path"]) as im:
            w, h = im.size
            im.draft("RGB", (256, 256))  # fast JPEG decode at reduced size
            return dhash_image(im), w, h
    except Exception:
        return None, None, None


def _video_sig(f: dict, duration: float) -> str | None:
    ff = find_binary("ffmpeg")
    if not ff or not duration:
        return None
    from io import BytesIO
    from PIL import Image
    hashes = []
    for frac in (0.2, 0.5, 0.8):
        r = subprocess.run([ff, "-hide_banner", "-v", "error", "-ss", f"{duration * frac:.2f}", "-i", f["path"],
                            "-frames:v", "1", "-vf", "scale=64:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                           capture_output=True, timeout=60, creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0 or not r.stdout:
            return None
        with Image.open(BytesIO(r.stdout)) as im:
            hashes.append(f"{dhash_image(im):016x}")
    return ",".join(hashes)


def _ham(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _close_pairs(hashes: list[int], threshold: int, task: Task):
    """Index pairs whose Hamming distance is <= threshold.

    Small sets: all pairs. Large sets: pigeonhole on 8 byte-blocks (two hashes within
    7 bits share at least one identical byte at the same position), which only
    compares plausible candidates.
    """
    n = len(hashes)
    if n <= 3000 or threshold > 7:
        for i in range(n):
            hi = hashes[i]
            for j in range(i + 1, n):
                if _ham(hi, hashes[j]) <= threshold:
                    yield i, j
            if i % 400 == 0 and task.cancel.is_set():
                task.update()
        return
    seen: set[tuple[int, int]] = set()
    for block in range(8):
        shift = block * 8
        buckets: dict[int, list[int]] = {}
        for i, h in enumerate(hashes):
            buckets.setdefault((h >> shift) & 0xFF, []).append(i)
        for members in buckets.values():
            for a in range(len(members)):
                i = members[a]
                for b in range(a + 1, len(members)):
                    j = members[b]
                    if (i, j) in seen:
                        continue
                    if _ham(hashes[i], hashes[j]) <= threshold:
                        seen.add((i, j))
                        yield i, j
            if task.cancel.is_set():
                task.update()


class _UF:
    def __init__(self) -> None:
        self.p: dict[int, int] = {}

    def find(self, x: int) -> int:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        self.p[self.find(a)] = self.find(b)


_COPY_RE = re.compile(r"(\s\(\d+\)|\s-\s?copie|\s-\s?copy|_\d+)$", re.I)


def _keep_score(f: dict) -> tuple:
    """Which file to keep: best quality, then the 'original' name (no " (1)"), then the oldest."""
    stem = Path(f["path"]).stem
    return ((f.get("width") or 0) * (f.get("height") or 0), f.get("duration") or 0, f["size"],
            0 if _COPY_RE.search(stem) else 1, -f["mtime"])


def scan(db: Database, folders: list[str], opts: dict[str, Any], task: Task) -> dict:
    kinds = set(opts.get("kinds") or ["video", "audio", "image"])
    similar = opts.get("similar", True)
    threshold = int(opts.get("threshold") or 6)
    files = _walk(folders, kinds, opts.get("recursive", True), int(opts.get("min_size_kb") or 4) * 1024, task)
    total = len(files)
    task.update(2, "listing", found=total)
    # ---------------------------------------------------------- exact duplicates
    by_size: dict[int, list[dict]] = {}
    for f in files:
        by_size.setdefault(f["size"], []).append(f)
    candidates = [g for g in by_size.values() if len(g) > 1]
    n_cand = sum(len(g) for g in candidates)
    done = 0
    exact_groups: list[list[dict]] = []
    for group in candidates:
        by_quick: dict[str, list[dict]] = {}
        for f in group:
            done += 1
            fp = db.get_fingerprint(f["path"], f["size"], f["mtime"]) or {}
            q = fp.get("quick")
            if not q:
                try:
                    q = _quick_hash(f["path"], f["size"])
                except OSError:
                    continue
                db.put_fingerprint(f["path"], f["size"], f["mtime"], quick=q)
            f["_fp"] = {**fp, "quick": q}
            by_quick.setdefault(q, []).append(f)
            task.update(2 + 40 * done / max(1, n_cand), "hashing", found=total)
        for same in by_quick.values():
            if len(same) < 2:
                continue
            by_full: dict[str, list[dict]] = {}
            for f in same:
                full = f["_fp"].get("full")
                if not full:
                    try:
                        full = _full_hash(f["path"], task)
                    except OSError:
                        continue
                    db.put_fingerprint(f["path"], f["size"], f["mtime"], full=full)
                by_full.setdefault(full, []).append(f)
            exact_groups += [g for g in by_full.values() if len(g) > 1]
    in_exact = {id(f) for g in exact_groups for f in g}

    # ---------------------------------------------------------- similar
    similar_groups: list[list[dict]] = []
    if similar:
        images = [f for f in files if f["kind"] == "image"]
        videos = [f for f in files if f["kind"] == "video"]
        work = len(images) + len(videos)
        k = 0
        for f in images:
            k += 1
            fp = db.get_fingerprint(f["path"], f["size"], f["mtime"]) or {}
            if fp.get("dhash"):
                f["_h"], f["width"], f["height"] = int(fp["dhash"], 16), fp.get("width"), fp.get("height")
            else:
                h, w, hh = _image_sig(f)
                if h is None:
                    continue
                f["_h"], f["width"], f["height"] = h, w, hh
                db.put_fingerprint(f["path"], f["size"], f["mtime"], dhash=f"{h:016x}", width=w, height=hh)
            task.update(42 + 40 * k / max(1, work), "comparing", found=total)
        uf = _UF()
        hashed = [f for f in images if "_h" in f]
        for i, j in _close_pairs([f["_h"] for f in hashed], threshold, task):
            uf.union(i, j)
        groups: dict[int, list[dict]] = {}
        for i, f in enumerate(hashed):
            if i in uf.p:
                groups.setdefault(uf.find(i), []).append(f)
        for g in groups.values():
            if len(g) > 1 and not all(id(f) in in_exact for f in g):
                similar_groups.append(g)

        # videos: duration first (cheap), frames only for close durations
        for f in videos:
            k += 1
            fp = db.get_fingerprint(f["path"], f["size"], f["mtime"]) or {}
            if fp.get("duration") is None:
                try:
                    info = ffmpeg.probe(f["path"])
                except Exception:
                    continue
                db.put_fingerprint(f["path"], f["size"], f["mtime"], duration=info.get("duration"),
                                   width=info.get("width"), height=info.get("height"))
                fp = {**fp, "duration": info.get("duration"), "width": info.get("width"), "height": info.get("height")}
            f.update(duration=fp.get("duration"), width=fp.get("width"), height=fp.get("height"), _vs=fp.get("vsig"))
            task.update(42 + 40 * k / max(1, work), "comparing", found=total)
        vids = sorted((f for f in videos if f.get("duration")), key=lambda f: f["duration"])
        uf = _UF()
        for i in range(len(vids)):
            for j in range(i + 1, len(vids)):
                if vids[j]["duration"] - vids[i]["duration"] > 1.5:
                    break
                if id(vids[i]) in in_exact and id(vids[j]) in in_exact:
                    continue
                for f in (vids[i], vids[j]):
                    if not f.get("_vs"):
                        f["_vs"] = _video_sig(f, f["duration"])
                        if f["_vs"]:
                            db.put_fingerprint(f["path"], f["size"], f["mtime"], vsig=f["_vs"])
                a, b = vids[i].get("_vs"), vids[j].get("_vs")
                if a and b:
                    dist = sum(_ham(int(x, 16), int(y, 16)) for x, y in zip(a.split(","), b.split(","))) / 3
                    if dist <= threshold + 2:
                        uf.union(i, j)
        vgroups: dict[int, list[dict]] = {}
        for i, f in enumerate(vids):
            if i in uf.p:
                vgroups.setdefault(uf.find(i), []).append(f)
        similar_groups += [g for g in vgroups.values() if len(g) > 1 and not all(id(f) in in_exact for f in g)]

    task.update(90, "thumbnails", found=total)
    out = []
    for kind_label, groups in (("exact", exact_groups), ("similar", similar_groups)):
        for g in groups:
            best = max(g, key=_keep_score)
            items = []
            for f in sorted(g, key=_keep_score, reverse=True):
                items.append({"path": f["path"], "name": Path(f["path"]).name, "folder": str(Path(f["path"]).parent),
                              "size": f["size"], "mtime": f["mtime"], "kind": f["kind"],
                              "width": f.get("width"), "height": f.get("height"), "duration": f.get("duration"),
                              "keep": f is best, "thumbnail": thumbs.cache_local(f["path"]) if f["kind"] != "audio" else None})
            out.append({"type": kind_label, "kind": g[0]["kind"], "files": items,
                        "wasted": sum(f["size"] for f in g) - best["size"]})
    out.sort(key=lambda g: (g["type"] != "exact", -g["wasted"]))  # identical copies first
    task.update(100, "done", found=total)
    log.info("Duplicate scan: %d files, %d exact groups, %d similar groups", total, len(exact_groups), len(similar_groups))
    return {"groups": out, "scanned": total, "folders": folders,
            "wasted": sum(g["wasted"] for g in out if g["type"] == "exact")}
