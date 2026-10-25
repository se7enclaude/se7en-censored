"""Audio tools: separate the voice from the music, and clean up a recording.

* separation: MDX-Net vocal model (UVR "Kim Vocal 2", ONNX, on the GPU through DirectML). The voice is
  predicted from the spectrogram and the instrumental is the mix minus the voice, so voice + instrumental
  always add up to the original;
* cleaning: voices use RNNoise (FFmpeg ``arnndn``) or, at the strongest setting, the separation model then
  RNNoise (keeps only the voice, removing noise, music and hum); other sounds use FFmpeg's FFT denoiser. Hum
  (50/60 Hz) and click removal and loudness levelling are optional.

The sound is streamed through FFmpeg pipes chunk by chunk, so hour-long files use little memory. For a
video, the picture is copied untouched and only the sound is replaced. Outputs never overwrite anything and
are written under a temporary name first (a canceled job leaves no half file).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

from .. import ai_assets
from ..errors import Canceled, MediaLabError
from ..logging_setup import get_logger
from ..media import ffmpeg
from ..upscale import onnx_rt

log = get_logger("process")
Progress = Callable[[float, str, float | None], None]  # fraction, label, eta seconds

SEPARATION_MODEL = "kim_vocal_2"
DENOISE_MODEL = "rnnoise_lq"
SR = 44100
HOP = 1024
N_FFT = 6144  # measured on a known mix: 6144 gives +16.6 dB on the voice, 7680 only +10.7 dB
DIM_F = 3072
DIM_T = 256
CHUNK = HOP * (DIM_T - 1)
TRIM = N_FFT // 2
GEN = CHUNK - 2 * TRIM  # new samples produced per model run

AUDIO_FORMATS = {  # output format -> (extension, codec arguments)
    "mp3": ("mp3", ["-c:a", "libmp3lame", "-b:a", "320k"]),
    "m4a": ("m4a", ["-c:a", "aac", "-b:a", "256k"]),
    "flac": ("flac", ["-c:a", "flac"]),
    "wav": ("wav", ["-c:a", "pcm_s16le"]),
    "opus": ("opus", ["-c:a", "libopus", "-b:a", "192k"]),
}
_SAME_AS_SOURCE = {"mp3": "mp3", "m4a": "m4a", "aac": "m4a", "flac": "flac", "wav": "wav", "opus": "opus", "ogg": "opus"}
_VIDEO_AUDIO = {"mp4": ["-c:a", "aac", "-b:a", "256k"], "m4v": ["-c:a", "aac", "-b:a", "256k"],
                "mov": ["-c:a", "aac", "-b:a", "256k"], "mkv": ["-c:a", "aac", "-b:a", "256k"],
                "webm": ["-c:a", "libopus", "-b:a", "192k"], "avi": ["-c:a", "libmp3lame", "-b:a", "320k"]}
LABELS = {"vocals": ("voix", "vocals"), "instrumental": ("instrumental", "instrumental"), "cleaned": ("nettoyé", "cleaned")}


# ================================================================ spectrogram (matches torch.stft / istft)
_WIN = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)).astype(np.float32)  # periodic Hann
_IDX = np.arange(DIM_T)[:, None] * HOP + np.arange(N_FFT)[None, :]
_WSUM = np.zeros(N_FFT + HOP * (DIM_T - 1), np.float32)
for _t in range(DIM_T):
    _WSUM[_t * HOP:_t * HOP + N_FFT] += _WIN ** 2
_WSUM = np.maximum(_WSUM, 1e-8)


def _stft(x: np.ndarray) -> np.ndarray:
    """[2, CHUNK] -> model input [1, 4, DIM_F, DIM_T] (real/imag of each channel)."""
    xp = np.pad(x, ((0, 0), (TRIM, TRIM)), mode="reflect")
    spec = np.fft.rfft(xp[:, _IDX] * _WIN, axis=-1).transpose(0, 2, 1)[:, :DIM_F]  # [2, DIM_F, DIM_T]
    return np.stack([spec.real, spec.imag], 1).reshape(1, 4, DIM_F, DIM_T).astype(np.float32)


def _istft(pred: np.ndarray) -> np.ndarray:
    """model output [1, 4, DIM_F, DIM_T] -> [2, CHUNK]."""
    p = pred.reshape(2, 2, DIM_F, DIM_T)
    full = np.zeros((2, N_FFT // 2 + 1, DIM_T), np.complex64)
    full[:, :DIM_F] = p[:, 0] + 1j * p[:, 1]
    frames = np.fft.irfft(full.transpose(0, 2, 1), n=N_FFT, axis=-1).astype(np.float32) * _WIN  # [2, T, N_FFT]
    out = np.zeros((2, _WSUM.size), np.float32)
    for t in range(DIM_T):
        out[:, t * HOP:t * HOP + N_FFT] += frames[:, t]
    return (out / _WSUM)[:, TRIM:TRIM + CHUNK]


class Separator:
    """Runs the vocal model on [2, CHUNK] windows; falls back to the CPU if the GPU fails."""

    def __init__(self, model: Path, quality: str = "normal") -> None:
        self.model = model
        self.device = "auto"
        self.twice = quality == "max"  # second pass on the inverted mix, averaged: fewer artifacts

    def _run(self, spec: np.ndarray) -> np.ndarray:
        try:
            return onnx_rt.run(self.model, {"input": spec}, self.device)[0]
        except MediaLabError as e:
            if self.device == "cpu" or not onnx_rt.uses_gpu(self.device):
                raise
            log.warning("Voice separation on the GPU failed (%s); continuing on the CPU", e.code)
            self.device = "cpu"
            return onnx_rt.run(self.model, {"input": spec}, self.device)[0]

    def vocals(self, window: np.ndarray) -> np.ndarray:
        spec = _stft(window)
        pred = self._run(spec)
        if self.twice:
            pred = 0.5 * pred - 0.5 * self._run(-spec)
        return _istft(pred)


# ================================================================ helpers
def _unique(p: Path) -> Path:
    n, cand = 1, p
    while cand.exists():
        cand = p.with_name(f"{p.stem} ({n}){p.suffix}")
        n += 1
    return cand


def _part(dst: Path) -> Path:
    return dst.with_name(f"{dst.stem}.se7en-part{dst.suffix}")


def _plan_output(src: Path, info: dict, opts: dict, name: str, french: bool) -> tuple[Path, list[str], bool]:
    """Destination (named after ``name``: vocals | instrumental | cleaned), audio codec args, and whether the
    picture is kept (video source + keep_video)."""
    folder = Path(opts.get("output_dir") or src.parent).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    label = LABELS[name][0 if french else 1]
    keep_video = bool(info.get("has_video")) and info.get("kind") == "video" and opts.get("keep_video", True)
    if keep_video:
        ext = src.suffix.lower().lstrip(".")
        if ext not in _VIDEO_AUDIO:
            ext = "mp4"
        return _unique(folder / f"{src.stem} ({label}).{ext}"), _VIDEO_AUDIO[ext], True
    fmt = opts.get("format") or "auto"
    if fmt == "auto":
        fmt = _SAME_AS_SOURCE.get(src.suffix.lower().lstrip("."), "mp3")
    ext, args = AUDIO_FORMATS.get(fmt, AUDIO_FORMATS["mp3"])
    return _unique(folder / f"{src.stem} ({label}).{ext}"), args, False


def _rate(codec: list[str], source_rate: int) -> list[str]:
    """Explicit output sample rate: RNNoise works at 48 kHz and loudnorm outputs 192 kHz otherwise."""
    if "libopus" in codec:
        return ["-ar", "48000"]  # the only full-band rate Opus accepts
    return ["-ar", str(min(max(int(source_rate or SR), 8000), 96000))]


def _filters(opts: dict, *, denoise: str | None) -> list[str]:
    """Optional repairs, in order: hum, rumble, clicks, denoise, loudness."""
    chain: list[str] = []
    hum = int(opts.get("hum") or 0)
    if hum in (50, 60):
        chain += [f"bandreject=f={hum * k}:width_type=h:w=4" for k in (1, 2, 3)]
    if denoise in ("voice-light", "voice-medium") or hum:
        chain.append("highpass=f=70")
    if opts.get("declick"):
        chain.append("adeclick")
    if denoise == "voice-light":
        chain.append("arnndn=m=lq.rnnn:mix=0.75")
    elif denoise == "voice-medium":
        chain.append("arnndn=m=lq.rnnn")
    elif denoise and denoise.startswith("other-"):
        nr = {"other-light": 8, "other-medium": 14, "other-strong": 22}[denoise]
        chain.append(f"afftdn=nr={nr}:nf=-50:tn=1")
    if opts.get("normalize"):
        chain.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    return chain


class _Encoder:
    """FFmpeg process fed with float32 stereo PCM on stdin (plus the source picture when keeping the video)."""

    def __init__(self, dst: Path, codec: list[str], src: Path | None, afilter: list[str], cwd: str | None) -> None:
        self.dst = dst
        self.part = _part(dst)
        self.err = tempfile.TemporaryFile()
        args = [ffmpeg.require("ffmpeg"), "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
                "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "pipe:0"]
        if src is not None:
            args += ["-i", str(src), "-map", "1:v:0", "-map", "0:a:0", "-c:v", "copy", "-map_metadata", "1", "-shortest"]
        if afilter:
            args += ["-af", ",".join(afilter)]
        args += [*codec, *_rate(codec, SR), str(self.part)]
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=self.err, cwd=cwd,
                                     creationflags=ffmpeg.CREATE_NO_WINDOW)

    def write(self, pcm: np.ndarray) -> None:
        try:
            self.proc.stdin.write(np.ascontiguousarray(pcm.T, dtype="<f4").tobytes())
        except (BrokenPipeError, OSError):
            self.proc.wait()
            raise self._failure()

    def _failure(self) -> MediaLabError:
        self.err.seek(0)
        detail = self.err.read().decode("utf-8", "replace")[-1500:]
        log.error("Audio encoder failed: %s", detail)
        return MediaLabError("ffmpeg_failed", "Writing the audio file failed.", "Open Logs for the FFmpeg output.",
                             True, detail)

    def finish(self) -> Path:
        self.proc.stdin.close()
        if self.proc.wait() != 0:
            raise self._failure()
        os.replace(self.part, self.dst)
        return self.dst

    def abort(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.part.unlink(missing_ok=True)


def _decoder(src: Path) -> subprocess.Popen:
    return subprocess.Popen([ffmpeg.require("ffmpeg"), "-hide_banner", "-nostdin", "-loglevel", "error",
                             "-i", str(src), "-map", "0:a:0", "-vn", "-f", "f32le", "-ac", "2", "-ar", str(SR), "pipe:1"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=ffmpeg.CREATE_NO_WINDOW)


def _read(proc: subprocess.Popen, samples: int) -> np.ndarray:
    data = proc.stdout.read(samples * 8)
    usable = len(data) - len(data) % 8
    return np.frombuffer(data[:usable], dtype="<f4").reshape(-1, 2).T


def _probe_audio(src: Path) -> dict:
    if not src.is_file():
        raise MediaLabError("source_missing", "The source file no longer exists.", "It may have been moved or deleted.")
    info = ffmpeg.probe(src)
    if not info.get("has_audio"):
        raise MediaLabError("no_audio_stream", "This file has no sound.", "Choose a video or audio file with sound.")
    return info


def _model_progress(progress: Progress, french: bool) -> Callable[[int, int], None]:
    label = "Téléchargement du modèle IA" if french else "Downloading the AI model"
    return lambda done, total: progress(0.0, f"{label} · {done * 100 // max(total, 1)} %", None)


# ================================================================ separation (also the strong voice cleaner)
def _separate_stream(src: Path, info: dict, stems: list[str], opts: dict, cancel: threading.Event,
                     progress: Progress, french: bool, label: str, afilter: list[str] | None = None,
                     names: dict[str, str] | None = None, cwd: str | None = None) -> list[Path]:
    model = ai_assets.ensure(SEPARATION_MODEL, _model_progress(progress, french))
    sep = Separator(model, opts.get("quality", "normal"))
    encoders: dict[str, _Encoder] = {}
    for stem in stems:
        dst, codec, keep_video = _plan_output(src, info, opts, (names or {}).get(stem, stem), french)
        encoders[stem] = _Encoder(dst, codec, src if keep_video else None, afilter or [], cwd)
    dec = _decoder(src)
    total = max(1, int((info.get("duration") or 0) * SR))
    done, t0 = 0, time.monotonic()
    progress(0.0, label, None)
    try:
        pending = np.zeros((2, TRIM), np.float32)  # left context: silence before the start
        eof = False
        while True:
            while not eof and pending.shape[1] < CHUNK:
                block = _read(dec, CHUNK - pending.shape[1])
                if block.shape[1] == 0:
                    eof = True
                else:
                    pending = np.concatenate([pending, block], 1)
            valid = min(GEN, pending.shape[1] - TRIM)
            if valid <= 0:
                break
            window = pending[:, :CHUNK]
            if window.shape[1] < CHUNK:
                window = np.pad(window, ((0, 0), (0, CHUNK - window.shape[1])))
            if cancel.is_set():
                raise Canceled()
            voice = sep.vocals(window)[:, TRIM:TRIM + valid]
            mix = window[:, TRIM:TRIM + valid]
            if "vocals" in encoders:
                encoders["vocals"].write(voice)
            if "instrumental" in encoders:
                encoders["instrumental"].write(mix - voice)
            pending = pending[:, GEN:]
            done += valid
            frac = min(0.99, done / total)
            el = time.monotonic() - t0
            progress(frac, label, el / frac - el if frac > 0.02 else None)
        dec.wait()
        if done == 0:
            raise MediaLabError("no_audio_stream", "This file has no sound.", "Choose a video or audio file with sound.")
        return [e.finish() for e in encoders.values()]
    except BaseException:
        for e in encoders.values():
            e.abort()
        raise
    finally:
        if dec.poll() is None:
            dec.kill()
            dec.wait()


def separate(src: Path, opts: dict, cancel: threading.Event, progress: Progress, french: bool = False) -> dict:
    src = Path(src).resolve()  # FFmpeg may run from the models folder
    info = _probe_audio(src)
    keep = opts.get("stems") or "both"
    stems = {"both": ["vocals", "instrumental"], "vocals": ["vocals"], "instrumental": ["instrumental"]}.get(keep)
    if not stems:
        raise MediaLabError("bad_option", f"Unknown stems option “{keep}”.")
    t0 = time.perf_counter()
    label = "Séparation de la voix" if french else "Separating the voice"
    files = _separate_stream(src, info, stems, opts, cancel, progress, french, label)
    return _result(src, info, files, "separate", time.perf_counter() - t0)


# ================================================================ cleaning
def clean(src: Path, opts: dict, cancel: threading.Event, progress: Progress, french: bool = False) -> dict:
    src = Path(src).resolve()  # FFmpeg may run from the models folder
    info = _probe_audio(src)
    kind = opts.get("sound") or "voice"
    level = opts.get("strength") or "medium"
    t0 = time.perf_counter()
    label = "Nettoyage du son" if french else "Cleaning the sound"
    if kind == "voice" and level == "strong":
        # the separation model keeps only the voice (removes noise, music and hum at once), then RNNoise
        # silences what is left between words: -58 dB of residual noise instead of -51 dB on the test mix
        rnn = ai_assets.ensure(DENOISE_MODEL, _model_progress(progress, french))
        files = _separate_stream(src, info, ["vocals"], {**opts, "quality": "max"}, cancel, progress, french, label,
                                 ["arnndn=m=lq.rnnn", *_filters(opts, denoise=None)], names={"vocals": "cleaned"},
                                 cwd=str(rnn.parent))
        return _result(src, info, files, "clean", time.perf_counter() - t0)
    denoise = f"{'voice' if kind == 'voice' else 'other'}-{level}"
    if denoise == "voice-strong":
        denoise = "voice-medium"
    cwd = None
    if denoise.startswith("voice-"):
        cwd = str(ai_assets.ensure(DENOISE_MODEL, _model_progress(progress, french)).parent)
    dst, codec, keep_video = _plan_output(src, info, opts, "cleaned", french)
    part = _part(dst)
    args = ["-i", str(src)]
    args += ["-map", "0:v:0", "-map", "0:a:0", "-c:v", "copy"] if keep_video else ["-map", "0:a:0", "-vn"]
    source_rate = int((info.get("audio") or [{}])[0].get("sample_rate") or SR)
    args += ["-af", ",".join(_filters(opts, denoise=denoise)), *codec, *_rate(codec, source_rate), str(part)]

    def on_progress(frac: float, state: dict) -> None:
        el = time.perf_counter() - t0
        progress(frac, label, el / frac - el if frac > 0.02 else None)

    progress(0.0, label, None)
    try:
        ffmpeg.run(args, duration=info.get("duration") or None, cancel=cancel, label="audio clean",
                   on_progress=on_progress, cwd=cwd)
        os.replace(part, dst)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return _result(src, info, [dst], "clean", time.perf_counter() - t0)


def _result(src: Path, info: dict, files: list[Path], op: str, elapsed: float) -> dict:
    main = files[0]
    log.info("Audio %s: %s -> %s in %.1fs", op, src.name, ", ".join(f.name for f in files), elapsed)
    return {"file_path": str(main), "size": sum(f.stat().st_size for f in files), "title": main.name,
            "format": main.suffix.lstrip(".").upper(), "duration": info.get("duration"),
            "meta": {"op": op, "source": str(src), "files": [str(f) for f in files], "elapsed": round(elapsed, 2)}}
