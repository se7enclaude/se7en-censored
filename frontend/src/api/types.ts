export type JobStatus =
  | "waiting"
  | "analyzing"
  | "downloading"
  | "processing"
  | "paused"
  | "completed"
  | "failed"
  | "canceled";

export interface ApiErrorBody {
  code: string;
  message: string;
  hint?: string;
  retryable?: boolean;
  detail?: string;
  extra?: Record<string, unknown>;
}

export interface Quality {
  height: number;
  label: string;
  width?: number;
  fps?: number | null;
  codecs: string[];
  hdr: boolean;
  size?: number | null;
  size_exact: boolean;
  tbr?: number;
  sizes_by_codec: Record<string, number | null>;
}

export interface AudioOption {
  format_id: string;
  codec: string;
  abr?: number | null;
  ext?: string;
  size?: number | null;
  size_exact: boolean;
  language?: string | null;
}

export interface Entry {
  url: string;
  title?: string;
  id?: string;
  duration?: number;
  thumbnail?: string;
  uploader?: string;
  ext?: string;
  /** engine for this entry when it differs from the list's (a page scan mixes plain files and players) */
  provider?: string;
  /** deep site scan: the page the video was found on, and the Referer its server expects */
  page?: string;
  page_title?: string;
  referer?: string | null;
  /** encrypted stream (DRM): listed, never downloadable */
  protected?: boolean;
}

export interface Preset {
  id: string;
  name: string;
  kind: string;
  platforms: string[];
  options: DownloadOptions;
  auto_apply: boolean;
  created_at: number;
}

export interface MediaInfo {
  url: string;
  /** deep site scan: the pages that were explored */
  pages?: { url: string; title: string }[];
  webpage_url?: string;
  provider: string;
  platform: string;
  platform_name: string;
  id?: string;
  kind: "video" | "audio" | "image" | "playlist" | "gallery";
  already?: { id: string; created_at: number; file_path?: string; format?: string; resolution?: string; exists: boolean }[];
  title?: string;
  uploader?: string;
  thumbnail?: string;
  thumbnail_key?: string | null;
  duration?: number | null;
  upload_date?: string | null;
  description?: string;
  tags: string[];
  view_count?: number | null;
  width?: number | null;
  height?: number | null;
  fps?: number | null;
  ext?: string | null;
  is_live?: boolean;
  qualities: Quality[];
  audio: AudioOption[];
  audio_codecs: string[];
  video_codecs: string[];
  has_video: boolean;
  has_audio: boolean;
  max_height?: number | null;
  recommended?: { quality: string; label?: string; codec: string; reasons: string[] } | null;
  entries: Entry[];
  entry_count?: number | null;
  size?: number | null;
  warnings: string[];
  preset?: Preset | null;
  analyze_seconds?: number;
}

export interface Detection {
  url: string;
  platform: string;
  platform_name: string;
  extractor: string;
  provider: string;
  kind?: string;
}

export interface DownloadOptions {
  mode?: "av" | "video" | "audio" | "image";
  image_format?: string;
  quality?: string;
  container?: string;
  vcodec?: string;
  audio_format?: string;
  audio_bitrate?: number;
  output_dir?: string;
  naming_template?: string;
  folder_structure?: string;
  embed_metadata?: boolean;
  embed_thumbnail?: boolean;
  embed_chapters?: boolean;
  write_subtitles?: boolean;
  write_description?: boolean;
  [k: string]: unknown;
}

export interface JobProgress {
  stage?: string;
  label?: string | null;
  substage?: string;
  percent?: number | null;
  downloaded?: number;
  total?: number | null;
  speed?: number;
  eta?: number | null;
  connections?: number;
  fragment?: [number, number] | null;
  part?: string;
  unit?: string;
  frames_done?: number;
  frames_total?: number;
  segment?: number;
  segments_total?: number;
  fps_processing?: number | null;
  elapsed?: number;
  tile?: number;
  attempt?: number;
  strategy?: string | null;
}

