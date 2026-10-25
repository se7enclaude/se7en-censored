"""Typed application settings persisted as JSON (atomic writes)."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from .config import paths


def _default_video_dir() -> str:
    return str(Path.home() / "Videos" / "se7en Censored")


class GeneralSettings(BaseModel):
    language: Literal["auto", "en", "fr"] = "auto"
    theme: Literal["dark", "light", "system"] = "light"
    accent: str = "#7c5cff"
    notifications: bool = True
    notification_sound: bool = True
    ui_sounds: bool = True
    sound_volume: int = Field(60, ge=0, le=100)
    startup_page: Literal["download", "upscale", "queue", "history", "convert", "trim", "studio", "subtitles", "player", "last"] = "download"
    auto_update: bool = True
    clipboard_watch: bool = True
    reduce_motion: bool = False
    launch_animation: bool = True
    startup_sound: bool = True


class DownloadSettings(BaseModel):
    default_folder: str = Field(default_factory=_default_video_dir)
    default_mode: Literal["av", "video", "audio", "image"] = "av"
    default_quality: str = "best"  # best | 2160 | 1440 | 1080 | 720 | 480 | 360 | 240 | 144
    default_container: Literal["mp4", "mkv", "webm"] = "mp4"
    # compat = plays everywhere (H.264 + AAC in MP4, converted when the source lacks it)
    default_video_codec: Literal["compat", "auto", "h264", "h265", "av1", "vp9"] = "compat"
    default_image_format: Literal["jpg", "png", "webp"] = "jpg"
    warn_duplicates: bool = True
    default_audio_format: Literal["best", "mp3", "aac", "m4a", "opus", "wav", "flac"] = "mp3"
    default_audio_bitrate: int = 320
    concurrent_downloads: int = Field(3, ge=1, le=16)
    concurrent_fragments: int = Field(8, ge=1, le=32)
    naming_template: str = "{title}"
    folder_structure: str = "{platform}"
    platform_folders: dict[str, str] = Field(default_factory=dict)
    embed_metadata: bool = True
    embed_thumbnail: bool = True
    embed_chapters: bool = True
    write_description: bool = False
    write_subtitles: bool = False
    subtitle_langs: str = "en,fr"
    rate_limit_kbps: int = 0  # 0 = unlimited
    use_aria2c: bool = False
    auto_apply_presets: bool = True
    auto_analyze_on_paste: bool = True
    max_retries: int = Field(10, ge=0, le=50)
    playlist_limit: int = Field(500, ge=1, le=5000)


class AISettings(BaseModel):
    default_model: str = "realesrgan-x4plus"
    default_scale: int = 4
    gpu_id: str = "auto"  # auto | cpu | <vulkan index>
    vram_limit_mb: int = 0  # 0 = auto
    tile_size: int = 0  # 0 = auto (derived from VRAM)
    threads: str = "1:2:2"  # ncnn load:proc:save
    video_encoder: Literal["auto", "h264_nvenc", "hevc_nvenc", "av1_nvenc", "h264_qsv", "hevc_qsv",
                           "h264_amf", "hevc_amf", "libx264", "libx265"] = "auto"
    video_quality: int = Field(18, ge=0, le=51)  # CRF / CQ
    segment_seconds: int = Field(20, ge=2, le=600)
    output_folder: str = ""  # empty = next to the source file
    output_suffix: str = "_upscaled"
    image_format: Literal["png", "jpg", "webp"] = "png"
    concurrent_jobs: int = Field(1, ge=1, le=4)
    temporal_stabilization: bool = True


class ToolsSettings(BaseModel):
    convert_concurrent: int = Field(2, ge=1, le=8)
    convert_output_folder: str = ""  # empty = next to the source file
    studio_output_folder: str = ""  # audio tools (separate / clean); empty = next to the source
    use_gpu_encoder: bool = True
    trim_mode: Literal["fast", "precise"] = "fast"
    trim_output: Literal["separate", "merge"] = "merge"
    duplicate_folders: list[str] = Field(default_factory=list)


class SubtitlesSettings(BaseModel):
    engine: Literal["local", "claude"] = "local"  # correction / translation (Whisper always runs locally)
    claude_model: str = "claude-opus-5-5"
    default_preset: str = "netflix"
    output_folder: str = ""  # exports; empty = next to the video
    max_chars: int = Field(42, ge=20, le=60)
    max_cps: float = Field(17.0, ge=8.0, le=30.0)
    polish: bool = True


class PrivacySettings(BaseModel):
    cookies_mode: Literal["none", "browser", "file"] = "none"
    cookies_browser: str = "firefox"
    keep_history: bool = True
    history_retention_days: int = 0  # 0 = forever
    clear_temp_on_exit: bool = True


class AdvancedSettings(BaseModel):
    debug_mode: bool = False
    ffmpeg_path: str = ""
    realesrgan_path: str = ""
    aria2c_path: str = ""
    api_port: int = 0  # 0 = random free port


SETTINGS_VERSION = 3


class Settings(BaseModel):
    version: int = SETTINGS_VERSION
    general: GeneralSettings = Field(default_factory=GeneralSettings)
    download: DownloadSettings = Field(default_factory=DownloadSettings)
    ai: AISettings = Field(default_factory=AISettings)
    tools: ToolsSettings = Field(default_factory=ToolsSettings)
    subtitles: SubtitlesSettings = Field(default_factory=SubtitlesSettings)
    privacy: PrivacySettings = Field(default_factory=PrivacySettings)
    advanced: AdvancedSettings = Field(default_factory=AdvancedSettings)


def _migrate(data: dict) -> dict:
    """Upgrade settings written by older versions."""
    v = int(data.get("version") or 1)
    if v < 2:
        dl = data.setdefault("download", {})
        # 1.0 kept the source codec: 4K AV1 then showed a black picture in Windows' player.
        if dl.get("default_video_codec") in (None, "auto"):
            dl["default_video_codec"] = "compat"
        g = data.setdefault("general", {})
        g["notification_sound"] = True
    if v < 3:
        # 1.3 "liquid glass" redesign: its light red look becomes the default (the old default was dark)
        g = data.setdefault("general", {})
        if g.get("theme") in (None, "dark"):
            g["theme"] = "light"
    data["version"] = SETTINGS_VERSION
    return data


def _deep_merge(base: dict, patch: dict) -> dict:
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "platform_folders":
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or paths().settings
        self._lock = threading.RLock()
        self._settings = self._load()
        self._listeners: list = []

    def _load(self) -> Settings:
        if self.path.exists():
            try:
                data = _migrate(json.loads(self.path.read_text("utf-8-sig")))
                return Settings.model_validate(data)
            except (ValidationError, json.JSONDecodeError, OSError):
                # Keep a copy of the broken file and start from defaults.
                try:
                    self.path.replace(self.path.with_suffix(".broken.json"))
                except OSError:
                    pass
        return Settings()

    def get(self) -> Settings:
        with self._lock:
            return self._settings

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(self._settings.model_dump_json(indent=2), "utf-8")
            os.replace(tmp, self.path)

    def update(self, patch: dict[str, Any]) -> Settings:
        with self._lock:
            merged = _deep_merge(self._settings.model_dump(), patch)
            self._settings = Settings.model_validate(merged)  # raises ValidationError
            self.save()
            new = self._settings
        for cb in list(self._listeners):
            cb(new)
        return new

    def reset(self, section: str | None = None) -> Settings:
        with self._lock:
            if section:
                data = self._settings.model_dump()
                data[section] = Settings().model_dump()[section]
                self._settings = Settings.model_validate(data)
            else:
                self._settings = Settings()
            self.save()
            return self._settings

    def on_change(self, cb) -> None:
        self._listeners.append(cb)
