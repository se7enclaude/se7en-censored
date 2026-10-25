"""Converter, cutter, player support, duplicate finder and statistics on real (generated) media."""
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from medialab.errors import Canceled, MediaLabError
from medialab.media import ffmpeg
from medialab.tools import convert as conv
from medialab.tools import playback, trim


def _gen(ff: str, out: Path, *args: str) -> Path:
    subprocess.run([ff, "-hide_banner", "-v", "error", "-y", *args, str(out)], check=True, capture_output=True)
    return out


@pytest.fixture(scope="module")
def media(tmp_path_factory, request):
    from conftest import FFMPEG
    if not FFMPEG:
        pytest.skip("FFmpeg not available")
    from medialab import components
    components.set_overrides(ffmpeg=FFMPEG)
    d = tmp_path_factory.mktemp("media")
    ff = FFMPEG
    m = {
        "mkv": _gen(ff, d / "clip.mkv", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=25:duration=4",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-g", "25",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"),
        "odd": _gen(ff, d / "odd.webm", "-f", "lavfi", "-i", "testsrc2=size=321x241:rate=25:duration=2",
                    "-c:v", "libvpx-vp9", "-b:v", "300k"),
        "flac": _gen(ff, d / "tone.flac", "-f", "lavfi", "-i", "sine=frequency=330:duration=5"),
        "wma": _gen(ff, d / "tone.wma", "-f", "lavfi", "-i", "sine=frequency=550:duration=3", "-c:a", "wmav2"),
        "srt": d / "subs.srt",
    }
    m["srt"].write_text("1\n00:00:00,500 --> 00:00:02,000\nHello se7en\n\n2\n00:00:02,500 --> 00:00:03,500\nBye\n",
                        "utf-8")
    im = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
    for x in range(300):
        for y in range(60, 140):
            im.putpixel((x, y), (x % 255, 80, 200, 255))
    im.save(d / "alpha.png")
    m["png"] = d / "alpha.png"
    frames = [Image.new("RGB", (64, 48), (i * 40, 20, 200 - i * 30)) for i in range(5)]
    frames[0].save(d / "anim.gif", save_all=True, append_images=frames[1:], duration=100, loop=0)
    m["gif"] = d / "anim.gif"
    m["dir"] = d
    return m


def _run(src, target, opts=None, cancel=None):
    return conv.convert(Path(src), target, opts or {}, cancel or threading.Event(), lambda *_: None)


# ------------------------------------------------------------------ converter
def test_mkv_to_mp4_is_an_instant_lossless_remux(media):
    res = _run(media["mkv"], "mp4")
    out = ffmpeg.probe(res["file_path"])
    assert res["meta"]["remux"] is True
    assert out["vcodec"] == "h264" and out["audio"][0]["codec"] == "aac"
    assert abs(out["duration"] - 4) < 0.2
    assert Path(res["file_path"]).suffix == ".mp4"


def test_video_to_mp3_keeps_only_the_sound(media):
    res = _run(media["mkv"], "mp3", {"audio_bitrate": 192})
    out = ffmpeg.probe(res["file_path"])
    assert not out["has_video"] and out["audio"][0]["codec"] == "mp3"


def test_video_to_m4a_copies_the_original_aac(media):
    res = _run(media["mkv"], "m4a")
    assert res["meta"]["remux"] is True
    assert ffmpeg.probe(res["file_path"])["audio"][0]["codec"] == "aac"


def test_webm_and_odd_dimensions(media):
    res = _run(media["odd"], "mp4", {"quality": "normal"})
    out = ffmpeg.probe(res["file_path"])
    assert out["vcodec"] == "h264" and out["width"] % 2 == 0 and out["height"] % 2 == 0
    res = _run(media["mkv"], "webm", {"quality": "small"})
    out = ffmpeg.probe(res["file_path"])
    assert out["vcodec"] == "vp9" and out["audio"][0]["codec"] == "opus"


def test_resolution_is_never_upscaled_and_downscale_keeps_aspect(media):
    res = _run(media["mkv"], "mp4", {"resolution": 144})
    out = ffmpeg.probe(res["file_path"])
    assert out["height"] == 144 and out["width"] == 192
    res = _run(media["mkv"], "mp4", {"resolution": 2160, "quality": "normal"})
    assert ffmpeg.probe(res["file_path"])["height"] == 240


def test_target_size_is_respected(media):
    res = _run(media["mkv"], "mp4", {"target_size_mb": 0.15})
    assert res["size"] <= 0.15 * 1_000_000


def test_target_size_too_small_is_explained(media):
    with pytest.raises(MediaLabError) as e:
        _run(media["mkv"], "mp4", {"target_size_mb": 0.005})
    assert e.value.code == "target_too_small"


def test_gif_and_gif_to_mp4(media):
    res = _run(media["mkv"], "gif", {"gif_width": 160, "fps": 10})
    with Image.open(res["file_path"]) as im:
        assert im.format == "GIF" and getattr(im, "n_frames", 1) > 10
    res = _run(media["gif"], "mp4")
    out = ffmpeg.probe(res["file_path"])
    assert out["vcodec"] == "h264" and out["frames"] >= 4


def test_audio_formats(media):
    for target, codec in [("wav", "pcm_s16le"), ("opus", "opus"), ("ogg", "vorbis"), ("alac", "alac"),
                          ("flac", "flac"), ("aiff", "pcm_s16be"), ("wma", "wmav2")]:
        res = _run(media["flac"], target)
        assert ffmpeg.probe(res["file_path"])["audio"][0]["codec"] == codec, target


def test_loudness_normalisation_and_mono(media):
    res = _run(media["flac"], "mp3", {"normalize": True, "channels": 1, "sample_rate": 44100})
    a = ffmpeg.probe(res["file_path"])["audio"][0]
    assert a["channels"] == 1 and a["sample_rate"] == "44100"


def test_image_formats_open_correctly(media):
    for target, fmt_name in [("jpg", "JPEG"), ("webp", "WEBP"), ("avif", "AVIF"), ("bmp", "BMP"), ("tiff", "TIFF"),
                             ("ico", "ICO"), ("gif-image", "GIF"), ("png", "PNG")]:
        res = _run(media["png"], target, {"image_quality": 85})
        with Image.open(res["file_path"]) as im:
            assert im.format == fmt_name, target
            if target == "jpg":
                assert im.mode == "RGB" and im.getpixel((5, 5)) == (255, 255, 255)  # transparency -> white
    res = _run(media["png"], "pdf")
    assert Path(res["file_path"]).read_bytes()[:4] == b"%PDF"
    res = _run(media["png"], "webp", {"max_size": 150})
    with Image.open(res["file_path"]) as im:
        assert max(im.size) == 150


def test_heic_roundtrip(media):
    pytest.importorskip("pillow_heif")
    res = _run(media["png"], "heic")
    res2 = _run(res["file_path"], "jpg")
    with Image.open(res2["file_path"]) as im:
        assert im.size == (300, 200)


def test_animated_gif_to_animated_webp(media):
    res = _run(media["gif"], "webp")
    with Image.open(res["file_path"]) as im:
        assert getattr(im, "n_frames", 1) == 5


def test_subtitles(media):
    res = _run(media["srt"], "vtt")
    assert Path(res["file_path"]).read_text("utf-8").startswith("WEBVTT")


def test_incompatible_target_is_refused(media):
    with pytest.raises(MediaLabError) as e:
        _run(media["flac"], "mp4")
    assert e.value.code in ("incompatible_target", "no_video_stream")
    with pytest.raises(MediaLabError):
        _run(media["png"], "mp3")


def test_outputs_never_overwrite(media):
    a = _run(media["mkv"], "mkv")
    b = _run(media["mkv"], "mkv")
    assert a["file_path"] != b["file_path"] and Path(a["file_path"]).exists() and Path(b["file_path"]).exists()
    assert Path(media["mkv"]).exists()  # source untouched (mkv -> mkv never replaces it)


def test_cancel_leaves_no_partial_file(media):
    cancel = threading.Event()
    cancel.set()
    before = set(media["dir"].iterdir())
    with pytest.raises(Canceled):
        _run(media["mkv"], "webm", {"quality": "high"}, cancel)
    after = set(media["dir"].iterdir())
    assert not [p for p in after - before if ".se7en-part" in p.name]


# ------------------------------------------------------------------ cutter
def test_segments_are_normalised_and_inverted():
    segs = [{"start": 5, "end": 8}, {"start": 1, "end": 3}, {"start": 2.5, "end": 4}, {"start": 9, "end": 9.01}]
    assert trim.normalize_segments(segs, 10) == [(1, 4), (5, 8)]
    assert trim.normalize_segments(segs, 10, remove=True) == [(0.0, 1), (4, 5), (8, 10)]
    assert trim.normalize_segments([{"start": -3, "end": 50}], 10) == [(0.0, 10)]


def _cut(src, segments, **opts):
    return trim.trim(Path(src), segments, opts, threading.Event(), lambda *_: None, french=True)


def test_precise_cut_is_frame_accurate(media):
    res = _cut(media["mkv"], [{"start": 1.3, "end": 2.9}], mode="precise")
    out = ffmpeg.probe(res["file_path"])
    assert abs(out["duration"] - 1.6) < 0.12
    assert out["has_video"] and out["has_audio"]
    assert "(extrait)" in Path(res["file_path"]).name


def test_fast_cut_merges_parts(media):
    res = _cut(media["mkv"], [{"start": 0, "end": 1}, {"start": 2, "end": 3}], mode="fast", output="merge")
    out = ffmpeg.probe(res["file_path"])
    assert 1.8 < out["duration"] < 2.8  # keyframe-snapped: no extra group of pictures
    assert not res["extra_files"]


def test_separate_parts_and_remove_mode(media):
    res = _cut(media["flac"], [{"start": 1, "end": 2}], remove=True, output="separate")
    files = [res["file_path"], *res["extra_files"]]
    assert len(files) == 2
    durs = sorted(round(ffmpeg.probe(f)["duration"], 1) for f in files)
    assert durs == [1.0, 3.0]


def test_audio_fade(media):
    res = _cut(media["flac"], [{"start": 0.5, "end": 3.5}], fade=0.5)
    assert abs(ffmpeg.probe(res["file_path"])["duration"] - 3.0) < 0.05


def test_cutting_nothing_is_refused(media):
    with pytest.raises(MediaLabError) as e:
        _cut(media["flac"], [{"start": 0, "end": 99}])
    assert e.value.code == "no_change"


# ------------------------------------------------------------------ player
def test_playback_plans(media):
    mp4 = _run(media["mkv"], "mp4")["file_path"]
    assert playback.plan(Path(mp4))["mode"] == "direct"
    assert playback.plan(Path(media["mkv"]))["mode"] == "remux"
    assert playback.plan(Path(media["flac"]))["mode"] == "direct"
    assert playback.plan(Path(media["wma"]))["mode"] == "transcode"
    assert playback.plan(Path(media["png"]))["mode"] == "image"


def test_streams_produce_playable_bytes(media):
    for path, mode, magic in [(media["mkv"], "remux", b"ftyp"), (media["mkv"], "transcode", b"ftyp"),
                              (media["wma"], "transcode", None)]:
        gen, mime = playback.open_stream(Path(path), mode, 0.5, sid=f"t-{mode}")
        first = b""
        for chunk in gen:
            first += chunk
            if len(first) > 2000:
                break
        gen.close()
        assert len(first) > 500, (path, mode)
        if magic:
            assert magic in first[:64] and mime == "video/mp4"
        else:
            assert mime == "audio/mpeg"


def test_waveform_and_filmstrip(media):
    w = playback.waveform(Path(media["flac"]), 400)
    assert 390 <= w["buckets"] <= 410 and max(w["peaks"]) == 1.0
    f = playback.filmstrip(Path(media["mkv"]), 6, 48)
    assert len(f["frames"]) == 6


# ------------------------------------------------------------------ duplicates
def test_duplicate_finder(media, tmp_path):
    from medialab.db import Database
    from medialab.tools import duplicates, tasks

    folder = tmp_path / "lib"
    (folder / "sub").mkdir(parents=True)
    src = Image.effect_mandelbrot((640, 480), (-2, -1.2, 1, 1.2), 60).convert("RGB")
    src.save(folder / "photo.png")
    shutil.copy(folder / "photo.png", folder / "sub" / "photo (1).png")  # exact copy
    src.resize((320, 240)).save(folder / "photo-small.jpg", quality=80)  # same picture, smaller
    Image.effect_noise((640, 480), 80).convert("RGB").save(folder / "other.png")
    shutil.copy(media["mkv"], folder / "clip.mkv")
    shutil.copy(media["mkv"], folder / "clip copy.mkv")
    db = Database(tmp_path / "t.db")
    task = tasks.Task("duplicates")
    res = duplicates.scan(db, [str(folder)], {"min_size_kb": 1}, task)
    exact = [g for g in res["groups"] if g["type"] == "exact"]
    similar = [g for g in res["groups"] if g["type"] == "similar"]
    assert len(exact) == 2
    photo = next(g for g in exact if g["kind"] == "image")
    assert [f["keep"] for f in photo["files"]].count(True) == 1
    assert next(f for f in photo["files"] if f["keep"])["name"] == "photo.png"  # the original name is kept
    assert any(any(f["name"] == "photo-small.jpg" for f in g["files"]) for g in similar)
    assert not any(f["name"] == "other.png" for g in res["groups"] for f in g["files"])
    # second scan uses the cache
    t0 = time.perf_counter()
    duplicates.scan(db, [str(folder)], {"min_size_kb": 1}, tasks.Task("duplicates"))
    assert time.perf_counter() - t0 < 5


# ------------------------------------------------------------------ statistics
def test_statistics(tmp_path):
    from medialab.db import Database
    from medialab.tools import stats

    db = Database(tmp_path / "s.db")
    from datetime import datetime, timedelta

    now = time.time()
    today = datetime.fromtimestamp(now)
    for i in range(5):
        # calendar days, not 86400 s: a 25-hour day (end of summer time) would put two entries on the same date
        at = (today - timedelta(days=i)).timestamp()
        db.add_history({"kind": "download", "title": f"v{i}", "platform": "youtube", "size": 1000 * (i + 1),
                        "duration": 60, "format": "MP4 · H264", "resolution": "1080p", "created_at": at,
                        "meta": {"platform_name": "YouTube", "uploader": "Chan", "elapsed": 2}})
    db.add_history({"kind": "upscale", "title": "img", "size": 5000, "created_at": now, "meta": {"elapsed": 3}})
    s = stats.compute(db, 30, check_files=False)
    assert s["total"] == 6 and s["by_kind"]["download"]["count"] == 5
    assert s["platforms"][0]["name"] == "YouTube" and s["platforms"][0]["count"] == 5
    assert s["streak"] == 5 and len(s["timeline"]) == 30
    assert s["by_kind"]["upscale"]["seconds"] == 3
    assert s["biggest"]["size"] == 5000


def test_every_file_dialog_filter_is_accepted_by_pywebview():
    """A label with "&" once made the native "Add files" dialog fail silently (AI and Cut tabs)."""
    from webview.util import parse_file_type
    from medialab import system
    for kind in ("media", "video", "audio", "image", "av", "any", "playable", "cookies", "exe", "unknown"):
        for t in system.file_types(kind):
            parse_file_type(t)  # raises ValueError when pywebview would refuse it