export type JobKind = "download" | "upscale" | "convert" | "trim" | "studio" | "subtitles" | "clip";

export interface Job {
  id: string;
  kind: JobKind;
  status: JobStatus;
  priority: number;
  position: number;
  title?: string;
  source: string;
  platform?: string;
  thumbnail?: string | null;
  options: Record<string, any>;
  progress: JobProgress;
  result?: Record<string, any> | null;
  error?: ApiErrorBody | null;
  attempts: number;
  created_at: number;
  updated_at: number;
  started_at?: number;
  finished_at?: number;
}

export interface HistoryItem {
  id: string;
  kind: JobKind;
  title?: string;
  platform?: string;
  source?: string;
  thumbnail?: string | null;
  file_path?: string;
  folder?: string;
  resolution?: string;
  format?: string;
  size?: number;
  duration?: number;
  options?: Record<string, any>;
  meta?: Record<string, any>;
  created_at: number;
  exists: boolean;
}

export interface GPU {
  index: number;
  vendor: string;
  cuda: boolean;
  name?: string;
  vram_total_mb?: number;
  vram_used_mb?: number | null;
  vram_free_mb?: number | null;
  utilization?: number | null;
  temperature?: number | null;
  power_w?: number | null;
  encoder_utilization?: number | null;
}

export interface Hardware {
  cpu: { name: string; cores: number; threads: number; usage: number };
  ram: { total_mb: number; used_mb: number };
  gpus: GPU[];
  accelerator: "cuda" | "vulkan" | "cpu";
}

export interface ModelInfo {
  id: string;
  name: string;
  backend: string;
  scales: number[];
  categories: string[];
  description: string;
  video_suited: boolean;
  source: string;
  license: string;
  installed: boolean;
  usable: boolean;
  notes: string;
  speed?: "fast" | "medium" | "slow" | "";
  noncommercial?: boolean;
}

export interface Capabilities {
  backends: { id: string; name: string; available: boolean; info: string }[];
  models: ModelInfo[];
  categories: { id: string; installed: string[]; available: string[] }[];
  vulkan_devices: { index: number; name: string }[];
  enhancements: Record<string, { available: boolean; note?: string; models?: Record<string, boolean> }>;
  video: { available: boolean; encoders: string[] };
  onnx?: { available: boolean; info: string };
}

export interface LocalFile {
  path: string;
  name: string;
  kind: "image" | "video" | "audio" | "animated" | "subtitle" | null;
  ok: boolean;
  size?: number;
  width?: number;
  height?: number;
  fps?: number;
  duration?: number;
  frames?: number;
  vcodec?: string;
  is_hdr?: boolean;
  has_audio?: boolean;
  thumbnail?: string | null;
  error?: ApiErrorBody;
  acodec?: string;
  has_video?: boolean;
  subtitles?: number;
}

export interface ConvertTarget {
  id: string;
  kind: "video" | "audio" | "image" | "subtitle";
  ext: string;
  label: string;
  vcodec?: string | null;
  acodec?: string | null;
  lossless: boolean;
  note: string;
}

export interface PlaybackPlan {
  mode: "direct" | "remux" | "transcode" | "image";
  fallback?: "remux" | "transcode" | null;
  kind: "video" | "audio" | "image";
  path: string;
  name: string;
  size?: number;
  duration?: number | null;
  width?: number | null;
  height?: number | null;
  fps?: number | null;
  vcodec?: string | null;
  acodec?: string | null;
  audio_tracks?: number;
  has_cover?: boolean;
  title?: string | null;
  artist?: string | null;
  album?: string | null;
  is_hdr?: boolean;
}

export interface LibraryItem {
  path: string;
  name: string;
  title?: string;
  kind: "video" | "audio" | "image";
  thumbnail?: string | null;
  duration?: number | null;
  size?: number | null;
  created_at?: number;
  source_kind?: string;
}

