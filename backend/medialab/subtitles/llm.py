"""Language model for subtitle correction and translation: local (llama.cpp + Ministral 3 8B) or Claude.

Both providers return exactly one output line per input cue (JSON schema), so every corrected / translated
line keeps its cue's timing. Batches carry the previous cues as context. Safeguards:

* correction never rewrites meaning: a corrected line that shares too little with the original is rejected
  and the original (cleaned of hesitations) is kept;
* a batch whose line count does not match is retried in smaller parts, then left untranslated rather than
  shifted onto the wrong cues.

Local model: chosen after comparing Ministral 3 8B and Gemma 4 12B on this exact task; Gemma shifted
translated lines between cues, Ministral kept every line aligned and wrote the most natural French.
"""
from __future__ import annotations

import json
import re
import socket
import subprocess
import threading
import time
from typing import Callable

import httpx

from .. import ai_assets, components
from ..errors import Canceled, MediaLabError
from ..logging_setup import get_logger
from .languages import english_name

log = get_logger("process")
BATCH = 24
CONTEXT = 4
CLAUDE_DEFAULT_MODEL = "claude-opus-5-5"

FILLERS = {
    "fr": r"\b(?:euh+|heu+|hum+|hmm+|mmh+|bah|ben)\b[,.]?",
    "en": r"\b(?:uh+|um+|umm+|erm+|hmm+|mhm)\b[,.]?",
    "es": r"\b(?:eh+|em+|este|mmm+)\b[,.]?",
    "it": r"\b(?:ehm+|eh+|mmm+)\b[,.]?",
    "de": r"\b(?:äh+|ähm+|öh+|hm+)\b[,.]?",
    "pt": r"\b(?:éh+|hum+|ahn+)\b[,.]?",
}


def clean_hesitations(text: str, lang: str) -> str:
    """Remove pure hesitations and stutters ("je je", "euh") - never meaningful words."""
    t = text
    pat = FILLERS.get(lang)
    if pat:
        t = re.sub(pat, "", t)  # case-sensitive on purpose: "Ben" can be a name
    t = re.sub(r"\b(\w{1,6})( \1\b)+", r"\1", t, flags=re.I)  # je je / the the
    t = re.sub(r"\s+([,.!?;:])", r"\1", re.sub(r"\s{2,}", " ", t)).strip(" ,")
    return t[:1].upper() + t[1:] if t and text[:1].isupper() else t


# ------------------------------------------------------------------ prompts
POLISH_FR = """Tu es correcteur professionnel de sous-titres en français.
On te donne des phrases numérotées issues d'une transcription automatique de la parole (sous-titres d'une vidéo).
Corrige chaque phrase : orthographe, grammaire, accords, conjugaison, ponctuation, majuscules, espaces,
et les erreurs évidentes de transcription (homophones mal reconnus : « au dépend » → « aux dépens », etc.).
Supprime les hésitations et les tics verbaux sans valeur (euh, bah, ben, « du coup » ou « en fait » de remplissage),
les bégaiements et les répétitions involontaires.
Ne change JAMAIS le sens, les informations ni la manière de parler : garde le vocabulaire, le registre (tutoiement,
« on », expressions familières) et l'ordre des idées. N'ajoute rien, ne résume pas, ne reformule pas.
Garde l'ordre des mots, les inversions et les tournures de l'auteur (« au Caucase pareil » reste tel quel).
N'ajoute ni guillemets ni tirets de dialogue qui ne sont pas dans la phrase ; ne supprime aucune phrase.
Texte brut uniquement (pas de markdown).
Réponds par un objet JSON : la clé "n" contient la phrase n corrigée, et seulement elle.
Ne fusionne jamais deux phrases et ne déplace aucun mot d'une phrase à une autre."""

POLISH_OTHER = """You are a professional subtitle proofreader for {lang}.
You receive numbered sentences from an automatic speech transcription (a video's subtitles), in {lang}.
Fix each sentence in {lang}: spelling, grammar, agreement, punctuation, capitalization, spacing and obvious
recognition errors (misheard homophones). Remove meaningless hesitations and filler words, stutters and
involuntary repetitions.
NEVER change the meaning, the information or the way the person speaks: keep the vocabulary, the register and
the order of ideas and words. Do not add, summarize or rephrase; never drop a sentence; do not add quotation
marks or dialogue dashes that are not there.
Plain text only (no markdown). Answer with a JSON object: key "n" holds sentence n corrected, and nothing else.
Never merge two sentences and never move words from one sentence to another."""

