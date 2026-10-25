"""Subtitle cues from timed words, following professional subtitling rules.

* one cue = at most 2 lines of ``max_chars`` characters (42 for Latin scripts, fewer for CJK);
* cues are cut where the speech naturally breaks: end of sentence first, then clause punctuation, then pauses;
  never right after an article, a preposition or a conjunction ("weak" words), never inside a number group;
* timing follows the voice: a cue starts with its first word and ends with its last one, then lingers for
  a comfortable reading speed (``max_cps``) without overlapping the next cue; short gaps are closed so cues
  chain smoothly, with a minimum gap of 2 frames;
* line breaks are chosen among all positions by a cost: never after a weak word, preferably after
  punctuation, lines as balanced as possible (slightly shorter first line: pyramid shape).

The optimal segmentation of each speech block is found by dynamic programming over word boundaries.
"""
from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass, field

from .languages import NO_SPACES

NBSP = " "
NNBSP = " "

WEAK = {
    "fr": """le la les l' un une des du de d' à au aux en et ou ni mais que qu' qui dont où ce c' cet cette ces mon ma
             mes ton ta tes son sa ses notre nos votre vos leur leurs je j' tu il elle on nous vous ils elles me m' te
             t' se s' ne n' y par pour sur sous dans avec sans chez vers entre contre très plus moins si comme car donc
             puis lorsque quand parce jusqu' est sont a ont pas""",
    "en": """a an the of to in on at by for with from into onto and or but nor that which who whom whose this these
             those my your his her its our their is are was were be been being i you he she we they not very as if
             than so because when while about over under around through across behind beside within without
             like upon toward towards via per""",
    "es": "el la los las un una unos unas de del a al en y o u ni pero que quien cuyo este esta estos estas mi tu su "
          "nuestro vuestro por para con sin sobre entre muy más menos si como porque cuando no se me te le les lo",
    "it": "il lo la i gli le un uno una di del della dei degli delle a al alla ai agli alle da dal in nel nella con su "
          "per tra fra e o ma che chi cui questo questa mio tuo suo nostro vostro loro molto più meno se come non si mi ti",
    "de": "der die das den dem des ein eine einen einem einer eines und oder aber sondern dass wenn weil als ob von zu "
          "zum zur mit bei nach aus für auf an in im am um durch gegen ohne unter über vor sehr nicht mein dein sein ihr "
          "unser euer ich du er sie es wir",
    "pt": "o a os as um uma uns umas de do da dos das em no na nos nas por pelo pela para com sem e ou mas que quem "
          "cujo este esta meu teu seu nosso muito mais menos se como porque quando não me te lhe",
    "nl": "de het een en of maar dat die dit deze van in op aan met voor door naar bij om over uit te niet zeer mijn "
          "jouw zijn haar ons hun ik je jij hij zij we wij",
}
_WEAK = {k: {w.strip() for w in v.split()} for k, v in WEAK.items()}
_OBJECTS = {"en": {"them", "it", "us", "him", "her", "me", "you"}, "fr": {"-moi", "-toi", "-le", "-la", "-les", "-nous", "-vous", "-lui", "-leur"}}

# Netflix-style defaults per language family
DEFAULT_RULES = {"max_chars": 42, "max_lines": 2, "max_cps": 17.0, "min_duration": 0.833, "max_duration": 7.0,
                 "min_gap": 0.083, "close_gap": 0.5, "linger": 0.6}
LANG_RULES = {"ja": {"max_chars": 16, "max_cps": 4.0}, "zh": {"max_chars": 16, "max_cps": 9.0},
              "yue": {"max_chars": 16, "max_cps": 9.0}, "ko": {"max_chars": 16, "max_cps": 12.0},
              "th": {"max_chars": 35, "max_cps": 15.0}, "en": {"max_cps": 20.0}}


def rules_for(lang: str, overrides: dict | None = None) -> dict:
    r = {**DEFAULT_RULES, **LANG_RULES.get(lang, {})}
    for k, v in (overrides or {}).items():
        if v is not None and k in r:
            r[k] = type(r[k])(v)
    return r


@dataclass
class Cue:
    start: float
    end: float
    text: str  # lines separated by "\n"
    words: list[dict] = field(default_factory=list)  # original timed words {text, start, end, p}
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    speaker: int | None = None

    def to_dict(self) -> dict:
        return {"id": self.id, "start": round(self.start, 3), "end": round(self.end, 3), "text": self.text,
                "words": self.words, "speaker": self.speaker}