export interface UpdateState {
  state: "idle" | "unconfigured" | "checking" | "uptodate" | "available" | "downloading" | "ready" | "installing" | "error";
  current: string;
  latest?: string;
  notes?: string;
  published?: string;
  size?: number;
  downloaded?: number;
  total?: number;
  configured?: boolean;
  portable?: boolean;
  checked?: number;
  error?: ApiErrorBody | null;
  /** set once after an update: the previous version ("older" before 1.2) and what changed in this one */
  updated_from?: string;
  changes?: Record<string, string[]>;
}

export interface TaskState<R = any> {
  id: string;
  kind: string;
  state: "running" | "done" | "failed" | "canceled";
  progress: { percent?: number | null; label?: string | null; found?: number; pages?: number; step?: string };
  result?: R;
  error?: ApiErrorBody | null;
}

export interface DupFile {
  path: string;
  name: string;
  folder: string;
  size: number;
  mtime: number;
  kind: string;
  width?: number | null;
  height?: number | null;
  duration?: number | null;
  keep: boolean;
  thumbnail?: string | null;
}

export interface DupGroup {
  type: "exact" | "similar";
  kind: string;
  files: DupFile[];
  wasted: number;
}

export interface Stats {
  total: number;
  by_kind: Record<string, { count: number; bytes: number; seconds: number }>;
  platforms: { id: string; name: string; count: number; bytes: number }[];
  formats: { name: string; count: number }[];
  resolutions: { name: string; count: number }[];
  uploaders: { name: string; count: number }[];
  timeline: ({ day: string; bytes: number } & Record<string, number | string>)[];
  hours: number[];
  media_seconds: number;
  median_speed?: number | null;
  biggest?: { title?: string; size: number; kind: string } | null;
  longest?: { title?: string; duration: number; kind: string } | null;
  on_disk: number;
  missing: number;
  active_days: number;
  streak: number;
  first?: number | null;
  days: number;
}

export interface PreviewResult {
  id: string;
  original: string;
  result: string;
  width: number;
  height: number;
  region: { x: number; y: number; w: number; h: number };
  source: { width: number; height: number };
  output: { width: number; height: number };
  plan: string;
  elapsed: number;
  estimate_seconds: number;
  kind: string;
  at?: number;
}

export interface Component {
  id: string;
  name: string;
  description: string;
  required_for: string;
  optional: boolean;
  installed: boolean;
  path?: string | null;
  version?: string | null;
  size?: number;
  models?: { id: string; file: string; size: number; license: string; author: string; noncommercial: boolean; installed: boolean; purpose: string }[];
}

export interface LogEntry {
  seq: number;
  ts: number;
  level: "debug" | "info" | "warning" | "error" | "critical";
  category: string;
  message: string;
  job_id?: string | null;
  exception?: string;
}

export interface Settings {
  general: {
    language: "auto" | "en" | "fr";
    theme: "dark" | "light" | "system";
    accent: string;
    notifications: boolean;
    notification_sound: boolean;
    ui_sounds: boolean;
    sound_volume: number;
    auto_update: boolean;
    startup_page: string;
    clipboard_watch: boolean;
    reduce_motion: boolean;
    launch_animation: boolean;
    startup_sound: boolean;
  };
  download: {
    default_folder: string;
    default_mode: "av" | "video" | "audio" | "image";
    default_image_format: string;
    warn_duplicates: boolean;
    default_quality: string;
    default_container: string;
    default_video_codec: string;
    default_audio_format: string;
    default_audio_bitrate: number;
    concurrent_downloads: number;
    concurrent_fragments: number;
    naming_template: string;
    folder_structure: string;
    platform_folders: Record<string, string>;
    embed_metadata: boolean;
    embed_thumbnail: boolean;
    embed_chapters: boolean;
    write_description: boolean;
    write_subtitles: boolean;
    subtitle_langs: string;
    rate_limit_kbps: number;
    use_aria2c: boolean;
    auto_apply_presets: boolean;
    auto_analyze_on_paste: boolean;
    max_retries: number;
    playlist_limit: number;
  };
  ai: {
    default_model: string;
    default_scale: number;
    gpu_id: string;
    vram_limit_mb: number;
    tile_size: number;
    threads: string;
    video_encoder: string;
    video_quality: number;
    segment_seconds: number;
    output_folder: string;
    output_suffix: string;
    image_format: string;
    concurrent_jobs: number;
    temporal_stabilization: boolean;
  };
  tools: {
    convert_concurrent: number;
    convert_output_folder: string;
    studio_output_folder: string;
    use_gpu_encoder: boolean;
    trim_mode: "fast" | "precise";
    trim_output: "separate" | "merge";
    duplicate_folders: string[];
  };
  subtitles?: { engine: "local" | "claude"; claude_model: string; default_preset: string; output_folder: string; max_chars: number; max_cps: number; polish: boolean };
  privacy: {
    cookies_mode: "none" | "browser" | "file";
    cookies_browser: string;
    keep_history: boolean;
    history_retention_days: number;
    clear_temp_on_exit: boolean;
  };
  advanced: {
    debug_mode: boolean;
    ffmpeg_path: string;
    realesrgan_path: string;
    aria2c_path: string;
    api_port: number;
  };
}