TRANSLATE = """You are a professional subtitle translator ({src} → {dst}).
Translate the numbered lines (each one is a sentence spoken in a video) into natural, idiomatic {dst}, as a
professional translator would for a film or a documentary: keep the meaning, tone, register, humour and intent;
adapt idioms and cultural references instead of translating word for word. Subtitles must be concise and easy to
read: prefer short natural wording. Never add information, words or details that are not in the original.
Plain text only (no markdown).
Answer with a JSON object: key "n" holds the translation of line n, and nothing else.
Never merge two lines and never move content from one line to another."""


def _user_message(items: list[str], context: list[str], glossary: str) -> str:
    parts = []
    if glossary.strip():
        parts.append("Glossary / names to keep exactly:\n" + glossary.strip())
    if context:
        parts.append("Previous subtitles (context only, do not return them):\n" + "\n".join(context))
    parts.append(f"Subtitles ({len(items)}):\n" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(items)))
    return "\n\n".join(parts)


def _schema(n: int) -> dict:
    """One key per numbered input line ({"1": ..., "2": ...}): the model fills each numbered slot, so it cannot
    merge two lines and shift the rest (a plain list allowed that)."""
    keys = [str(i + 1) for i in range(n)]
    return {"type": "object", "properties": {k: {"type": "string"} for k in keys}, "required": keys,
            "additionalProperties": False}


def _parse(obj: dict, n: int) -> list[str]:
    if not isinstance(obj, dict):
        raise ValueError("not an object")
    if "lines" in obj and isinstance(obj["lines"], list):  # tolerate the older list shape
        return [str(x) for x in obj["lines"]]
    return [str(obj.get(str(i + 1), "")) for i in range(n)]


def _looping(text: str) -> bool:
    """A generation stuck in a loop repeats the same short passage over and over."""
    tail = text[-1200:]
    if len(tail) < 240:
        return False
    if len(set(tail[-200:])) <= 3:  # "\n\n\n\n…", "aaaa…"
        return True
    for period in range(1, 181):  # the same passage repeated 6 times in a row
        if period * 6 <= len(tail) and tail[-period * 6:] == tail[-period:] * 6:
            return True
    return False


def _strip_number(line: str) -> str:
    return re.sub(r"^\s*\d{1,3}[.)]\s+", "", line).strip()


def clean_output(text: str, lang: str) -> str:
    """Model output -> plain subtitle text: no markdown, French quotation marks in French."""
    t = _strip_number(text)
    t = re.sub(r"(\*{1,2}|__?)(\S(?:.*?\S)?)\1", r"\2", t)  # *emphasis*, **bold**, _x_
    t = t.replace("\u201c", '"').replace("\u201d", '"')
    if lang == "fr" and '"' in t:
        out = []
        for i, ch in enumerate(t):
            if ch == '"':
                prev = t[i - 1] if i else " "
                out.append("«" if (i == 0 or prev in " (\u00a0—–-") else "»")
            else:
                out.append(ch)
        t = "".join(out)
    return t.strip()


