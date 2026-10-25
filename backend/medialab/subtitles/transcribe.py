"""Speech recognition for subtitles: whisper.cpp (Whisper large-v3-turbo) + Silero voice activity detection.

* Whisper decodes the whole sound track in one pass (30 s windows, each conditioned on the previous text), with
  beam search and temperature fallback: the most accurate text, punctuation included;
* a short, well-punctuated prompt in the spoken language keeps Whisper in its "punctuated" mode (without it,
  it sometimes switches to lowercase text without punctuation - measured on real recordings);
* word times come from DTW on the model's cross-attention (``-dtw``), which needs flash attention off;
* Silero VAD then finds where people really speak: words heard outside speech are dropped (Whisper's invented
  text on music / silence) and subtitle edges are snapped onto the start / end of the voice.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .. import ai_assets, components
from ..errors import Canceled, MediaLabError
from ..logging_setup import get_logger
from ..media import ffmpeg

log = get_logger("process")
SR = 16000
Progress = Callable[[float, str], None]

# Credits Whisper is known to invent on silence / music (from its training subtitles)
HALLUCINATIONS = re.compile(
    r"(sous-titr(es|age) (réalisés?|par)|amara\.org|merci d'avoir regardé|abonnez-vous|thanks? for watching|"
    r"subtitles by|transcribed by|ⓒ|www\.|\.com$)", re.I)


@dataclass
class Word:
    text: str  # as displayed, with its punctuation (no leading space)
    start: float
    end: float
    p: float  # confidence 0..1

    def to_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in asdict(self).items()}


def threads() -> int:
    return max(2, min(32, (os.cpu_count() or 4) - 1))


# ------------------------------------------------------------------ audio
def extract_audio(src: Path, dst: Path, duration: float | None, cancel: threading.Event,
                  progress: Callable[[float], None] | None = None) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_suffix(".part.wav")
    ffmpeg.run(["-i", str(src), "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(SR), "-c:a", "pcm_s16le", str(part)],
               duration=duration, cancel=cancel, label="subtitle audio",
               on_progress=(lambda f, _s: progress(f)) if progress else None)
    os.replace(part, dst)
    return dst


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")


def write_wav(path: Path, pcm: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.ascontiguousarray(pcm, dtype="<i2").tobytes())


# ------------------------------------------------------------------ engine files
def engine_paths(progress: Callable[[int, int], None] | None = None) -> tuple[str, str, Path, Path]:
    """whisper-cli, VAD tool, Whisper model, VAD model (installing what is missing)."""
    if not components.ensure("whisper"):
        raise MediaLabError("component_missing", "The speech recognition engine could not be installed.",
                            "Check your internet connection and retry.", True, extra={"component": "whisper"})
    vad = ai_assets.ensure("silero_vad")
    model = ai_assets.ensure("whisper_turbo", progress)
    return components.require("whisper-cli"), components.require("whisper-vad-speech-segments"), model, vad


def _run(cmd: list[str], cancel: threading.Event | None, cwd: str | None = None,
         on_line: Callable[[str], None] | None = None, label: str = "whisper") -> str:
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=cwd,
                            creationflags=components.CREATE_NO_WINDOW, text=True, encoding="utf-8", errors="replace")
    out: list[str] = []
    try:
        assert proc.stdout
        for line in proc.stdout:
            out.append(line)
            if on_line:
                on_line(line)
            if cancel is not None and cancel.is_set():
                proc.kill()
                raise Canceled()
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    text = "".join(out)
    if proc.returncode != 0:
        log.error("%s failed (code %s): %s", label, proc.returncode, text[-1500:])
        low = text.lower()
        if "failed to load model" in low or "invalid model" in low:
            raise MediaLabError("model_load_failed", "The speech model could not be loaded.",
                                "It may be damaged: delete it in Settings › Components and retry.", True, text[-1500:])
        raise MediaLabError("transcription_failed", "Speech recognition failed.", "Open Logs for details.", True,
                            text[-1500:])
    return text


# ------------------------------------------------------------------ voice activity
def speech_segments(wav: Path, vad_tool: str, vad_model: Path, cancel: threading.Event | None = None) -> list[tuple[float, float]]:
    """Speech regions in seconds. Long monologues are split by the VAD itself (≤ 25 s)."""
    out = _run([vad_tool, "-f", str(wav), "-vm", str(vad_model), "-t", str(min(8, threads())),
                "-vt", "0.5", "-vspd", "200", "-vsd", "150", "-vmsd", "25", "-vp", "40"], cancel, label="voice detection")
    segs = [(float(a) / 100, float(b) / 100)
            for a, b in re.findall(r"Speech segment \d+: start = ([\d.]+), end = ([\d.]+)", out)]  # centiseconds
    return sorted(segs)


# ------------------------------------------------------------------ language
LANG_RE = re.compile(r"auto-detected language: (\w+) \(p = ([\d.]+)\)")


def detect_language(pcm: np.ndarray, speech: list[tuple[float, float]], cli: str, model: Path,
                    cancel: threading.Event | None = None) -> dict:
    """Whisper's language identification on up to 30 s of actual speech (not intro music or silence)."""
    parts, total = [], 0
    for a, b in speech:
        seg = pcm[int(a * SR):int(b * SR)]
        take = seg[:max(0, 30 * SR - total)]
        parts.append(take)
        total += len(take)
        if total >= 30 * SR:
            break
    sample = np.concatenate(parts) if parts else pcm[:30 * SR]
    if len(sample) < SR // 2:
        raise MediaLabError("no_speech", "No speech was found in this video.",
                            "Check that the video has a voice track.")
    with tempfile.TemporaryDirectory(prefix="se7en-lang-") as tmp:
        f = Path(tmp) / "sample.wav"
        write_wav(f, sample)
        out = _run([cli, "-m", str(model), "-f", str(f), "-l", "auto", "-dl", "-t", str(threads())], cancel,
                   label="language detection")
    m = LANG_RE.search(out)
    if not m:
        raise MediaLabError("language_detection_failed", "The language could not be detected.",
                            "Choose the language manually.", True, out[-800:])
    return {"code": m.group(1), "p": float(m.group(2))}


