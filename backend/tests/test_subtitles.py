"""Subtitles module: cue building, file formats, word timing, the language-model safety net and the HTTP API.
Speech recognition and the language models themselves need large local models: they are replaced by fakes here."""
import subprocess
import sys
import threading
import time

import pytest

from medialab.subtitles import cues as C
from medialab.subtitles import formats as F
from medialab.subtitles import llm as L


# ------------------------------------------------------------------ helpers
def timed_words(text: str, start: float = 0.5, rate: float = 0.32, pauses: dict | None = None) -> list[dict]:
    """Words spoken at a steady pace (``pauses``: word index -> silence before it)."""
    out, t = [], start
    for i, w in enumerate(text.split()):
        t += (pauses or {}).get(i, 0.0)
        out.append({"text": w, "start": round(t, 3), "end": round(t + rate - 0.04, 3), "p": 0.95})
        t += rate
    return out


FR_TEXT = ("Aujourd'hui, on va aller voir le nouveau film au cinéma avec toute la famille. "
           "Franchement, il est vraiment incroyable et les effets spéciaux sont magnifiques. "
           "Après la séance, nous irons manger une pizza dans le petit restaurant du quartier. "
           "Ensuite, chacun rentrera chez soi pour se reposer avant la semaine qui arrive.")


class FakeLLM:
    """Answers each numbered item through ``fn`` (the real providers return one string per item)."""

    def __init__(self, fn):
        self.fn = fn
        self.cancel = None
        self.calls = 0

    def lines(self, system, items, context, glossary):
        self.calls += 1
        return [self.fn(x) for x in items]


# ------------------------------------------------------------------ cues
def test_build_cues_follows_the_readability_rules():
    words = timed_words(FR_TEXT, pauses={FR_TEXT.split().index("Après"): 1.5})
    cues = C.build_cues(words, "fr")
    assert len(cues) >= 4
    r = C.rules_for("fr")
    for a, b in zip(cues, cues[1:]):
        assert b.start - a.end >= 0.07  # two frames at least between cues
    for c in cues:
        lines = c.text.split("\n")
        assert len(lines) <= r["max_lines"]
        assert all(C.visible_len(ln) <= r["max_chars"] for ln in lines), c.text
        assert r["min_duration"] - 0.01 <= c.end - c.start <= r["max_duration"] + 0.01
        last = C.flat(c.text).split(" ")[-1]
        assert not C.is_weak(last, "fr"), f"cue ends on a weak word: {c.text!r}"
    # every word is kept, in order
    assert " ".join(C.flat(c.text) for c in cues).replace(" ", " ").split() == FR_TEXT.split()


def test_a_long_silence_always_splits():
    words = timed_words("Bonjour à tous. Merci d'être venus ce soir.", pauses={3: 2.0})
    cues = C.build_cues(words, "fr")
    assert all(not (c.start < words[3]["start"] - 1 < c.end) for c in cues)


def test_french_typography():
    t = C.typography("Il a dit : « oui » ! Vraiment ?", "fr")
    assert " :" in t or " :" in t
    assert "« " in t and " »" in t
    assert " !" in t or " !" in t


def test_break_lines_prefers_balanced_natural_cut():
    text = C.break_lines("Le chêne un jour dit au roseau vous avez bien sujet d'accuser la nature", "fr")
    lines = text.split("\n")
    assert len(lines) == 2 and all(len(x) <= 42 for x in lines)
    assert not C.is_weak(lines[0].split(" ")[-1], "fr")


# ------------------------------------------------------------------ formats
CUES = [
    {"id": "a", "start": 1.0, "end": 3.5, "text": "Le chêne et le roseau.", "words": []},
    {"id": "b", "start": 3.6, "end": 7.25, "text": "Le chêne, un jour,\ndit au roseau : « Vous… »", "words": []},
]