# ------------------------------------------------------------------ providers
class LocalLLM:
    """llama-server (Vulkan build) running Ministral 3 8B; started on demand, stopped after the job."""

    name = "local"

    def __init__(self, progress: Callable[[int, int], None] | None = None) -> None:
        self.progress = progress
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self.gpu_layers = 99
        self.cancel: threading.Event | None = None

    def _start(self) -> None:
        if not components.ensure("llama"):
            raise MediaLabError("component_missing", "The local language engine could not be installed.",
                                "Check your internet connection and retry.", True, extra={"component": "llama"})
        model = ai_assets.ensure("ministral_8b", self.progress)
        server = components.require("llama-server")
        for layers in (self.gpu_layers, 0):  # GPU first; CPU if the graphics card cannot hold the model
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                self.port = s.getsockname()[1]
            self.proc = subprocess.Popen(
                [server, "-m", str(model), "-ngl", str(layers), "-c", "8192", "--host", "127.0.0.1",
                 "--port", str(self.port), "--jinja", "-np", "1",
                 "--reasoning-format", "none", "--reasoning-budget", "0"],  # JSON from the first token: no hidden
                 # "[THINK]" block (Ministral can loop in it forever before answering)
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                creationflags=components.CREATE_NO_WINDOW)
            t0 = time.monotonic()
            while time.monotonic() - t0 < 240 and self.proc.poll() is None:
                try:
                    if httpx.get(f"http://127.0.0.1:{self.port}/health", timeout=2).status_code == 200:
                        log.info("Local language model ready (%s) in %.1fs", "GPU" if layers else "CPU",
                                 time.monotonic() - t0)
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            self.stop()
            log.warning("llama-server did not start with %d GPU layers", layers)
        raise MediaLabError("llm_start_failed", "The local language model could not start.",
                            "Close other heavy apps (not enough memory) or use Claude in Settings › Subtitles.", True)

    def lines(self, system: str, items: list[str], context: list[str], glossary: str) -> list[str]:
        """Streams the answer: cancellation stops it at once, and a runaway or looping generation is cut
        (token budget proportional to the input) instead of running for minutes."""
        if self.proc is None or self.proc.poll() is not None:
            self._start()
        budget = 200 + sum(len(t) for t in items) + 30 * len(items)  # ~2.5× the tokens a faithful answer needs
        payload = {
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": _user_message(items, context, glossary)}],
            "temperature": 0.15, "max_tokens": budget, "stream": True, "repeat_penalty": 1.05,
            "response_format": {"type": "json_schema", "json_schema": {"name": "subtitles", "strict": True,
                                                                       "schema": _schema(len(items))}}}
        parts: list[str] = []
        with httpx.stream("POST", f"http://127.0.0.1:{self.port}/v1/chat/completions", json=payload,
                          timeout=httpx.Timeout(30, read=180)) as r:
            if r.status_code != 200:
                raise MediaLabError("llm_failed", "The local language model failed.", "Retry.", True,
                                    r.read().decode("utf-8", "replace")[-800:])
            for line in r.iter_lines():
                if self.cancel is not None and self.cancel.is_set():
                    raise Canceled()
                if not line.startswith("data: "):
                    continue
                data = line[6:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choice = (chunk.get("choices") or [{}])[0]
                parts.append((choice.get("delta") or {}).get("content") or "")
                if len(parts) % 40 == 0 and _looping("".join(parts)):
                    log.warning("Local model is repeating itself; batch of %d retried in smaller parts", len(items))
                    raise ValueError("repetition loop")
                if choice.get("finish_reason") == "length":
                    raise ValueError("token budget exceeded")
        return _parse(json.loads("".join(parts)), len(items))

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.proc = None


class ClaudeLLM:
    """Anthropic Messages API (official SDK), structured JSON output, server-side fallback on refusals."""

    name = "claude"

    def __init__(self, api_key: str, model: str = CLAUDE_DEFAULT_MODEL) -> None:
        if not api_key:
            raise MediaLabError("claude_key_missing", "No Claude API key is configured.",
                                "Paste your Anthropic API key in Settings › Subtitles, or use the local model.")
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, max_retries=3, timeout=180.0)
        self.model = model or CLAUDE_DEFAULT_MODEL
        self.cancel: threading.Event | None = None

    def lines(self, system: str, items: list[str], context: list[str], glossary: str) -> list[str]:
        a = self.anthropic
        try:
            # streamed: long answers never hit an HTTP timeout, and a cancel stops the request at once
            with self.client.beta.messages.stream(
                model=self.model,
                max_tokens=32000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=system,
                messages=[{"role": "user", "content": _user_message(items, context, glossary)}],
                output_config={"effort": "medium",
                               "format": {"type": "json_schema", "schema": _schema(len(items))}},
            ) as stream:
                for _event in stream:
                    if self.cancel is not None and self.cancel.is_set():
                        raise Canceled()
                resp = stream.get_final_message()
        except a.AuthenticationError as e:
            raise MediaLabError("claude_key_invalid", "The Claude API key was refused.",
                                "Check the key in Settings › Subtitles.", False, str(e)) from e
        except a.PermissionDeniedError as e:
            raise MediaLabError("claude_forbidden", "This Claude API key cannot use this model.",
                                "Check your Anthropic account or choose another model.", False, str(e)) from e
        except a.RateLimitError as e:
            raise MediaLabError("claude_rate_limited", "Claude is rate-limiting this API key.",
                                "Wait a minute and retry.", True, str(e)) from e
        except a.APIStatusError as e:
            if e.status_code == 529 or e.status_code >= 500:
                raise MediaLabError("claude_overloaded", "Claude is temporarily unavailable.",
                                    "Retry in a few minutes, or use the local model.", True, str(e)) from e
            raise MediaLabError("claude_failed", f"Claude returned an error ({e.status_code}).", "Retry.",
                                True, str(e)) from e
        except a.APIConnectionError as e:
            raise MediaLabError("claude_offline", "Claude could not be reached.",
                                "Check your internet connection, or use the local model.", True, str(e)) from e
        if resp.stop_reason == "refusal":
            raise MediaLabError("claude_refused", "Claude declined to process these subtitles.",
                                "Use the local model for this video.", False)
        text = next((b.text for b in resp.content if b.type == "text"), "")
        return _parse(json.loads(text), len(items))

    def stop(self) -> None:
        pass


