"""Subtitle generation: audio → voice detection → language → Whisper → cues → correction / translation.

Intermediate results (16 kHz audio, speech regions) are cached per project, so detecting the language on import
and generating afterwards decode the video only once.
"""
from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from typing import Callable

from ..errors import MediaLabError
from ..logging_setup import get_logger
from . import cues as C
from . import llm as L
from . import project as P
from . import transcribe as T
from .languages import name as lang_name

log = get_logger("process")
Progress = Callable[[float, str, float | None], None]


def _prepared(pid: str, video: dict, cancel: threading.Event, progress: Progress, french: bool) -> tuple:
    """(wav path, speech regions, engine files), cached per project."""
    progress(0.0, "Préparation du moteur" if french else "Preparing the engine", None)
    label_dl = "Téléchargement du modèle Whisper" if french else "Downloading the Whisper model"
    cli, vad_tool, model, vad = T.engine_paths(
        lambda d, t: progress(0.0, f"{label_dl} · {d * 100 // max(t, 1)} %", None))
    cache = P.cache_dir(pid)
    wav = cache / "audio.16k.wav"
    if not wav.exists():
        progress(0.01, "Extraction du son" if french else "Extracting the sound", None)
        T.extract_audio(Path(video["path"]), wav, video.get("duration"), cancel,
                        lambda f: progress(0.01 + 0.04 * f, "Extraction du son" if french else "Extracting the sound", None))
    sp_file = cache / "speech.json"
    if sp_file.exists():
        speech = [tuple(x) for x in json.loads(sp_file.read_text("utf-8"))]
    else:
        progress(0.05, "Détection de la voix" if french else "Finding speech", None)
        speech = T.speech_segments(wav, vad_tool, vad, cancel)
        sp_file.write_text(json.dumps(speech), "utf-8")
    if not speech:
        raise MediaLabError("no_speech", "No speech was found in this video.",
                            "Subtitles are made from the voice: check that the video has spoken dialogue.")
    return wav, speech, cli, model


def detect(pid: str, cancel: threading.Event, progress: Progress, french: bool = True) -> dict:
    project = P.load(pid)
    wav, speech, cli, model = _prepared(pid, project["video"], cancel, progress, french)
    progress(0.08, "Détection de la langue" if french else "Detecting the language", None)
    result = T.detect_language(T.read_wav(wav), speech, cli, model, cancel)
    project = P.load(pid)
    project["detected"] = result
    if not project.get("source_lang"):
        project["source_lang"] = result["code"]
    if not project.get("target_lang"):
        project["target_lang"] = result["code"]
    P.save(project)
    log.info("Language of %s: %s (p=%.3f)", project["name"], result["code"], result["p"])
    return result


def generate(pid: str, opts: dict, settings, cancel: threading.Event, progress: Progress,
             french: bool = True) -> dict:
    t_start = time.monotonic()
    project = P.load(pid)
    video = project["video"]
    if not Path(video["path"]).is_file():
        raise MediaLabError("source_missing", "The video of this project no longer exists.",
                            "It may have been moved or deleted.")
    src = opts.get("source_lang") or project.get("source_lang")
    dst = opts.get("target_lang") or project.get("target_lang") or src
    engine = opts.get("engine") or settings.subtitles.engine
    polish = bool(opts.get("polish", True))
    glossary = str(opts.get("glossary") or "")[:1000]
    rules = opts.get("rules") or {}
    wav, speech, cli, model = _prepared(pid, video, cancel, progress, french)
    if not src:
        progress(0.08, "Détection de la langue" if french else "Detecting the language", None)
        det = T.detect_language(T.read_wav(wav), speech, cli, model, cancel)
        src = det["code"]
        project["detected"] = det
    dst = dst or src

    t0 = time.monotonic()
    label = f"Transcription ({lang_name(src, french)})" if french else f"Transcribing ({lang_name(src, False)})"

    def asr_progress(f: float, _msg: str) -> None:
        el = time.monotonic() - t0
        progress(0.1 + 0.6 * f, label, el / f - el if f > 0.03 else None)

    segments = T.transcribe(wav, src, cli, model, cancel, asr_progress, glossary)
    segments = T.drop_outside_speech(segments, speech)
    words = [w for s in segments for w in s["words"]]
    T.snap_to_speech(words, speech)
    if not words:
        raise MediaLabError("no_speech", "No speech could be recognized in this video.",
                            "Check the sound, or choose the spoken language manually.")
    progress(0.71, "Découpage des sous-titres" if french else "Building the subtitles", None)
    cues = C.build_cues(words, src, rules, speech)
    original = [c.to_dict() for c in cues]

    llm = None
    notes: list[str] = []
    try:
        if polish or dst != src:
            try:
                llm = L.provider(settings, lambda d, t: progress(
                    0.72, (f"Téléchargement du modèle de langue · {d * 100 // max(t, 1)} %" if french
                           else f"Downloading the language model · {d * 100 // max(t, 1)} %"), None), engine)
            except MediaLabError as e:
                if dst != src:
                    raise
                notes.append(e.message)  # correction is optional: keep the transcript as is
                llm = None
        texts = [L.clean_hesitations(C.flat(c.text), src) if polish else C.flat(c.text) for c in cues]
        if llm and polish:
            lbl = "Correction du texte" if french else "Correcting the text"
            texts, rejected = L.polish(llm, texts, src, glossary, cancel,
                                       lambda f: progress(0.72 + (0.12 if dst != src else 0.26) * f, lbl, None))
            if rejected:
                notes.append(f"{rejected} line(s) kept as transcribed (correction would have changed the meaning)")
        if llm and dst != src:
            lbl = (f"Traduction ({lang_name(src, True)} → {lang_name(dst, True)})" if french
                   else f"Translating ({lang_name(src, False)} → {lang_name(dst, False)})")
            texts, failed = L.translate(llm, texts, src, dst, glossary, cancel,
                                        lambda f: progress(0.85 + 0.13 * f, lbl, None),
                                        weights=[c.end - c.start for c in cues])
            if failed:
                notes.append(f"{failed} line(s) could not be translated")
        for c, t in zip(cues, texts):
            c.text = t
        # a cue whose words were all hesitations is gone; its neighbours keep their own timing
        cues = [c for c in cues if C.flat(c.text).strip(" .,…")] or cues
    finally:
        if llm:
            llm.stop()  # frees the graphics card for the other AI tools
    cues = C.relayout(cues, dst, rules, video.get("fps"))

    project = P.load(pid)
    project.update({
        "source_lang": src, "target_lang": dst, "engine": engine if (polish or dst != src) else None,
        "options": {**project.get("options", {}), "polish": polish, "glossary": glossary, "rules": rules},
        "status": "ready", "cues": [c.to_dict() for c in cues], "original": original, "original_lang": src,
        "generated": {"at": time.time(), "seconds": round(time.monotonic() - t_start, 1), "notes": notes,
                      "engine": getattr(llm, "name", None)},
    })
    P.save(project)
    progress(1.0, "Terminé" if french else "Done", 0)
    log.info("Subtitles for %s: %d cues (%s → %s) in %.1fs", project["name"], len(cues), src, dst,
             time.monotonic() - t_start)
    return copy.deepcopy(project)