# ------------------------------------------------------------------ text helpers
def _base(word: str) -> str:
    return re.sub(r"^[«“‘\"(¿¡]+|[.,;:!?…»”’\")]+$", "", word).lower()


def is_weak(word: str, lang: str) -> bool:
    b = _base(word)
    if lang == "en" and re.fullmatch(r"[a-z]+'(s|re|ve|m|ll|d)", b):
        return True  # that's, they're, I'm: subject + auxiliary, the verb follows
    weak = _WEAK.get(lang, set())
    return b in weak or (b.endswith(("'", "’")) and b[:-1] + "'" in weak)


def ends_sentence(word: str) -> bool:
    return bool(re.search(r"[.!?…。！？]([»”’\")]*)$", word))


def ends_clause(word: str) -> bool:
    return bool(re.search(r"[,;:–—、，；：]([»”’\")]*)$", word))


def visible_len(text: str) -> int:
    """Characters as the viewer sees them (combining marks don't count)."""
    return sum(1 for c in unicodedata.normalize("NFC", text) if not unicodedata.combining(c) and c != "\n")


def join_words(words: list[str], lang: str) -> str:
    sep = "" if lang in NO_SPACES else " "
    return typography(sep.join(w.strip() for w in words if w.strip()), lang)


def typography(text: str, lang: str) -> str:
    """Spacing rules of the language: French non-breaking spaces before ; : ! ? » and after «."""
    t = re.sub(r"[ \t]+", " ", text.replace("’", "'")).strip()
    t = re.sub(r" ([,.…)])", r"\1", t)
    if lang == "fr":
        t = re.sub(r"\s*([;:!?»])", NBSP + r"\1", t)
        t = re.sub(r"«\s*", "«" + NBSP, t)
        t = re.sub(r"^" + NBSP, "", t)
        t = re.sub(NBSP + r"([!?])" + NBSP + r"([!?])", NBSP + r"\1\2", t)  # "?!" stays together
        t = re.sub(r"(\d)" + NBSP + ":" + NBSP + r"?(\d)", r"\1:\2", t)  # 10:30
    else:
        t = re.sub(r"\s+([;:!?])", r"\1", t)
    return t


def flat(text: str) -> str:
    return re.sub(r"\s*\n\s*", " ", text).strip()


# ------------------------------------------------------------------ line breaking
def break_lines(text: str, lang: str, max_chars: int = 42, max_lines: int = 2) -> str:
    """Best 1- or 2-line layout of a cue's text."""
    return layout(text, lang, max_chars, max_lines)[0]


def layout(text: str, lang: str, max_chars: int = 42, max_lines: int = 2) -> tuple[str, float]:
    """(best layout, its cost): 0 for one line; higher when the only possible breaks are poor."""
    t = flat(text)
    if visible_len(t) <= max_chars or max_lines < 2:
        return t, 0.0
    if lang in NO_SPACES:
        cut = _best_cjk_cut(t, max_chars)
        return t[:cut].rstrip() + "\n" + t[cut:].lstrip(), 0.0
    tokens = t.split(" ")
    best, best_cost = None, float("inf")
    total = visible_len(t)
    for k in range(1, len(tokens)):
        l1 = " ".join(tokens[:k])
        l2 = " ".join(tokens[k:])
        n1, n2 = visible_len(l1), visible_len(l2)
        cost = 0.0
        over = max(0, n1 - max_chars) + max(0, n2 - max_chars)
        cost += over * 10
        cost += abs(n1 - n2) / max(total, 1) * 6  # balance
        if n1 > n2:
            cost += 0.3  # pyramid: slightly shorter top line
        last = tokens[k - 1]
        if ends_sentence(last):
            cost -= 4
        elif ends_clause(last):
            cost -= 3
        if is_weak(last, lang):
            cost += 6
        if re.fullmatch(r"[\d.,]+", _base(last)) and not ends_clause(last):
            cost += 4  # keep numbers with what they count
        if tokens[k][:1] in "»)”" or tokens[k] in ("-", "–"):
            cost += 5
        if cost < best_cost:
            best, best_cost = (l1, l2), cost
    return (f"{best[0]}\n{best[1]}", best_cost) if best else (t, 0.0)