# ------------------------------------------------------------------ tasks
def _words(t: str) -> set[str]:
    return {w for w in re.findall(r"\w+", t.lower()) if len(w) > 2}


# words a correction may remove (hesitations, verbal tics): their removal is not a loss of content
TICS = {
    "fr": set("euh heu hum hmm mmh bah ben du coup en fait genre voilà quoi bon enfin alors donc style tu vois sais "
              "j je veux dire".split()),
    "en": set("uh um umm erm hmm like you know so well actually basically literally i mean kind of sort right okay "
              "just".split()),
}


def _seq(t: str) -> list[str]:
    """Lower-case words; a stuttered repetition (« le le ») counts once, since removing it is a correction."""
    out: list[str] = []
    for w in re.findall(r"\w+", t.lower()):
        out.append(w)
        for n in (1, 2, 3):  # « on va on va » → « on va »
            if len(out) >= 2 * n and out[-n:] == out[-2 * n:-n]:
                del out[-n:]
                break
    return out


def safe_polish(original: str, corrected: str, lang: str = "fr") -> bool:
    """A correction may fix spelling (close words), punctuation and remove hesitations; it must keep every other
    word, in the same order, and add almost nothing. A shifted, shortened or reworded answer is refused."""
    import difflib
    a, b = _seq(original), _seq(corrected)
    if not a:
        return bool(corrected.strip())
    if not b:
        return False
    tics = TICS.get(lang, set())
    matched, deleted, inserted = 0, [], []
    for tag, a0, a1, b0, b1 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            matched += a1 - a0
        elif tag == "replace":
            for x, y in zip(a[a0:a1], b[b0:b1]):  # corrected spelling: close words count as kept
                if difflib.SequenceMatcher(a=x, b=y).ratio() >= 0.6:
                    matched += 1
                else:
                    deleted.append(x)
                    inserted.append(y)
            deleted += a[a0 + (b1 - b0):a1]
            inserted += b[b0 + (a1 - a0):b1]
        elif tag == "delete":
            deleted += a[a0:a1]
        else:
            inserted += b[b0:b1]
    content = [w for w in deleted if w not in tics]
    need = len([w for w in a if w not in tics]) or 1
    kept = 1 - len(content) / need
    moved = {w for w in content if len(w) >= 4} & set(inserted)
    return kept >= 0.85 and not moved and len(inserted) <= max(2, len(a) // 5)


def guard_quotes(original: str, corrected: str) -> str:
    """Quotation marks the speech transcript did not have are not added (the model sometimes quotes every
    sentence of a dialogue)."""
    t = corrected
    for mark in ("«", "»", '"', "“", "”"):
        if mark not in original and mark in t:
            t = t.replace(mark + " ", "").replace(" " + mark, "").replace(mark, "")
    return re.sub(r"\s{2,}", " ", t).strip()


def run_batches(llm, system: str, texts: list[str], glossary: str, cancel: threading.Event,
                progress: Callable[[float], None] | None = None,
                check: Callable[[str, str], bool] | None = None, lang: str = "") -> tuple[list[str], int]:
    """Process all cues in batches; returns (lines, number of cues kept unchanged after a failed check)."""
    out: list[str] = []
    rejected = 0
    llm.cancel = cancel  # lets the provider stop mid-generation
    i = 0
    while i < len(texts):
        if cancel.is_set():
            raise Canceled()
        batch = texts[i:i + BATCH]
        context = out[-CONTEXT:]
        lines = _checked(llm, system, batch, context, glossary, check, lang)
        for src, new in zip(batch, lines):
            new = clean_output(new, lang) if new else ""
            if not new or (check and not check(src, new)):
                rejected += 1
                new = src
            out.append(new)
        i += len(batch)
        if progress:
            progress(i / len(texts))
    return out, rejected


def _checked(llm, system: str, batch: list[str], context: list[str], glossary: str,
             check: Callable[[str, str], bool] | None, lang: str, depth: int = 0) -> list[str]:
    """Like _ask, and when several lines fail the check (typically an answer shifted by one line),
    the batch is retried in two halves instead of losing those lines."""
    lines = _ask(llm, system, batch, context, glossary)
    if not check or len(batch) < 2 or depth >= 3:
        return lines
    bad = sum(1 for a, b in zip(batch, lines) if not b or not check(a, clean_output(b, lang)))
    if bad < max(2, len(batch) // 5):
        return lines
    log.info("%d of %d lines failed the check: batch retried in two halves", bad, len(batch))
    half = len(batch) // 2
    first = _checked(llm, system, batch[:half], context, glossary, check, lang, depth + 1)
    return first + _checked(llm, system, batch[half:], (context + batch[:half])[-CONTEXT:], glossary, check, lang,
                            depth + 1)


def _ask(llm, system: str, batch: list[str], context: list[str], glossary: str, depth: int = 0) -> list[str]:
    try:
        lines = llm.lines(system, batch, context, glossary)
    except (ValueError, KeyError, TypeError) as e:  # malformed JSON, runaway or looping generation
        log.warning("Language model returned malformed output (%s)", e)
        lines = []
    if len(lines) == len(batch):
        return lines
    if len(batch) > 1 and depth < 3:  # never shift lines onto the wrong cues: split and retry
        half = len(batch) // 2
        return (_ask(llm, system, batch[:half], context, glossary, depth + 1)
                + _ask(llm, system, batch[half:], context + batch[:half][-CONTEXT:], glossary, depth + 1))
    log.warning("Language model could not keep the line count; subtitle left as is")
    return ["" for _ in batch]


def polish(llm, texts: list[str], lang: str, glossary: str, cancel: threading.Event,
           progress: Callable[[float], None] | None = None) -> tuple[list[str], int]:
    """Whole sentences are corrected (the model sees the full context), then every corrected word is put back
    in the cue of the original word it aligns with: words keep their timing, nothing moves to another cue."""
    system = POLISH_FR if lang == "fr" else POLISH_OTHER.format(lang=english_name(lang))
    groups = sentence_groups(texts)
    sentences = [" ".join(texts[i].strip() for i in g) for g in groups]
    check = lambda a, b: safe_polish(a, guard_quotes(a, b), lang)  # noqa: E731
    done, rejected = run_batches(llm, system, sentences, glossary, cancel, progress, check, lang)
    out = [""] * len(texts)
    for g, src, corrected in zip(groups, sentences, done):
        corrected = guard_quotes(src, corrected)
        parts = realign([texts[i] for i in g], corrected) if len(g) > 1 else [corrected]
        for i, part in zip(g, parts):
            out[i] = part
    return out, rejected


def _key(w: str) -> str:
    return re.sub(r"\W+", "", w.lower())


def realign(original_parts: list[str], corrected: str) -> list[str]:
    """Distribute a corrected sentence over the cues of the original one, word by word (difflib alignment):
    each corrected word goes to the cue holding the original word it replaces; added words follow the
    previous word; a cue whose words were all removed (hesitations) becomes empty."""
    import difflib
    orig: list[tuple[str, int]] = [(w, k) for k, part in enumerate(original_parts) for w in part.split()]
    corr = corrected.split()
    if not orig:
        return [corrected] + [""] * (len(original_parts) - 1)
    owner = [0] * len(corr)
    sm = difflib.SequenceMatcher(a=[_key(w) for w, _ in orig], b=[_key(w) for w in corr], autojunk=False)
    for tag, a0, a1, b0, b1 in sm.get_opcodes():
        for j in range(b0, b1):
            if tag in ("equal", "replace") and a1 > a0:
                a = a0 + min(a1 - a0 - 1, (j - b0) * (a1 - a0) // max(1, b1 - b0))
                owner[j] = orig[a][1]
            else:  # inserted word: stays with the word before it (or the first cue)
                owner[j] = owner[j - 1] if j > 0 else (orig[a0][1] if a0 < len(orig) else orig[-1][1])
    for j in range(1, len(owner)):
        owner[j] = max(owner[j], owner[j - 1])  # keep the order
    parts = [[] for _ in original_parts]
    for w, k in zip(corr, owner):
        if parts[k] == [] and not any(ch.isalnum() for ch in w) and k > 0 and parts[k - 1]:
            parts[k - 1].append(w)  # a lone punctuation mark stays at the end of the previous cue
        else:
            parts[k].append(w)
    return [" ".join(p) for p in parts]


def sentence_groups(texts: list[str], max_group: int = 6) -> list[list[int]]:
    """Consecutive cues that form complete sentences (a sentence may span several cues)."""
    from .cues import ends_sentence
    groups: list[list[int]] = []
    cur: list[int] = []
    for i, t in enumerate(texts):
        cur.append(i)
        last = t.strip().split(" ")[-1] if t.strip() else ""
        if ends_sentence(last) or len(cur) >= max_group:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


def distribute(text: str, weights: list[float], lang: str) -> list[str]:
    """Split a translated sentence over its cues, in proportion to how long each original cue speaks,
    cutting at the most natural word boundary near each proportional point."""
    from .cues import ends_clause, ends_sentence, is_weak
    from .languages import NO_SPACES
    k = len(weights)
    if k == 1:
        return [text]
    units = list(text) if lang in NO_SPACES else text.split(" ")
    sep = "" if lang in NO_SPACES else " "
    if len(units) < k:
        return [text] + [""] * (k - 1)
    lens = [len(u) + 1 for u in units]
    total_len = sum(lens)
    total_w = sum(max(w, 0.05) for w in weights)
    cuts: list[int] = []
    acc_w = 0.0
    lo = 1
    for w in weights[:-1]:
        acc_w += max(w, 0.05)
        target = acc_w / total_w * total_len
        remaining_cuts = k - len(cuts) - 1
        best, best_cost = None, float("inf")
        acc = 0
        for idx in range(1, len(units)):
            acc += lens[idx - 1]
            if idx < lo or len(units) - idx < remaining_cuts:
                continue
            last = units[idx - 1]
            cost = abs(acc - target) / max(total_len, 1) * 10
            if ends_sentence(last):
                cost -= 2.0
            elif ends_clause(last):
                cost -= 1.5
            if is_weak(last, lang):
                cost += 3.0
            if cost < best_cost:
                best, best_cost = idx, cost
        cuts.append(best if best is not None else lo)
        lo = cuts[-1] + 1
    parts, prev = [], 0
    for c in cuts + [len(units)]:
        parts.append(sep.join(units[prev:c]).strip())
        prev = c
    return parts


def translate(llm, texts: list[str], src: str, dst: str, glossary: str, cancel: threading.Event,
              progress: Callable[[float], None] | None = None,
              weights: list[float] | None = None) -> tuple[list[str], int]:
    """Whole sentences are translated (natural wording, nothing can drift onto another cue), then each
    translated sentence is spread over the cues it spans, following the original speech durations."""
    system = TRANSLATE.format(src=english_name(src), dst=english_name(dst))
    groups = sentence_groups(texts)
    sentences = [" ".join(texts[i].strip() for i in g) for g in groups]
    done, failed = run_batches(llm, system, sentences, glossary, cancel, progress, None, dst)
    out = [""] * len(texts)
    w = weights or [max(1.0, len(t)) for t in texts]
    for g, src_text, tr in zip(groups, sentences, done):
        parts = distribute(tr, [w[i] for i in g], dst) if tr != src_text or len(g) == 1 else [texts[i] for i in g]
        for i, part in zip(g, parts):
            out[i] = part
    return out, failed


def provider(settings, progress: Callable[[int, int], None] | None = None, engine: str | None = None):
    """The chosen engine: Claude (with the stored API key) or the local model."""
    st = settings.subtitles
    engine = engine or st.engine
    if engine == "claude":
        from .secret import get_key
        return ClaudeLLM(get_key(), st.claude_model or CLAUDE_DEFAULT_MODEL)
    return LocalLLM(progress)
