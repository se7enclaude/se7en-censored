from medialab.downloader import formats as fmt

DUR = 222.0


def yt_like():
    """A realistic subset of a YouTube format list (DASH video-only + audio-only + progressive)."""
    v = []
    for fid, h, codec, tbr, fps, size in [
        ("160", 144, "avc1.4d400c", 80, 30, 2_000_000),
        ("134", 360, "avc1.4d401e", 300, 30, 8_000_000),
        ("135", 480, "avc1.4d401f", 600, 30, 16_000_000),
        ("136", 720, "avc1.4d401f", 1200, 30, 33_000_000),
        ("247", 720, "vp9", 1000, 30, 28_000_000),
        ("137", 1080, "avc1.640028", 2500, 30, 42_000_000),
        ("248", 1080, "vp9", 2000, 30, 38_000_000),
        ("399", 1080, "av01.0.08M.08", 1800, 30, 35_000_000),
        ("271", 1440, "vp9", 6000, 30, 78_000_000),
        ("313", 2160, "vp9", 12000, 30, 135_000_000),
        ("401", 2160, "av01.0.12M.08", 10000, 30, 120_000_000),
    ]:
        v.append({"format_id": fid, "height": h, "width": h * 16 // 9, "vcodec": codec, "acodec": "none",
                  "tbr": tbr, "fps": fps, "filesize": size, "ext": "mp4" if codec.startswith(("avc", "av01")) else "webm",
                  "protocol": "https"})
    a = [
        {"format_id": "140", "vcodec": "none", "acodec": "mp4a.40.2", "abr": 129, "ext": "m4a", "filesize": 3_600_000, "protocol": "https"},
        {"format_id": "251", "vcodec": "none", "acodec": "opus", "abr": 135, "ext": "webm", "filesize": 3_800_000, "protocol": "https"},
        {"format_id": "249", "vcodec": "none", "acodec": "opus", "abr": 50, "ext": "webm", "filesize": 1_300_000, "protocol": "https"},
    ]
    sb = [{"format_id": "sb0", "format_note": "storyboard", "ext": "mhtml", "vcodec": "none", "acodec": "none"}]
    prog = [{"format_id": "18", "height": 360, "width": 640, "vcodec": "avc1.42001E", "acodec": "mp4a.40.2",
             "tbr": 500, "ext": "mp4", "protocol": "https"}]
    return {"duration": DUR, "formats": v + a + sb + prog}


def test_summary_only_lists_existing_qualities():
    s = fmt.summarize(yt_like())
    heights = [q["height"] for q in s["qualities"]]
    assert heights == [2160, 1440, 1080, 720, 480, 360, 144]
    assert 240 not in heights  # not offered by the source → never shown
    q1080 = next(q for q in s["qualities"] if q["height"] == 1080)
    assert set(q1080["codecs"]) == {"h264", "vp9", "av1"}
    assert q1080["size"] and q1080["size"] > 35_000_000
    assert s["audio_codecs"] == ["aac", "opus"]


def test_select_mp4_auto_prefers_h264_and_m4a():
    sel = fmt.select(yt_like()["formats"], mode="av", quality="1080", container="mp4")
    assert sel["spec"] == "137+140"
    assert not sel["needs_reencode"]


def test_select_best_4k_mp4_original_prefers_vp9_over_av1():
    # Windows ships the VP9 decoder; AV1 needs an optional Store extension.
    sel = fmt.select(yt_like()["formats"], mode="av", quality="best", container="mp4")
    assert sel["video"]["format_id"] == "313"
    assert not sel["needs_reencode"]


def test_compat_4k_is_converted_to_h264_because_youtube_has_no_h264_4k():
    sel = fmt.select(yt_like()["formats"], mode="av", quality="2160", container="mp4", vcodec="compat")
    assert sel["height"] == 2160
    assert sel["needs_reencode"] and sel["reencode_to"] == "h264" and sel["reencode_reason"] == "compat"
    assert sel["audio"]["format_id"] == "140"  # AAC: plays everywhere
    assert sel["spec"] == f"{sel['video']['format_id']}+140"