# ------------------------------------------------------------------ transcription


# A punctuated opening line in the spoken language (Whisper's "initial prompt")
PROMPTS = {
    "fr": "Bonjour à tous. Voici la transcription, avec une ponctuation soignée.",
    "en": "Hello everyone. Here is the transcript, with careful punctuation.",
    "es": "Hola a todos. Esta es la transcripción, con una puntuación cuidada.",
    "it": "Ciao a tutti. Ecco la trascrizione, con una punteggiatura accurata.",
    "de": "Hallo zusammen. Hier ist die Abschrift, mit sorgfältiger Zeichensetzung.",
    "pt": "Olá a todos. Aqui está a transcrição, com uma pontuação cuidada.",
    "nl": "Hallo allemaal. Hier is de transcriptie, met zorgvuldige interpunctie.",
    "ja": "皆さん、こんにちは。句読点を正しく付けた書き起こしです。",
    "zh": "大家好。以下是带有标点符号的文字记录。",
    "ko": "안녕하세요, 여러분. 문장 부호를 정확하게 넣은 받아쓰기입니다.",
    "ru": "Всем привет. Вот расшифровка, с аккуратной пунктуацией.",
    "ar": "مرحبا بالجميع. هذا هو النص المكتوب، مع علامات الترقيم الصحيحة.",
    "pl": "Witam wszystkich. Oto transkrypcja, ze starannie dobraną interpunkcją.",
    "tr": "Herkese merhaba. İşte özenli noktalama işaretleriyle yazılmış döküm.",
}
_OPENING = set("«“‘\"([¿¡")


def _words_from_segment(seg: dict, seg_start: float, seg_end: float) -> list[Word]:
    """Whisper tokens -> words: sub-word tokens merged, punctuation attached to its word (opening « ( ¿ to
    the next one). A word starts at its first token's DTW time and ends where the next word starts.
    Spacing rules per language are applied later (cues.typography)."""
    words: list[Word] = []
    prefix = ""
    for tok in seg.get("tokens", []):
        text = tok.get("text", "")
        if not text or text.startswith("[_") or text.startswith("<|"):
            continue
        core = text.strip()
        if not core:
            continue
        t_dtw = tok.get("t_dtw", -1)
        t0 = t_dtw / 100 if t_dtw is not None and t_dtw >= 0 else tok["offsets"]["from"] / 1000
        p = float(tok.get("p", 1.0))
        starts_new = text.startswith(" ") or not words
        is_punct = not any(c.isalnum() for c in core)
        if starts_new and is_punct and all(c in _OPENING for c in core):
            prefix += core
        elif is_punct and words:  # " »", " ?", "," -> belongs to the previous word
            words[-1].text += core
        elif starts_new or not words:
            words.append(Word(prefix + core, t0, t0, p))
            prefix = ""
        else:
            words[-1].text += core
            words[-1].p = min(words[-1].p, p)
    if prefix and words:
        words[-1].text += prefix
    for w, nxt in zip(words, words[1:] + [None]):
        w.end = min(nxt.start if nxt else seg_end, w.start + 1.6)  # a word never lasts more than 1.6 s
    return words