def _best_cjk_cut(t: str, max_chars: int) -> int:
    n = len(t)
    best, cost = n // 2, float("inf")
    for k in range(max(1, n - max_chars), min(n - 1, max_chars) + 1):
        c = abs(k - n / 2) + (0 if t[k - 1] in "，、。！？：；" else 3)
        if c < cost:
            best, cost = k, c
    return best


def fits(text: str, lang: str, max_chars: int, max_lines: int) -> bool:
    return all(visible_len(line) <= max_chars for line in break_lines(text, lang, max_chars, max_lines).split("\n"))


# ------------------------------------------------------------------ segmentation
def build_cues(words: list, lang: str, rules: dict | None = None,
               speech: list[tuple[float, float]] | None = None) -> list[Cue]:
    """Timed words -> cues. ``words``: objects with text/start/end/p (transcribe.Word) or dicts."""
    r = rules_for(lang, rules)
    ws = [w if isinstance(w, dict) else w.to_dict() for w in words]
    ws = [w for w in ws if str(w["text"]).strip()]
    if not ws:
        return []
    # hard blocks at long pauses: a cue never spans a silence of 1.2 s or more
    blocks, cur = [], [ws[0]]
    for a, b in zip(ws, ws[1:]):
        if b["start"] - a["end"] >= 1.2:
            blocks.append(cur)
            cur = []
        cur.append(b)
    blocks.append(cur)
    cues: list[Cue] = []
    for block in blocks:
        for i, j in _segment(block, lang, r):
            part = block[i:j]
            text = join_words([w["text"] for w in part], lang)
            cues.append(Cue(part[0]["start"], part[-1]["end"],
                            break_lines(text, lang, r["max_chars"], r["max_lines"]), part))
    if speech:
        align_to_speech(cues, speech)
    return retime(cues, r)


def align_to_speech(cues: list[Cue], speech: list[tuple[float, float]]) -> None:
    """Word timestamps lag the voice by ~0.1-0.2 s: a cue that starts just after a speech onset starts on it;
    a cue whose voice stops just before / after a speech offset ends on it (reading linger comes after)."""
    import bisect
    starts = [a for a, _ in speech]
    ends = [b for _, b in speech]
    prev_end = 0.0
    for c in cues:
        k = bisect.bisect_right(starts, c.start + 0.05) - 1
        if k >= 0 and c.start - 0.4 <= starts[k] <= c.start + 0.05 and starts[k] > prev_end:
            c.start = starts[k]
        k = bisect.bisect_left(ends, c.end - 0.3)
        if k < len(ends) and ends[k] <= c.end + 0.4:
            c.end = max(c.start + 0.2, ends[k])
        prev_end = c.end


def _segment(block: list[dict], lang: str, r: dict) -> list[tuple[int, int]]:
    n = len(block)
    cap = r["max_chars"] * r["max_lines"]
    INF = float("inf")
    dp = [INF] * (n + 1)
    back = [0] * (n + 1)
    dp[0] = 0.0
    for j in range(1, n + 1):
        for i in range(j - 1, max(-1, j - 60), -1):
            part = block[i:j]
            text = join_words([w["text"] for w in part], lang)
            chars = visible_len(text)
            dur = part[-1]["end"] - part[0]["start"]
            if j - i > 1 and (chars > cap or dur > r["max_duration"] or
                              not fits(text, lang, r["max_chars"], r["max_lines"])):
                break  # longer cues starting earlier only get worse
            c = dp[i] + _cue_cost(block, i, j, chars, dur, lang, r)
            if c < dp[j]:
                dp[j], back[j] = c, i
    out, j = [], n
    while j > 0:
        out.append((back[j], j))
        j = back[j]
    return out[::-1]