def test_srt_vtt_round_trip():
    srt = F.to_srt(CUES)
    assert "00:00:01,000 --> 00:00:03,500" in srt
    vtt = F.to_vtt(CUES, "fr")
    assert vtt.startswith("WEBVTT") and "00:00:03.600 --> 00:00:07.250" in vtt
    for content in (srt, vtt):
        back = F.parse(content)
        assert [(c["start"], c["end"], c["text"]) for c in back] == [(c["start"], c["end"], c["text"]) for c in CUES]


@pytest.mark.parametrize("anim", ["none", "fade", "karaoke", "word", "typewriter"])
@pytest.mark.parametrize("background", ["none", "box"])
def test_ass_export_imports_back(anim, background):
    ass = F.to_ass(CUES, {"animation": anim, "background": background}, 1920, 1080, "fr")
    assert "[Events]" in ass and "PlayResY: 1080" in ass
    back = F.parse(ass)
    assert [c["text"] for c in back] == [c["text"] for c in CUES]  # a box drawn on its own layer is not a 2nd cue
    assert back[1]["start"] == pytest.approx(3.6, abs=0.01)


def test_box_with_timed_animation_draws_one_box_layer():
    ass = F.to_ass(CUES, {"animation": "karaoke", "background": "box"}, 1280, 720, "fr")
    assert "Style: Text," in ass
    dialogue = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert len(dialogue) == 4
    assert dialogue[0].startswith("Dialogue: 0,") and "\\1a&HFF&" in dialogue[0] and "\\kf" not in dialogue[0]
    assert dialogue[1].startswith("Dialogue: 1,") and ",Text," in dialogue[1] and "\\kf" in dialogue[1]


def test_txt_and_times():
    assert F.to_txt(CUES).startswith("Le chêne et le roseau. Le chêne, un jour, dit au roseau")
    assert "[00:00:01" in F.to_txt(CUES, timestamps=True)
    assert F.srt_time(3661.5) == "01:01:01,500"
    assert F.ass_time(3.456) == "0:00:03.46"


def test_word_times_stay_inside_the_cue_and_in_order():
    words = timed_words("le chene et le roseau", start=1.0)
    cue = {"start": 1.0, "end": 3.0, "text": "Le chêne et le roseau.", "words": words}
    t = F.word_times(cue, "fr")
    assert [u for u, _, _ in t] == ["Le", "chêne", "et", "le", "roseau."]
    for (_, a, b), (_, c, _) in zip(t, t[1:]):
        assert 1.0 <= a < b <= c <= 3.0
    assert t[1][1] == pytest.approx(words[1]["start"], abs=0.05)  # matched despite the corrected accent


# ------------------------------------------------------------------ language-model safety net
@pytest.mark.parametrize("original,corrected,ok", [
    ("euh bah du coup aujourd'hui on va aller voir le nouveau film et franchement il est vraiment incroyable",
     "Aujourd'hui, on va aller voir le nouveau film et franchement, il est vraiment incroyable.", True),
    ("la cigale ayant chanter tout l'été", "La cigale, ayant chanté tout l'été,", True),
    ("je je pense que le le film est bien", "Je pense que le film est bien.", True),
    ("on va on va y aller", "On va y aller.", True),
    ("un roc au Caucase pareil", "Un roc pareil au Caucase.", False),  # words moved
    ("Le moindre vent fait rider la face de l'eau. La suite arrive", "La suite arrive.", False),  # sentence lost
    ("il pleut", "Il pleut des cordes sur toute la ville depuis ce matin.", False),  # content added
    ("bonjour", "", False),
])
def test_safe_polish(original, corrected, ok):
    assert L.safe_polish(original, corrected, "fr") is ok


def test_guard_quotes_removes_invented_quotation_marks():
    assert L.guard_quotes("Il dit bonjour.", "« Il dit bonjour. »") == "Il dit bonjour."
    assert L.guard_quotes("« Il dit bonjour. »", "« Il dit bonjour. »") == "« Il dit bonjour. »"