def _repair_times(words: list[Word], lo: float, hi: float) -> None:
    """Monotonic, inside the segment; words sharing one timestamp are spread by length."""
    if not words:
        return
    for w in words:
        w.start = min(max(w.start, lo), hi)
        w.end = min(max(w.end, w.start), hi)
    i = 0
    n = len(words)
    while i < n:
        j = i
        while j + 1 < n and abs(words[j + 1].start - words[i].start) < 0.02:
            j += 1
        if j > i:  # collapsed run i..j: spread between its start and the next distinct anchor
            a = words[i].start
            b = words[j + 1].start if j + 1 < n else max(words[j].end, a + 0.3 * (j - i + 1))
            b = min(max(b, a + 0.12 * (j - i + 1)), hi)
            weights = [max(1, len(w.text)) for w in words[i:j + 1]]
            total = sum(weights)
            t = a
            for w, k in zip(words[i:j + 1], weights):
                w.start = t
                t += (b - a) * k / total
                w.end = t
        i = j + 1
    for a, b in zip(words, words[1:]):
        if b.start < a.start:
            b.start = a.start
        if a.end > b.start:
            a.end = b.start
        if a.end <= a.start:
            a.end = min(a.start + 0.05, b.start if b.start > a.start else a.start + 0.05)


def snap_to_speech(words: list[Word], speech: list[tuple[float, float]]) -> None:
    """Move word edges that fall in silence onto the nearest speech boundary (starts forward, ends backward)."""
    if not speech:
        return
    import bisect
    starts = [a for a, _ in speech]
    for w in words:
        i = bisect.bisect_right(starts, w.start) - 1
        if i >= 0 and speech[i][0] <= w.start <= speech[i][1]:
            pass
        elif i + 1 < len(speech) and w.start < speech[i + 1][0] and speech[i + 1][0] - w.start < 0.6:
            w.start = speech[i + 1][0]  # started in silence: begins with the next speech
        k = bisect.bisect_right(starts, w.end) - 1
        if k >= 0 and w.end > speech[k][1] and w.end - speech[k][1] < 0.6 and speech[k][1] > w.start:
            w.end = speech[k][1]  # ended in silence: stops with the speech
        if w.end <= w.start:
            w.end = w.start + 0.08


def transcribe(wav: Path, language: str, cli: str, model: Path, cancel: threading.Event,
               progress: Progress | None = None, glossary: str = "") -> list[dict]:
    """Whole-file transcription. Returns segments {start, end, text, words[]} on the original timeline."""
    prompt = " ".join(x for x in (PROMPTS.get(language, ""), glossary.strip()) if x)
    tmp = Path(tempfile.mkdtemp(prefix="se7en-asr-"))
    try:
        cmd = [cli, "-m", str(model), "-f", str(wav), "-l", language, "-t", str(threads()), "-bs", "5",
               "-nfa", "-dtw", "large.v3.turbo", "-ojf", "-of", str(tmp / "out"), "-pp"]
        if prompt:
            cmd += ["--prompt", prompt[:800]]
        t0 = time.monotonic()

        def on_line(line: str) -> None:
            m = re.search(r"progress =\s*(\d+)%", line)
            if m and progress:
                progress(int(m.group(1)) / 100, "")

        if progress:
            progress(0.0, "")
        _run(cmd, cancel, on_line=on_line, label="transcription")
        log.info("Transcribed %s (%s) in %.1fs", wav.name, language, time.monotonic() - t0)
        data = json.loads((tmp / "out.json").read_text("utf-8", errors="replace"))
        segments: list[dict] = []
        for seg in data.get("transcription", []):
            text = seg.get("text", "").strip()
            if not text:
                continue
            s0 = seg["offsets"]["from"] / 1000
            s1 = seg["offsets"]["to"] / 1000
            words = _words_from_segment(seg, s0, s1)
            if words:
                _repair_times(words, s0, max(s1, s0 + 0.2))
                segments.append({"start": s0, "end": s1, "text": text, "words": words})
        return _drop_hallucinations(segments)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def drop_outside_speech(segments: list[dict], speech: list[tuple[float, float]], margin: float = 0.4) -> list[dict]:
    """Remove words (and segments) Whisper produced where the VAD heard no voice (music, noise, silence)."""
    if not speech:
        return segments
    import bisect
    starts = [a for a, _ in speech]

    def in_speech(t: float) -> bool:
        i = bisect.bisect_right(starts, t + margin) - 1
        return i >= 0 and speech[i][0] - margin <= t <= speech[i][1] + margin

    out = []
    for seg in segments:
        kept = [w for w in seg["words"] if in_speech(w.start) or in_speech(w.end)]
        if len(kept) < len(seg["words"]):
            log.info("Dropped %d word(s) heard outside speech: %s", len(seg["words"]) - len(kept),
                     " ".join(w.text for w in seg["words"] if w not in kept)[:200])
        if kept:
            out.append({**seg, "words": kept})
    return out


def _drop_hallucinations(segments: list[dict]) -> list[dict]:
    out: list[dict] = []
    for seg in segments:
        norm = re.sub(r"\W+", " ", seg["text"].lower()).strip()
        if HALLUCINATIONS.search(seg["text"]) and len(norm.split()) < 12:
            log.info("Dropped a likely Whisper hallucination: %r", seg["text"])
            continue
        if out and norm and norm == re.sub(r"\W+", " ", out[-1]["text"].lower()).strip():
            continue  # repetition loop
        out.append(seg)
    return out
