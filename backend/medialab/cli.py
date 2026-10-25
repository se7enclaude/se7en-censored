"""Command-line client for integrations (After Effects, Premiere, DaVinci, scripts).

Talks to the running Media Lab through its local API (session file written at
startup). Examples:

    medialab-cli upscale "C:/footage/shot.mp4" --model realesr-animevideov3 --scale 2 --wait
    medialab-cli download "https://..." --quality 1080 --wait
    medialab-cli status
    medialab-cli install-components [ffmpeg realesrgan deno aria2]

With ``--wait`` the command blocks until the task finishes and prints the
output path on stdout (exit code 0), or the error (exit code 1).
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

from .security import TOKEN_HEADER, read_session_file


def _client() -> httpx.Client:
    info = read_session_file()
    if not info:
        print("se7en is not running. Start it first.", file=sys.stderr)
        raise SystemExit(2)
    return httpx.Client(base_url=f"http://127.0.0.1:{info['port']}", headers={TOKEN_HEADER: info["token"]},
                        timeout=60)


def _wait(c: httpx.Client, job_id: str) -> int:
    last = None
    while True:
        job = c.get(f"/api/jobs/{job_id}").json()
        st = job.get("status")
        pct = (job.get("progress") or {}).get("percent")
        line = f"{st} {pct:.1f}%" if isinstance(pct, (int, float)) else st
        if line != last:
            print(line, file=sys.stderr)
            last = line
        if st == "completed":
            res = job.get("result") or {}
            print(res.get("output_path") or res.get("file_path"))
            return 0
        if st in ("failed", "canceled"):
            err = job.get("error") or {}
            print(f"{st}: {err.get('message', '')} {err.get('hint', '')}", file=sys.stderr)
            return 1
        time.sleep(1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="se7en-cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("upscale")
    up.add_argument("paths", nargs="+")
    up.add_argument("--model")
    up.add_argument("--scale", type=float)
    up.add_argument("--width", type=int)
    up.add_argument("--height", type=int)
    up.add_argument("--denoise", type=int, default=0)
    up.add_argument("--sharpen", type=int, default=0)
    up.add_argument("--output-dir")
    up.add_argument("--wait", action="store_true")
    dl = sub.add_parser("download")
    dl.add_argument("url")
    dl.add_argument("--quality", default="best")
    dl.add_argument("--mode", default="av", choices=["av", "video", "audio"])
    dl.add_argument("--container", default="mp4")
    dl.add_argument("--output-dir")
    dl.add_argument("--wait", action="store_true")
    sub.add_parser("status")
    ic = sub.add_parser("install-components", help="install FFmpeg / Real-ESRGAN (no running app needed)")
    ic.add_argument("names", nargs="*", default=["ffmpeg", "realesrgan"])
    args = ap.parse_args(argv)

    if args.cmd == "install-components":
        from . import components
        from .errors import MediaLabError
        code = 0
        for name in args.names:
            print(f"Installing {name}…", flush=True)
            try:
                components.install(name)
                print(f"  {name}: OK", flush=True)
            except MediaLabError as e:
                print(f"  {name}: {e.message} {e.hint}", file=sys.stderr)
                code = 1
        return code

    with _client() as c:
        if args.cmd == "status":
            print(json.dumps(c.get("/api/jobs").json()["stats"], indent=2))
            return 0
        if args.cmd == "upscale":
            opts = {k: v for k, v in {"model": args.model, "scale": args.scale, "width": args.width,
                                      "height": args.height, "denoise": args.denoise, "sharpen": args.sharpen,
                                      "output_dir": args.output_dir}.items() if v not in (None, "")}
            r = c.post("/api/upscale/jobs", json={"paths": args.paths, "options": opts})
        else:
            info = c.post("/api/analyze", json={"url": args.url}, timeout=120)
            if info.status_code != 200:
                print(info.json().get("message"), file=sys.stderr)
                return 1
            i = info.json()
            opts = {"quality": args.quality, "mode": args.mode, "container": args.container}
            if args.output_dir:
                opts["output_dir"] = args.output_dir
            r = c.post("/api/downloads", json={"items": [{"url": args.url, "title": i.get("title"),
                                                          "platform": i.get("platform"), "provider": i.get("provider"),
                                                          "thumbnail": i.get("thumbnail")}], "options": opts})
        if r.status_code != 200:
            print(r.json().get("message", r.text), file=sys.stderr)
            return 1
        jobs = r.json()["jobs"]
        if not args.wait:
            print("\n".join(j["id"] for j in jobs))
            return 0
        return max((_wait(c, j["id"]) for j in jobs), default=0)


if __name__ == "__main__":
    raise SystemExit(main())