export interface AppEvent<T = any> {
  type: string;
  data: T;
  ts: number;
}

// ------------------------------------------------------------------ subtitles
export interface SubWord {
  text: string;
  start: number;
  end: number;
  p?: number;
}
export interface SubCue {
  id: string;
  start: number;
  end: number;
  text: string;
  words?: SubWord[];
  speaker?: number | null;
}
export type SubAnimation = "none" | "fade" | "pop" | "slide" | "word" | "char" | "highlight" | "typewriter" | "karaoke";
export interface SubStyle {
  preset: string;
  font: string;
  size: number;
  weight: number;
  italic: boolean;
  uppercase: boolean;
  color: string;
  opacity: number;
  outline_color: string;
  outline: number;
  shadow_color: string;
  shadow: number;
  shadow_opacity: number;
  background: "none" | "box";
  bg_color: string;
  bg_opacity: number;
  bg_padding: number;
  position: number;
  vertical: "bottom" | "top";
  align: "left" | "center" | "right";
  letter_spacing: number;
  line_height: number;
  max_width: number;
  animation: SubAnimation;
  highlight_color: string;
  anim_speed: number;
}
export interface SubVideo {
  path: string;
  name: string;
  size?: number;
  duration?: number;
  width?: number;
  height?: number;
  fps?: number;
  vcodec?: string;
  acodec?: string;
  format?: string;
  has_video?: boolean;
}
export interface SubProject {
  id: string;
  name: string;
  created: number;
  updated: number;
  video: SubVideo;
  thumbnail?: string | null;
  detected?: { code: string; p: number } | null;
  source_lang?: string | null;
  target_lang?: string | null;
  original_lang?: string | null;
  engine?: string | null;
  options?: { polish?: boolean; glossary?: string };
  status: "new" | "ready";
  cues: SubCue[];
  original: SubCue[];
  style: SubStyle;
  generated?: { at: number; seconds: number; notes: string[]; engine?: string | null };
}
export interface SubProjectSummary {
  id: string;
  name: string;
  updated: number;
  status: string;
  cues: number;
  video: string;
  duration?: number;
  exists: boolean;
  source_lang?: string | null;
  target_lang?: string | null;
}
export interface SubLanguage {
  code: string;
  fr: string;
  en: string;
  flag: string;
  featured: boolean;
}
export interface SubCatalog {
  languages: SubLanguage[];
  presets: Record<string, SubStyle>;
  fonts: { family: string; weights: number[] }[];
  animations: SubAnimation[];
  defaults: SubStyle;
  engines: {
    whisper: { installed: boolean; size: number };
    local: { installed: boolean; size: number; model: string };
    claude: { configured: boolean; hint?: string | null; model: string };
  };
  settings: { engine: "local" | "claude"; claude_model: string; default_preset: string; output_folder: string; max_chars: number; max_cps: number; polish: boolean };
}