def test_parse_keyed_answer_and_loop_detector():
    assert L._parse({"1": "a", "2": "b"}, 3) == ["a", "b", ""]
    assert L._parse({"lines": ["a", "b"]}, 2) == ["a", "b"]
    with pytest.raises(ValueError):
        L._parse(["a"], 1)
    assert L._looping("abc" + "je pense que " * 40)
    assert L._looping("x" * 300)
    assert not L._looping(FR_TEXT)


def test_realign_keeps_words_in_their_cues():
    parts = ["euh le chêne un jour", "dit au roseau vous avez", "bien sujet"]
    out = L.realign(parts, "Le chêne, un jour, dit au roseau : « Vous avez bien sujet")
    assert out == ["Le chêne, un jour,", "dit au roseau : « Vous avez", "bien sujet"]
    assert L.realign(["euh", "bonjour"], "Bonjour") == ["", "Bonjour"]


def test_distribute_cuts_at_natural_points():
    parts = L.distribute("The oak said one day to the reed, you have good reason to blame nature.", [2.0, 2.0], "en")
    assert len(parts) == 2 and " ".join(parts).split() == \
        "The oak said one day to the reed, you have good reason to blame nature.".split()
    assert parts[0].endswith("reed,")


def test_polish_with_fake_model_keeps_timing_and_rejects_rewrites():
    texts = ["euh le chêne un jour", "dit au roseau.", "un roc au Caucase pareil."]

    def fix(s):
        if "Caucase" in s:
            return "Un roc pareil au Caucase."  # reordered: must be refused
        return "Le chêne, un jour, dit au roseau."
    out, rejected = L.polish(FakeLLM(fix), texts, "fr", "", threading.Event())
    assert out[0] == "Le chêne, un jour," and out[1] == "dit au roseau."
    assert out[2] == texts[2] and rejected == 1


def test_run_batches_splits_a_shifted_answer():
    items = [f"phrase numéro {i} du texte." for i in range(10)]

    class Shifting(FakeLLM):
        def lines(self, system, batch, context, glossary):
            self.calls += 1
            if len(batch) > 3:  # a merged answer: every line lands on the wrong item
                return batch[1:] + [""]
            return [b.capitalize() for b in batch]
    llm = Shifting(None)
    out, rejected = L.run_batches(llm, "sys", items, "", threading.Event(), check=L.safe_polish, lang="fr")
    assert out == [x.capitalize() for x in items] and rejected == 0 and llm.calls > 1


def test_translate_spreads_sentences_over_cues():
    texts = ["Le chêne un jour", "dit au roseau.", "Fin."]
    tr = {"Le chêne un jour dit au roseau.": "One day the oak said to the reed.", "Fin.": "The end."}
    out, failed = L.translate(FakeLLM(lambda s: tr[s]), texts, "fr", "en", "", threading.Event(),
                              weights=[1.2, 1.0, 0.5])
    assert failed == 0 and out[2] == "The end."
    assert " ".join(out[:2]).split() == "One day the oak said to the reed.".split() and all(out[:2])


def test_cancel_stops_batches():
    ev = threading.Event()
    ev.set()
    with pytest.raises(L.Canceled):
        L.run_batches(FakeLLM(str), "sys", ["a"], "", ev)


# ------------------------------------------------------------------ Claude key (Windows DPAPI)
@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is Windows only")
def test_claude_key_is_stored_encrypted(data_dir):
    from medialab.subtitles import secret
    secret.set_key("sk-ant-unit-test-key-1234")
    assert secret.get_key() == "sk-ant-unit-test-key-1234"
    assert b"unit-test" not in (data_dir / "secrets" / "claude.key").read_bytes()
    assert secret.status() == {"configured": True, "hint": "…1234"}
    secret.set_key("")
    assert secret.status()["configured"] is False


