"""Video upscaling pipeline with segment checkpoints.

 1. analyze (ffprobe): fps, resolution, frames, HDR, audio
 2. split into segments of N frames (bounded by free disk space)
 3. per segment: extract frames → AI (dir mode) → post filters + encode
 4. checkpoint after every segment (state.json) — a stopped render resumes
    at the last finished segment, and even inside a segment already
    upscaled frames are reused
 5. concat segments (stream copy, no re-encode) + original audio/subtitles
    (copied when the container allows it)
 6. verify output (duration, size) and write the final file

Color: frames are converted to RGB with the right matrix (BT.709 for HD
sources even when untagged) and encoded back with BT.709 tags, which avoids
the classic color shift of naive upscalers.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from ..config import paths
from ..errors import Canceled, MediaLabError
from ..logging_setup import get_logger
from ..media import ffmpeg
from ..settings import Settings
from . import filters, interpolate
from .backends.base import BackendParams
from .backends.classic import ClassicBackend
from .engine import (BACKENDS, ai_operations, apply_operations, get_model, native_scales, output_size, resolve_model,
                     resolve_params, wants_upscale, with_oom_retry)
from .planning import describe, plan, segment_frames

log = get_logger("process")

MP4_AUDIO = {"aac", "mp3", "ac3", "eac3", "opus", "flac", "alac"}
ProgressFn = Callable[[dict], None]


def _options_hash(opts: dict, src_info: dict) -> str:
    keys = ("model", "scale", "width", "height", "keep_aspect", "denoise", "sharpen", "detail", "color",
            "temporal", "hdr", "encoder", "quality", "container", "face", "face_model", "face_strength",
            "colorize", "colorize_size", "remove_bg", "bg_mode", "bg_color", "interpolate")
    blob = json.dumps({k: opts.get(k) for k in keys} | {"src": [src_info.get("size"), src_info.get("duration")]},
                      sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


class _State:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                self.data = {}

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1), "utf-8")
        os.replace(tmp, self.path)


def _valid_png(p: Path) -> bool:
    try:
        with Image.open(p) as im:
            im.verify()
        return True
    except Exception:
        return False


def process_video(job_id: str, src: Path, dst: Path, opts: dict, settings: Settings, cancel: threading.Event,
                  progress: ProgressFn, strategy: dict | None = None) -> dict:
    info = ffmpeg.probe(src)
    if info.get("kind") != "video" or not info.get("fps"):
        raise MediaLabError("not_video", "This file has no usable video stream.", "Choose another file.")
    w, h, fps = info["width"], info["height"], info["fps"]
    if info.get("rotation") in (90, -90, 270, -270):
        w, h = h, w  # ffmpeg auto-rotates while decoding
    upscale = wants_upscale(opts)
    out_w, out_h = output_size(w, h, opts) if upscale else (w, h)
    if upscale:
        model, backend = resolve_model(opts.get("model") or settings.ai.default_model)
    else:
        model, backend = get_model("classic-lanczos"), BACKENDS["classic"]
    params = resolve_params(settings, model, strategy)
    steps = plan(w, h, out_w, out_h, native_scales(model, backend, params), max_second_pass_pixels=9_000_000)         if upscale else []
    ai_steps = [s for s in steps if s.kind == "ai"] if not isinstance(backend, ClassicBackend) else []
    ai_w, ai_h = (ai_steps[-1].width, ai_steps[-1].height) if ai_steps else (w, h)
    ops = ai_operations(opts)
    src_rate = Fraction(info.get("r_frame_rate") or f"{fps:.6f}").limit_denominator(1001000)
    interp = opts.get("interpolate") not in (None, "", 0, "0", "1", 1, False, "off")
    out_rate = interpolate.output_fps(src_rate, opts.get("interpolate")) if interp else src_rate
    interp = interp and out_rate > src_rate
    ratio = float(out_rate / src_rate) if interp else 1.0
    alpha_out = bool(opts.get("remove_bg")) and (opts.get("bg_mode") or "transparent") == "transparent"

    encoder_pref = "libx264" if (strategy or {}).get("cpu_encoder") else (opts.get("encoder") or settings.ai.video_encoder)
    encoder = ffmpeg.pick_encoder(encoder_pref)
    quality = int(opts.get("quality") or settings.ai.video_quality)
    container = "webm" if alpha_out else (opts.get("container") or "mp4").lower()
    log.info("Video %s: %dx%d@%.3f → %dx%d@%.3f via %s [%s, tile %s, encoder %s, tools %s]", src.name, w, h, fps,
             out_w, out_h, float(out_rate), describe(steps) or "no upscale", model.name, params.tile or "auto",
             "libvpx-vp9 (alpha)" if alpha_out else encoder, ops + (["rife"] if interp else []), extra={"job_id": job_id})

    if not ai_steps and not ops and not interp:
        return _classic_fast_path(src, dst, info, opts, out_w, out_h, encoder, quality, container, cancel, progress)

    ckdir = paths().checkpoints / job_id
    ckdir.mkdir(parents=True, exist_ok=True)
    state = _State(ckdir / "state.json")
    ohash = _options_hash({**opts, "encoder": encoder, "quality": quality}, info)
    if state.data.get("hash") != ohash:
        if state.data:
            log.info("Settings changed since the last run: restarting render from zero", extra={"job_id": job_id})
        for child in ckdir.iterdir():
            if child.name != "state.json":
                shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
        free_mb = shutil.disk_usage(ckdir).free // (1 << 20)
        per_seg = segment_frames(fps, int(opts.get("segment_seconds") or settings.ai.segment_seconds),
                                 w, h, (ai_w / w), free_mb)
        if interp:  # interpolated frames also take disk space
            per_seg = max(8, int(per_seg / ratio))
        state.data = {"hash": ohash, "src": str(src), "fps": fps, "frames_per_segment": per_seg,
                      "total_frames": info.get("frames"), "done": [], "frames_done": 0, "seconds_per_frame": None}
        state.save()
    else:
        log.info("Resuming render: %d segment(s) already done", len(state.data["done"]), extra={"job_id": job_id})

    per_seg = state.data["frames_per_segment"]
    total_frames = state.data.get("total_frames") or int((info.get("duration") or 0) * fps) or 1
    in_matrix = info.get("color_space") if info.get("color_space") not in (None, "unknown", "reserved") else (
        "bt709" if h >= 720 else "bt601")
    in_matrix = {"bt709": "bt709", "smpte170m": "bt601", "bt470bg": "bt601", "bt2020nc": "bt2020"}.get(in_matrix, "bt709")
    pre = filters.video_pre_chain(opts, bool(info.get("is_hdr")))
    if not info.get("is_hdr") or opts.get("hdr", "tonemap") != "tonemap":
        pre = [c for c in pre if c != "format=rgb24"] + [f"scale=in_color_matrix={in_matrix}:in_range=auto", "format=rgb24"]
    if info.get("vfr"):
        pre.insert(0, f"fps={info.get('r_frame_rate') or fps}")
    pre_resized = bool((ops or interp) and ai_steps and (ai_w, ai_h) != (out_w, out_h))
    post = filters.video_post_chain(opts, out_w, out_h, *((out_w, out_h) if pre_resized else (ai_w, ai_h)))
    # Frames come from RGB PNGs (tagged sRGB); tag the output as BT.709 video explicitly.
    post += ["scale=out_color_matrix=bt709:out_range=tv", "format=yuva420p" if alpha_out else "format=yuv420p",
             "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"]
    fps_str = f"{out_rate.numerator}/{out_rate.denominator}" if interp else (info.get("r_frame_rate") or f"{fps:.6f}")
    venc = (["-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-crf", "28", "-b:v", "0", "-row-mt", "1",
             "-deadline", "good", "-cpu-used", "4", "-auto-alt-ref", "0"] if alpha_out
            else ffmpeg.encoder_args(encoder, quality))
    extract_n = per_seg + 1 if interp else per_seg  # one overlap frame: motion is interpolated across segments

    seg = 0
    started = time.monotonic()
    rate_samples: list[float] = []
    while True:
        if cancel.is_set():
            raise Canceled()
        seg_file = ckdir / f"seg_{seg:05d}.mkv"
        if seg in state.data["done"] and seg_file.exists():
            seg += 1
            continue
        start_frame = seg * per_seg
        if start_frame >= total_frames + per_seg or (info.get("frames_exact") and start_frame >= total_frames):
            break
        sdir = ckdir / f"s{seg:05d}"
        in_dir, out_dir = sdir / "in", sdir / "out"
        seg_t0 = time.monotonic()
        frames_done_before = state.data["frames_done"]

        def report(stage: str, label: str, frac_in_seg: float, n_in_seg: int) -> None:
            done = frames_done_before + n_in_seg * frac_in_seg
            elapsed = time.monotonic() - started
            spf = state.data.get("seconds_per_frame")
            remaining = max(0, total_frames - done)
            eta = remaining * spf if spf else None
            progress({"stage": "processing", "label": label, "substage": stage,
                      "percent": min(99.5, done / total_frames * 100), "frames_done": int(done),
                      "frames_total": total_frames, "segment": seg + 1,
                      "segments_total": max(seg + 1, -(-total_frames // per_seg)),
                      "fps_processing": (sum(rate_samples[-5:]) / len(rate_samples[-5:])) if rate_samples else None,
                      "eta": eta, "elapsed": elapsed, "tile": params.tile})

        # ---- 1. extract
        marker = sdir / "extracted"
        if not marker.exists():
            shutil.rmtree(sdir, ignore_errors=True)
            in_dir.mkdir(parents=True)
            ss = max(0.0, (start_frame - 0.25) / fps)
            report("extract", "Extracting frames", 0, per_seg)
            ffmpeg.run(["-ss", f"{ss:.6f}", "-i", str(src), "-frames:v", str(extract_n), "-an", "-sn",
                        "-vf", ",".join(pre), "-fps_mode", "passthrough", "-start_number", "1",
                        str(in_dir / "%08d.png")], cancel=cancel, label="extract frames")
            marker.write_text("ok")
        n = len(list(in_dir.glob("*.png")))
        if n == 0:
            shutil.rmtree(sdir, ignore_errors=True)
            break  # end of stream
        n_real = min(n, per_seg)  # frames that belong to this segment (the rest is the overlap frame)
        if not ai_steps:
            out_dir = in_dir

        # ---- 2. AI steps (dir → dir), reusing frames already upscaled
        cur_in = in_dir
        for k, step in enumerate(ai_steps):
            step_out = out_dir if k == len(ai_steps) - 1 else sdir / f"mid{k}"
            step_out.mkdir(parents=True, exist_ok=True)
            existing = sorted(step_out.glob("*.png"), key=lambda p: p.stat().st_mtime)
            for p in existing[-3:]:
                if not _valid_png(p):
                    p.unlink(missing_ok=True)
            todo_dir = sdir / f"todo{k}"
            shutil.rmtree(todo_dir, ignore_errors=True)
            todo_dir.mkdir()
            pending = [f for f in sorted(cur_in.glob("*.png")) if not (step_out / f.name).exists()]
            for f in pending:
                os.link(f, todo_dir / f.name) if _same_drive(f, todo_dir) else shutil.copy2(f, todo_dir / f.name)
            label = f"Upscaling ×{step.scale:g} · {model.name}"
            if pending:
                already = n - len(pending)
                t_ai = time.monotonic()

                def on_p(frac: float, already=already, pend=len(pending), k=k) -> None:
                    step_frac = (already + frac * pend) / n
                    report("upscale", label, 0.1 + 0.8 * (k + step_frac) / len(ai_steps), n)

                def run(p: BackendParams, todo_dir=todo_dir, step_out=step_out, step=step, on_p=on_p) -> None:
                    backend.upscale_dir(model, todo_dir, step_out, int(step.scale), p, cancel, on_p)

                params = with_oom_retry(run, params, f"segment {seg + 1}")
                rate_samples.append(len(pending) / max(0.001, time.monotonic() - t_ai))
            shutil.rmtree(todo_dir, ignore_errors=True)
            if k > 0:
                shutil.rmtree(cur_in, ignore_errors=True)
            cur_in = step_out

        # ---- 2a. bring frames to the final size first when more work follows (faster, exact size)
        if (ops or interp) and ai_steps and (ai_w, ai_h) != (out_w, out_h) and not (sdir / "resized").exists():
            _resize_frames(out_dir, out_w, out_h, cancel)
            (sdir / "resized").write_text("ok")
        # ---- 2b. AI tools on every frame (faces, colors, background)
        if ops and not (sdir / "ops_done").exists():
            _frame_operations(out_dir, sdir / "ops", opts, cancel,
                              lambda f: report("tools", "Faces / colors / background", 0.5 + 0.3 * f, n))
            (sdir / "ops_done").write_text("ok")
        # ---- 2c. frame interpolation (RIFE)
        frames_dir = out_dir
        if interp:
            rdir = sdir / "rife"
            # Output frames owned by this segment, counted from the start of the video so rounding
            # never accumulates (24 -> 60 fps gives exactly 2.5x the frames over the whole file).
            keep = round((start_frame + n_real) * ratio) - round(start_frame * ratio)
            if not (sdir / "rife_done").exists():
                shutil.rmtree(rdir, ignore_errors=True)
                gpu = BACKENDS["ncnn"].resolve_gpu(settings.ai.gpu_id)
                interpolate.interpolate_dir(out_dir, rdir, max(keep, round(n * ratio)), gpu, cancel,
                                            lambda f: report("interpolate", "Interpolating frames (RIFE)", 0.8 + 0.1 * f, n))
                for extra in sorted(rdir.glob("*.png"))[keep:]:
                    extra.unlink()  # frames after the overlap belong to the next segment
                (sdir / "rife_done").write_text("ok")
            frames_dir = rdir

        # ---- 3. encode segment
        report("encode", "Encoding segment", 0.92, n)
        tmp_seg = seg_file.with_suffix(".part.mkv")
        ffmpeg.run(["-framerate", fps_str, "-start_number", "1", "-i", str(frames_dir / "%08d.png"),
                    "-vf", ",".join(post), *venc,
                    "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv",
                    "-an", str(tmp_seg)], cancel=cancel, label=f"encode segment {seg + 1}")
        os.replace(tmp_seg, seg_file)
        shutil.rmtree(sdir, ignore_errors=True)
        state.data["done"].append(seg)
        state.data["frames_done"] = frames_done_before + n_real
        spf = (time.monotonic() - seg_t0) / max(1, n_real)
        prev = state.data.get("seconds_per_frame")
        state.data["seconds_per_frame"] = spf if prev is None else prev * 0.6 + spf * 0.4
        state.save()
        log.info("Segment %d done (%d frames, %.2fs/frame)", seg + 1, n, spf, extra={"job_id": job_id})
        if n_real < per_seg:
            break  # last (short) segment
        seg += 1

    # ---- 4. concat + audio
    progress({"stage": "processing", "label": "Merging segments and audio", "substage": "mux", "percent": 99.6})
    seg_files = sorted(ckdir.glob("seg_*.mkv"))
    if not seg_files:
        raise MediaLabError("no_frames", "No frames could be read from this video.", "The file may be damaged.")
    listing = ckdir / "concat.txt"
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in seg_files), "utf-8")
    _mux(listing, src, dst, info, container, opts, cancel)
    out_info = ffmpeg.probe(dst)
    warnings = []
    if info.get("duration") and out_info.get("duration") and abs(out_info["duration"] - info["duration"]) > 1.0:
        warnings.append(f"Output duration {out_info['duration']:.1f}s differs from source {info['duration']:.1f}s")
    if out_info.get("width") != out_w or out_info.get("height") != out_h:
        warnings.append(f"Output is {out_info.get('width')}x{out_info.get('height')} instead of {out_w}x{out_h}")
    for wmsg in warnings:
        log.warning(wmsg, extra={"job_id": job_id})
    shutil.rmtree(ckdir, ignore_errors=True)
    return {"width": out_w, "height": out_h, "fps": float(out_rate), "source_fps": fps,
            "duration": out_info.get("duration"), "plan": describe(steps) or " + ".join(ops + (["rife"] if interp else [])),
            "encoder": "libvpx-vp9" if alpha_out else encoder, "verified": not warnings, "warnings": warnings,
            "source_width": w, "source_height": h, "tile": params.tile, "operations": ops, "interpolated": interp}


def _resize_frames(frames: Path, w: int, h: int, cancel: threading.Event) -> None:
    from concurrent.futures import ThreadPoolExecutor

    def one(f: Path) -> None:
        if cancel.is_set():
            return
        with Image.open(f) as im:
            if im.size == (w, h):
                return
            im.load()
            small = im.resize((w, h), Image.Resampling.LANCZOS)
        tmp = f.with_suffix(".partial")
        small.save(tmp, "PNG", compress_level=1)
        os.replace(tmp, f)

    with ThreadPoolExecutor(max_workers=max(2, min(8, (os.cpu_count() or 4) // 2))) as pool:
        list(pool.map(one, sorted(frames.glob("*.png"))))
    if cancel.is_set():
        raise Canceled()


def _frame_operations(frames: Path, work: Path, opts: dict, cancel: threading.Event, on_progress) -> None:
    """Faces / colorize / background on every PNG of ``frames`` (in place, resumable per frame)."""
    import numpy as np
    work.mkdir(parents=True, exist_ok=True)
    files = sorted(frames.glob("*.png"))
    video_opts = {**opts, "colorize_size": opts.get("colorize_size") or 384}
    if not opts.get("colorize"):
        # No frame-to-frame state: overlap disk/CPU work of several frames (inference stays serialized).
        from concurrent.futures import ThreadPoolExecutor
        counter = [0]
        lock = threading.Lock()

        def one(f: Path) -> None:
            if cancel.is_set():
                return
            done = work / f.name
            if not done.exists():
                with Image.open(f) as im:
                    im.load()
                    out = apply_operations(im, video_opts)
                tmp = work / (f.stem + ".partial")
                out.save(tmp, "PNG", compress_level=1)
                tmp.replace(done)
            with lock:
                counter[0] += 1
                on_progress(counter[0] / len(files))

        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(one, files))
        if cancel.is_set():
            raise Canceled()
        for f in files:
            os.replace(work / f.name, f)
        shutil.rmtree(work, ignore_errors=True)
        return
    prev_chroma = None
    prev_luma = None
    for i, f in enumerate(files):
        if cancel.is_set():
            raise Canceled()
        done = work / f.name
        if not done.exists():
            with Image.open(f) as im:
                im.load()
                out = apply_operations(im, video_opts)
            if opts.get("colorize") and out.mode in ("RGB", "RGBA"):
                # Temporal smoothing of the predicted colors: less flicker, reset on scene cuts.
                ycc = out.convert("RGB").convert("YCbCr")
                y, cb, cr = (np.asarray(c, dtype=np.float32) for c in ycc.split())
                if prev_chroma is not None and prev_luma is not None and prev_luma.shape == y.shape \
                        and float(np.abs(y - prev_luma).mean()) < 14:
                    cb = cb * 0.55 + prev_chroma[0] * 0.45
                    cr = cr * 0.55 + prev_chroma[1] * 0.45
                prev_chroma, prev_luma = (cb, cr), y
                merged = Image.merge("YCbCr", [Image.fromarray(np.clip(c, 0, 255).astype(np.uint8)) for c in (y, cb, cr)])
                alpha = out.getchannel("A") if out.mode == "RGBA" else None
                out = merged.convert("RGB")
                if alpha is not None:
                    out.putalpha(alpha)
            tmp = work / (f.stem + ".partial")
            out.save(tmp, "PNG", compress_level=1)
            tmp.replace(done)
        on_progress((i + 1) / len(files))
    for f in files:
        os.replace(work / f.name, f)
    shutil.rmtree(work, ignore_errors=True)


def _same_drive(a: Path, b: Path) -> bool:
    return a.resolve().anchor.lower() == b.resolve().anchor.lower()


def _mux(listing: Path, src: Path, dst: Path, info: dict, container: str, opts: dict,
         cancel: threading.Event) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".muxing" + dst.suffix)
    args = []
    offset = info.get("video_start_offset") or 0
    if offset > 0.001:
        args += ["-itsoffset", f"{offset:.6f}"]
    args += ["-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(src), "-map", "0:v:0"]
    keep_audio = opts.get("keep_audio", True) and info.get("has_audio")
    if keep_audio:
        args += ["-map", "1:a?"]
        codecs = {a.get("codec") for a in info.get("audio", [])}
        if container == "mp4" and not codecs <= MP4_AUDIO:
            args += ["-c:a", "aac", "-b:a", "256k"]
        elif container == "webm" and not codecs <= {"opus", "vorbis"}:
            args += ["-c:a", "libopus", "-b:a", "192k"]
        else:
            args += ["-c:a", "copy"]
    if container == "mkv":
        args += ["-map", "1:s?", "-c:s", "copy"]
    args += ["-c:v", "copy", "-map_metadata", "1", "-map_chapters", "1"]
    if container == "mp4":
        args += ["-movflags", "+faststart"]
    args.append(str(tmp))
    ffmpeg.run(args, cancel=cancel, label="mux")
    os.replace(tmp, dst)


def _classic_fast_path(src: Path, dst: Path, info: dict, opts: dict, out_w: int, out_h: int, encoder: str,
                       quality: int, container: str, cancel: threading.Event, progress: ProgressFn) -> dict:
    """Non-AI resize in a single FFmpeg pass (no frame files)."""
    chain = [c for c in filters.video_pre_chain(opts, bool(info.get("is_hdr"))) if c != "format=rgb24"]
    chain += filters.video_post_chain(opts, out_w, out_h, info["width"], info["height"])
    chain += ["scale=out_color_matrix=bt709:out_range=tv", "format=yuv420p",
              "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"]
    tmp = dst.with_name(dst.stem + ".rendering" + dst.suffix)
    args = ["-i", str(src), "-map", "0:v:0", "-vf", ",".join(chain), *ffmpeg.encoder_args(encoder, quality),
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    if opts.get("keep_audio", True) and info.get("has_audio"):
        args += ["-map", "0:a?", "-c:a", "copy" if container != "webm" else "libopus"]
    if container == "mp4":
        args += ["-movflags", "+faststart"]
    args.append(str(tmp))
    ffmpeg.run(args, duration=info.get("duration"), cancel=cancel, label="classic upscale",
               on_progress=lambda f, s: progress({"stage": "processing", "label": "Resizing (Lanczos)",
                                                  "percent": f * 100, "speed_x": s.get("speed")}))
    os.replace(tmp, dst)
    return {"width": out_w, "height": out_h, "fps": info.get("fps"), "duration": info.get("duration"),
            "plan": f"Lanczos to {out_w}×{out_h}", "encoder": encoder, "verified": True, "warnings": []}
