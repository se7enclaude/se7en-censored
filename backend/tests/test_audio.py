"""Audio tools: voice / music separation and sound cleaning, measured on known signals.

The voice is Windows speech synthesis (tests/data/speech_tts.wav); the "music" and the noise are generated
here, so every result can be compared with the exact original parts.
"""
import subprocess
import threading
import wave
from pathlib import Path

import numpy as np
import pytest

from medialab.tools import audio

DATA = Path(__file__).parent / "data"
SR = 44100


def _write(path: Path, x: np.ndarray, rate: int = SR) -> Path:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[0])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.T.tobytes())
    return path


def _read(path: str | Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
        return a.reshape(-1, w.getnchannels()).T, w.getframerate()


def _voice(ffmpeg: str, tmp: Path) -> np.ndarray:
    out = tmp / "voice44.wav"
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(DATA / "speech_tts.wav"), "-ar", str(SR), "-ac", "2", str(out)],
                   check=True)
    return _read(out)[0]


def _music(n: int) -> np.ndarray:
    t = np.arange(n) / SR
    chords = sum(np.sin(2 * np.pi * f * t) + 0.5 * np.sin(4 * np.pi * f * t) for f in (220, 261.63, 329.63))
    beat = np.mod(t, 0.5)
    kick = np.sin(2 * np.pi * (50 + 60 * np.exp(-30 * beat)) * t) * np.exp(-12 * beat)
    m = 0.1 * chords * (0.7 + 0.3 * np.sin(np.pi * t)) + 0.55 * kick
    return np.stack([m, m]).astype(np.float32)


def _sdr(ref: np.ndarray, est: np.ndarray) -> float:
    n = min(ref.shape[1], est.shape[1])
    return float(10 * np.log10(np.sum(ref[:, :n] ** 2) / max(np.sum((ref[:, :n] - est[:, :n]) ** 2), 1e-12)))


def _noise_in_pauses(clean_voice: np.ndarray, est: np.ndarray) -> float:
    k = 2048
    env = np.abs(clean_voice).mean(0)
    m = est.mean(0)
    quiet = [i for i in range(0, min(len(env), len(m)) - k, k) if env[i:i + k].max() < 0.01]
    return float(10 * np.log10(np.mean([np.mean(m[i:i + k] ** 2) for i in quiet]) + 1e-12))


def _noop(*_a) -> None:
    pass


def test_spectrogram_round_trip_is_transparent():
    """STFT -> iSTFT without a model gives the input back (only the top frequency bin is dropped)."""
    rng = np.random.default_rng(1)
    x = (rng.standard_normal((2, audio.CHUNK)) * 0.1).astype(np.float32)
    y = audio._istft(audio._stft(x))
    assert y.shape == x.shape
    assert _sdr(x, y) > 30


def test_filter_chain_order_and_options():
    chain = audio._filters({"hum": 50, "declick": True, "normalize": True}, denoise="voice-light")
    names = [c.split("=")[0] for c in chain]
    assert names == ["bandreject", "bandreject", "bandreject", "highpass", "adeclick", "arnndn", "loudnorm"]
    assert "f=100" in chain[1] and "mix=0.75" in chain[5]
    assert audio._filters({}, denoise="other-strong") == ["afftdn=nr=22:nf=-50:tn=1"]
    assert audio._rate(["-c:a", "libopus"], 44100) == ["-ar", "48000"]
    assert audio._rate(["-c:a", "flac"], 22050) == ["-ar", "22050"]


def test_separation_splits_voice_and_music(ffmpeg_bin, ai_models, tmp_path):
    voice = _voice(ffmpeg_bin, tmp_path)
    mix = voice + _music(voice.shape[1])
    src = _write(tmp_path / "chanson.wav", mix)
    res = audio.separate(src, {"stems": "both", "format": "wav"}, threading.Event(), _noop, french=True)
    vocals_path, inst_path = map(Path, res["meta"]["files"])
    assert vocals_path.name == "chanson (voix).wav" and inst_path.name == "chanson (instrumental).wav"
    vocals, rate = _read(vocals_path)
    inst, _ = _read(inst_path)
    assert rate == SR and vocals.shape == mix.shape  # nothing lost or added at the edges
    assert _sdr(voice, vocals) > 8  # the mix itself scores about -5 dB as "the voice"
    assert _sdr(mix - voice, inst) > 12
    assert _sdr(mix, vocals + inst) > 40  # voice + instrumental = the original


def test_voice_cleaning_removes_background_noise(ffmpeg_bin, ai_models, tmp_path):
    voice = _voice(ffmpeg_bin, tmp_path)
    rng = np.random.default_rng(7)
    noise = np.repeat(rng.standard_normal((1, voice.shape[1])).astype(np.float32) * 0.03, 2, 0)
    hum = 0.05 * np.sin(2 * np.pi * 50 * np.arange(voice.shape[1]) / SR)
    src = _write(tmp_path / "vocal.wav", voice + noise + hum)
    before = _noise_in_pauses(voice, voice + noise + hum)
    for strength, gain in (("medium", 12), ("strong", 8)):
        res = audio.clean(src, {"sound": "voice", "strength": strength, "hum": 50, "format": "wav"},
                          threading.Event(), _noop, french=True)
        out, rate = _read(res["file_path"])
        assert Path(res["file_path"]).name.startswith("vocal (nettoyé)")
        assert rate == SR, "the source sample rate is kept (RNNoise runs at 48 kHz internally)"
        assert _noise_in_pauses(voice, out) < before - gain, strength


def test_cleaning_a_video_keeps_the_picture(ffmpeg_bin, ai_models, tmp_path):
    voice = _voice(ffmpeg_bin, tmp_path)
    wav = _write(tmp_path / "v.wav", voice[:, :SR * 3])
    clip = tmp_path / "clip.mp4"
    subprocess.run([ffmpeg_bin, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25", "-i", str(wav),
                    "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
                    str(clip)], check=True)
    res = audio.separate(clip, {"stems": "instrumental"}, threading.Event(), _noop, french=False)
    out = Path(res["file_path"])
    assert out.name == "clip (instrumental).mp4"
    probe = subprocess.run([str(Path(ffmpeg_bin).with_name("ffprobe.exe")), "-v", "error", "-show_entries",
                            "stream=codec_type", "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.split()
    assert probe.count("video") == 1 and probe.count("audio") == 1
    again = audio.separate(clip, {"stems": "instrumental"}, threading.Event(), _noop, french=False)
    assert Path(again["file_path"]).name == "clip (instrumental) (1).mp4"  # never overwrites


def test_cancel_leaves_no_partial_file(ffmpeg_bin, ai_models, tmp_path):
    voice = _voice(ffmpeg_bin, tmp_path)
    src = _write(tmp_path / "long.wav", np.concatenate([voice] * 3, 1))
    cancel = threading.Event()

    def progress(frac, *_a):
        if frac > 0:
            cancel.set()

    with pytest.raises(Exception):
        audio.separate(src, {"stems": "both", "format": "flac"}, cancel, progress)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["long.wav", "voice44.wav"]