def _cue_cost(block: list[dict], i: int, j: int, chars: int, dur: float, lang: str, r: dict) -> float:
    cost = 1.0  # fewer cues are easier to follow
    last = block[j - 1]["text"]
    if j < len(block):  # quality of the cut after this cue
        pause = block[j]["start"] - block[j - 1]["end"]
        if ends_sentence(last):
            cut = 0.0
        elif ends_clause(last):
            cut = 0.8
        else:
            cut = 2.5
        if pause >= 0.25:
            cut -= min(1.2, pause * 2)
        if is_weak(last, lang):
            cut += 4.0
        if _base(block[j]["text"]) in _OBJECTS.get(lang, ()):
            cut += 1.5  # "around / them": a pronoun object stays with its verb or preposition
        cost += max(cut, -0.5)
    if chars < 14 and j - i < len(block):
        cost += 1.5 * (14 - chars) / 14  # avoid tiny flashing cues
    if dur < r["min_duration"] and j - i < len(block):
        cost += 4.0 * (r["min_duration"] - dur)  # too short to be read: it would flash
    reading = chars / max(r["max_cps"], 1)
    if reading > dur + r["linger"]:
        cost += (reading - dur - r["linger"]) * 1.5  # would be too fast to read
    # one sentence per cue when possible; never "end of a sentence + start of the next one" in a cue
    inner_ends = sum(1 for w in block[i:j - 1] if ends_sentence(w["text"]))
    if inner_ends:
        cost += 3.0 if not ends_sentence(last) and j < len(block) else (1.4 * inner_ends if chars > 45 else 0.1)
    if chars > r["max_chars"]:  # two lines: a cue whose only possible line break is poor costs more
        text = join_words([w["text"] for w in block[i:j]], lang)
        cost += max(0.0, layout(text, lang, r["max_chars"], r["max_lines"])[1] + 1.5) * 0.6
    return cost


def retime(cues: list[Cue], rules: dict, fps: float | None = None) -> list[Cue]:
    """Durations and gaps: minimum duration, reading-speed linger, chained short gaps, 2-frame minimum gap."""
    r = rules
    gap = max(r["min_gap"], 2 / fps if fps else 0)
    for k, c in enumerate(cues):
        nxt = cues[k + 1].start if k + 1 < len(cues) else None
        limit = (nxt - gap) if nxt is not None else c.end + 3.0
        want = max(c.end, c.start + r["min_duration"], c.start + visible_len(flat(c.text)) / r["max_cps"])
        want = min(want, c.end + r["linger"] + 1.0, c.start + r["max_duration"])
        c.end = max(c.end, min(want, limit))  # linger for reading, up to the next cue
        if nxt is not None and c.end > nxt - gap:
            c.end = max(c.start + 0.1, nxt - gap)  # never overlap
        if nxt is not None and 0 < nxt - c.end < r["close_gap"]:
            c.end = nxt - gap  # chain: no flicker between close cues
        if c.end <= c.start:
            c.end = c.start + min(r["min_duration"], max(0.2, (nxt or c.start + 1) - c.start - gap))
    return cues


def relayout(cues: list[Cue], lang: str, rules: dict | None = None, fps: float | None = None) -> list[Cue]:
    """After an edit / correction / translation: typography, line breaks and reading-speed timing again."""
    r = rules_for(lang, rules)
    out: list[Cue] = []
    for c in cues:
        text = typography(flat(c.text), lang)
        if not fits(text, lang, r["max_chars"], r["max_lines"]) and c.end - c.start >= 2 * r["min_duration"]:
            out.extend(_split_cue(c, text, lang, r))  # a longer translation: two cues rather than 3 lines
            continue
        c.text = break_lines(text, lang, r["max_chars"], r["max_lines"])
        out.append(c)
    cues[:] = out
    return retime(cues, r, fps)


def _split_cue(c: Cue, text: str, lang: str, r: dict) -> list[Cue]:
    from .llm import distribute  # same natural cut as translated sentences
    first, second = distribute(text, [1.0, 1.0], lang)
    if not first or not second:
        c.text = break_lines(text, lang, r["max_chars"], r["max_lines"])
        return [c]
    cut = c.start + (c.end - c.start) * len(first) / max(1, len(first) + len(second))
    words_a = [w for w in c.words if w.get("start", 0) < cut]
    words_b = [w for w in c.words if w.get("start", 0) >= cut]
    a = Cue(c.start, cut - r["min_gap"] / 2, break_lines(first, lang, r["max_chars"], r["max_lines"]), words_a,
            speaker=c.speaker)
    b = Cue(cut + r["min_gap"] / 2, c.end, break_lines(second, lang, r["max_chars"], r["max_lines"]), words_b,
            speaker=c.speaker)
    return [a, b]