def test_compat_1080_keeps_native_h264_without_conversion():
    sel = fmt.select(yt_like()["formats"], mode="av", quality="1080", container="mp4", vcodec="compat")
    assert sel["spec"] == "137+140"
    assert not sel["needs_reencode"]


def test_compat_never_picks_opus_in_mp4_when_aac_exists():
    f = yt_like()["formats"]
    for a in f:
        if a.get("format_id") == "251":
            a["abr"] = 300  # much better opus: compat still takes AAC
    sel = fmt.select(f, mode="av", quality="720", container="mp4", vcodec="compat")
    assert sel["audio"]["format_id"] == "140"


def test_select_webm_prefers_vp9_opus():
    sel = fmt.select(yt_like()["formats"], mode="av", quality="1440", container="webm")
    assert sel["spec"] == "271+251"


def test_requested_codec_missing_flags_reencode():
    sel = fmt.select(yt_like()["formats"], mode="av", quality="2160", container="mp4", vcodec="h264")
    assert sel["needs_reencode"] is True
    assert sel["height"] == 2160


def test_quality_above_source_falls_back_to_highest_below():
    f = [x for x in yt_like()["formats"] if (x.get("height") or 0) <= 720]
    sel = fmt.select(f, mode="av", quality="2160", container="mp4")
    assert sel["height"] == 720


def test_audio_only_prefers_matching_codec():
    sel = fmt.select(yt_like()["formats"], mode="audio", audio_format="m4a")
    assert sel["spec"] == "140"
    sel = fmt.select(yt_like()["formats"], mode="audio", audio_format="best")
    assert sel["spec"] in ("251", "140")


def test_video_only_single_stream():
    sel = fmt.select(yt_like()["formats"], mode="video", quality="720", container="mp4")
    assert sel["spec"] == "136"


def test_portrait_and_letterbox_labels():
    assert fmt.quality_height({"width": 1080, "height": 1920}) == 1080
    assert fmt.quality_height({"width": 1920, "height": 800}) == 1080
    assert fmt.quality_height({"width": 1440, "height": 1080}) == 1080


def test_combined_only_source():
    info = {"duration": 30, "formats": [
        {"format_id": "h264_540", "height": 1024, "width": 576, "vcodec": "h264", "acodec": "aac", "ext": "mp4", "tbr": 1200},
        {"format_id": "h264_720", "height": 1280, "width": 720, "vcodec": "h264", "acodec": "aac", "ext": "mp4", "tbr": 2000},
    ]}
    s = fmt.summarize(info)
    assert [q["height"] for q in s["qualities"]] == [720, 576] or s["qualities"][0]["height"] == 720
    assert s["has_audio"]
    sel = fmt.select(info["formats"], mode="av", quality="best", container="mp4")
    assert sel["spec"] == "h264_720"


def test_drm_formats_are_never_selected():
    info = yt_like()
    info["formats"].append({"format_id": "drm4k", "height": 2160, "width": 3840, "vcodec": "avc1", "acodec": "none",
                            "has_drm": True, "tbr": 99999, "protocol": "https"})
    sel = fmt.select(info["formats"], mode="av", quality="2160", container="mp4", vcodec="h264")
    assert sel["video"]["format_id"] != "drm4k"


def test_smart_recommendation_is_explained():
    s = fmt.summarize(yt_like())
    fast = fmt.smart_recommendation(s, container="mp4", avg_speed_bps=50 * 1024 * 1024)
    assert fast["quality"] == "2160" and fast["reasons"]
    slow = fmt.smart_recommendation(s, container="mp4", avg_speed_bps=150 * 1024)
    assert int(slow["quality"]) < 2160
    assert any("longer" in r for r in slow["reasons"])


def test_ytdlp_can_impersonate_a_browser():
    # TikTok (and a few other sites) only answer requests that look like a browser's: yt-dlp needs curl_cffi
    import yt_dlp
    with yt_dlp.YoutubeDL({"quiet": True}) as y:
        assert y._get_available_impersonate_targets(), "curl_cffi missing: TikTok downloads fail"