# ------------------------------------------------------------------ HTTP API
TOKEN = "subs-token"
H = {"X-MediaLab-Token": TOKEN}


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    from conftest import FFMPEG
    if not FFMPEG:
        pytest.skip("FFmpeg not available")
    from fastapi.testclient import TestClient

    from medialab.app import Services, create_app
    d = tmp_path_factory.mktemp("subs-media")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=6",
                    "-f", "lavfi", "-i", "sine=duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(d / "clip.mp4")], check=True)
    services = Services(token=TOKEN)
    services.settings.update({"advanced": {"ffmpeg_path": FFMPEG}})
    with TestClient(create_app(services)) as c:
        yield c, d


def _wait(c, job_id, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/api/jobs/{job_id}", headers=H).json()
        if j["status"] in ("completed", "failed", "canceled"):
            return j
        time.sleep(0.2)
    raise AssertionError("job stuck")


def test_api_project_edit_export_import_burn(api):
    c, d = api
    cat = c.get("/api/subtitles/catalog", headers=H).json()
    assert len(cat["languages"]) > 90 and len(cat["presets"]) == 9 and "claude" in cat["engines"]
    p = c.post("/api/subtitles/projects", headers=H, json={"path": str(d / "clip.mp4")}).json()
    pid = p["id"]
    assert p["video"]["width"] == 320 and p["video"]["height"] == 240 and p["video"]["duration"] == pytest.approx(6, 0.1)
    assert any(x["id"] == pid for x in c.get("/api/subtitles/projects", headers=H).json())

    cues = [{"id": "x1", "start": 0.5, "end": 2.5, "text": "Bonjour à tous"},
            {"id": "x2", "start": 2.6, "end": 4.0, "text": "  et bienvenue  "},
            {"id": "x3", "start": 4.5, "end": 4.4, "text": ""}]
    saved = c.put(f"/api/subtitles/projects/{pid}", headers=H,
                  json={"cues": cues, "style": {"preset": "youtube", "animation": "karaoke"}}).json()
    # the editor's state is kept as sent (a cue just added is still empty while it is being typed)...
    assert len(saved["cues"]) == 3 and saved["cues"][2]["end"] > saved["cues"][2]["start"]
    assert c.get(f"/api/subtitles/projects/{pid}", headers=H).json()["style"]["animation"] == "karaoke"

    out = d / "out"
    for fmt in ("srt", "vtt", "ass", "txt"):
        r = c.post(f"/api/subtitles/projects/{pid}/export", headers=H, json={"format": fmt, "folder": str(out)})
        assert r.status_code == 200, r.text
        f = r.json()["path"]
        assert f.endswith("." + fmt)
        if fmt != "txt":
            back = c.post(f"/api/subtitles/projects/{pid}/import", headers=H, json={"path": f}).json()
            # ...and files only hold real subtitles, trimmed
            assert [x["text"] for x in back["cues"]] == ["Bonjour à tous", "et bienvenue"], fmt
    bad = c.post(f"/api/subtitles/projects/{pid}/export", headers=H, json={"format": "doc"})
    assert bad.status_code >= 400

    job = c.post(f"/api/subtitles/projects/{pid}/burn", headers=H, json={"output_dir": str(out)}).json()["jobs"][0]
    j = _wait(c, job["id"])
    assert j["status"] == "completed", j.get("error")
    from conftest import FFMPEG
    from pathlib import Path
    probe = subprocess.run([str(Path(FFMPEG).with_name("ffprobe" + Path(FFMPEG).suffix)), "-v", "error",
                            "-show_entries", "stream=codec_type,width,height", "-of", "compact",
                            j["result"]["file_path"]], capture_output=True, text=True).stdout
    assert "width=320|height=240" in probe and "codec_type=audio" in probe

    assert c.post("/api/subtitles/projects", headers=H, json={"path": str(d / "nope.mp4")}).status_code >= 400
    assert c.put("/api/subtitles/claude-key", headers=H, json={"key": "not-a-key"}).status_code >= 400
    assert c.delete(f"/api/subtitles/projects/{pid}", headers=H).json() == {"ok": True}
    assert c.get(f"/api/subtitles/projects/{pid}", headers=H).status_code >= 400
