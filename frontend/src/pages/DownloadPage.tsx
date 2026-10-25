import { AnimatePresence, motion } from "motion/react";
import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  AudioLines,
  Captions,
  Bookmark,
  ClipboardPaste,
  Download,
  Film,
  FolderOpen,
  History,
  Image as ImageIcon,
  Layers,
  Play,
  Link2,
  RefreshCcw,
  RotateCcw,
  ScanLine,
  Sparkles,
  Video,
  Wand2,
  X,
} from "lucide-react";
import { ReactNode, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, thumbUrl } from "../api/client";
import type { Detection, DownloadOptions, Entry, Job, MediaInfo, Preset, Quality, TaskState } from "../api/types";
import heroArt from "../assets/hero-art.webp";
import { CutoutPanel, Notches } from "../components/CutoutPanel";
import { PixelImage } from "../components/Pixel";
import { Badge, Button, Check, ErrorAlert, Field, Modal, Notice, PlatformChip, Progress, Segmented, Select, Switch, Ticks } from "../components/ui";
import { useKey } from "../hooks/useShortcuts";
import { play } from "../lib/sounds";
import { useT } from "../i18n";
import { bytes, codecLabel, duration, num, speed } from "../lib/format";
import { useApp } from "../store/app";

const GENERIC_QUALITIES = ["best", "2160", "1440", "1080", "720", "480", "360", "144"];
const LOSSY = new Set(["mp3", "m4a", "aac", "opus"]);
const AUDIO_ROWS: { f: string; br?: number }[] = [
  { f: "mp3", br: 320 },
  { f: "mp3", br: 192 },
  { f: "m4a", br: 256 },
  { f: "opus", br: 160 },
  { f: "flac" },
  { f: "wav" },
  { f: "best" },
];

type Mode = "av" | "video" | "audio" | "image";
const rise = (i: number) => ({ animation: "se7en-rise .8s var(--ease-out) both", animationDelay: `${i * 90}ms` });

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^(www|m)\./, "");
  } catch {
    return url;
  }
}

export function DownloadPage() {
  const t = useT();
  const settings = useApp((s) => s.settings);
  const toast = useApp((s) => s.toast);
  const setPage = useApp((s) => s.setPage);
  const openInPlayer = useApp((s) => s.openInPlayer);
  const goSettings = useApp((s) => s.goSettings);
  const pendingUrls = useApp((s) => s.pendingUrls);
  const consumeUrls = useApp((s) => s.consumeUrls);

  const [text, setText] = useState("");
  const [focused, setFocused] = useState(false);
  const [detections, setDetections] = useState<Detection[]>([]);
  const [target, setTarget] = useState("");
  const [analyzing, setAnalyzing] = useState(false);
  const [info, setInfo] = useState<MediaInfo | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [clip, setClip] = useState<string | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [presetId, setPresetId] = useState<string>("");
  const [adding, setAdding] = useState(false);
  const [savePreset, setSavePreset] = useState(false);
  const [logStep, setLogStep] = useState(0);
  // deep site scan running in the background (its progress arrives as "task" events)
  const [scanTask, setScanTask] = useState<string | null>(null);
  const [scanFailed, setScanFailed] = useState(false);
  const scanState = useApp((s) => (scanTask ? s.tasks[scanTask] : undefined)) as TaskState | undefined;

  const d = settings?.download;
  const [mode, setMode] = useState<Mode>(d?.default_mode ?? "av");
  const [quality, setQuality] = useState<string>(d?.default_quality ?? "best");
  const [container, setContainer] = useState<string>(d?.default_container ?? "mp4");
  const [vcodec, setVcodec] = useState<string>(d?.default_video_codec ?? "auto");
  const [audioFormat, setAudioFormat] = useState<string>(d?.default_audio_format ?? "mp3");
  const [audioBitrate, setAudioBitrate] = useState<number>(d?.default_audio_bitrate ?? 320);
  const [imageFormat, setImageFormat] = useState<string>(d?.default_image_format ?? "jpg");
  const [outputDir, setOutputDir] = useState<string>("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [embedMeta, setEmbedMeta] = useState(d?.embed_metadata ?? true);
  // what the hero chips ask for: the analysis then opens on that mode (when the media has it)
  const [homeMode, setHomeMode] = useState<"av" | "audio" | "image">(d?.default_mode === "audio" ? "audio" : d?.default_mode === "image" ? "image" : "av");

  const inputRef = useRef<HTMLTextAreaElement>(null);
  const reqId = useRef(0);
  const lastClip = useRef<string>("");

  useEffect(() => {
    api.get<{ presets: Preset[] }>("/api/presets").then((r) => setPresets(r.presets)).catch(() => undefined);
  }, []);

  // ---------------------------------------------------------------- detection
  useEffect(() => {
    const handle = window.setTimeout(async () => {
      if (!text.trim()) {
        setDetections([]);
        return;
      }
      try {
        const r = await api.post<{ urls: Detection[] }>("/api/extract-urls", { text });
        setDetections(r.urls);
      } catch {
        /* harmless */
      }
    }, 160);
    return () => window.clearTimeout(handle);
  }, [text]);

  const applyOptions = useCallback((o: DownloadOptions) => {
    if (o.mode) setMode(o.mode);
    if (o.quality) setQuality(String(o.quality));
    if (o.container) setContainer(String(o.container));
    if (o.vcodec) setVcodec(String(o.vcodec));
    if (o.audio_format) setAudioFormat(String(o.audio_format));
    if (o.audio_bitrate) setAudioBitrate(Number(o.audio_bitrate));
    if (o.image_format) setImageFormat(String(o.image_format));
    if (o.output_dir) setOutputDir(String(o.output_dir));
    if (o.embed_metadata !== undefined) setEmbedMeta(Boolean(o.embed_metadata));
  }, []);

  const analyze = useCallback(
    async (url?: string) => {
      const u = (url ?? detections[0]?.url ?? text.trim()).trim();
      if (!u) return;
      const id = ++reqId.current;
      setTarget(u);
      setAnalyzing(true);
      setError(null);
      setInfo(null);
      setLogStep(0);
      setScanFailed(false);
      try {
        const r = await api.post<MediaInfo>("/api/analyze", { url: u });
        if (id !== reqId.current) return;
        setInfo(r);
        setSelected(new Set(r.entries.map((_, i) => i)));
        if (d) {
          const want: Mode = homeMode === "av" ? (d.default_mode === "video" ? "video" : "av") : homeMode;
          setMode(r.kind === "audio" ? "audio" : r.kind === "image" ? "image" : want === "image" && !r.thumbnail ? "av" : want);
          setQuality(d.default_quality);
          setContainer(d.default_container);
          setVcodec(d.default_video_codec);
          setOutputDir("");
        }
        if (r.preset) {
          setPresetId(r.preset.id);
          applyOptions(r.preset.options);
        } else setPresetId("");
      } catch (e: any) {
        if (id !== reqId.current) return;
        setError(e instanceof ApiError ? e : new ApiError({ code: "unknown", message: String(e) }, 0));
      } finally {
        if (id === reqId.current) setAnalyzing(false);
      }
    },
    [detections, text, d, applyOptions, homeMode],
  );

  const scanPage = useCallback(async () => {
    const u = (target || text.trim()).trim();
    if (!u) return;
    const id = ++reqId.current;
    setTarget(u);
    setAnalyzing(true);
    setError(null);
    setInfo(null);
    setLogStep(0);
    setScanFailed(false);
    try {
      const task = await api.post<TaskState>("/api/scan-site", { url: u });
      if (id !== reqId.current) {
        api.post(`/api/tasks/${task.id}/cancel`).catch(() => undefined);
        return;
      }
      useApp.setState({ tasks: { ...useApp.getState().tasks, [task.id]: task } });
      setScanTask(task.id);
    } catch (e: any) {
      if (id !== reqId.current) return;
      setError(e instanceof ApiError ? e : new ApiError({ code: "unknown", message: String(e) }, 0));
      setAnalyzing(false);
    }
  }, [target, text]);

  const scanRunning = !!scanTask && (!scanState || scanState.state === "running");
  const cancelScan = () => {
    if (scanTask) api.post(`/api/tasks/${scanTask}/cancel`).catch(() => undefined);
  };

  // safety net if a progress event is missed (sleep, reconnection)
  useEffect(() => {
    if (!scanRunning || !scanTask) return;
    const iv = window.setInterval(async () => {
      try {
        const r = await api.get<TaskState>(`/api/tasks/${scanTask}`);
        if (r.state !== "running") useApp.setState({ tasks: { ...useApp.getState().tasks, [r.id]: { ...r, result: undefined } } });
      } catch {
        /* ignore */
      }
    }, 2000);
    return () => window.clearInterval(iv);
  }, [scanRunning, scanTask]);

  // the scan ended: its result (or why it failed)
  useEffect(() => {
    if (!scanTask || !scanState || scanState.state === "running") return;
    const id = reqId.current;
    const tid = scanTask;
    setScanTask(null);
    api
      .get<TaskState<MediaInfo>>(`/api/tasks/${tid}`)
      .then((r) => {
        if (id !== reqId.current) return;
        if (r.state === "done" && r.result) {
          setInfo(r.result);
          setSelected(new Set(r.result.entries.flatMap((e, i) => (e.protected ? [] : [i]))));
          setPresetId("");
        } else if (r.state === "failed" && r.error) {
          setError(new ApiError(r.error, 0));
          setScanFailed(true);
        }
        else if (r.state === "canceled") reset();
      })
      .catch((e) => id === reqId.current && setError(e instanceof ApiError ? e : new ApiError({ code: "unknown", message: String(e) }, 0)))
      .finally(() => id === reqId.current && setAnalyzing(false));
  }, [scanTask, scanState?.state]); // eslint-disable-line react-hooks/exhaustive-deps

  // progressive analysis journal (the steps the engine actually performs)
  useEffect(() => {
    if (!analyzing) return;
    const ts = [350, 900, 1600].map((ms, i) => window.setTimeout(() => setLogStep(i + 1), ms));
    return () => ts.forEach(clearTimeout);
  }, [analyzing]);

  useEffect(() => {
    if (!pendingUrls) return;
    const urls = consumeUrls();
    if (!urls?.length) return;
    setText(urls.join("\n"));
    if (urls.length === 1) analyze(urls[0]);
  }, [pendingUrls]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!settings?.general.clipboard_watch) return;
    const check = async () => {
      try {
        const r = await api.get<{ urls: string[] }>("/api/system/clipboard");
        const u = r.urls[0];
        if (u && u !== lastClip.current && !text.includes(u)) {
          lastClip.current = u;
          setClip(u);
        }
      } catch {
        /* ignore */
      }
    };
    check();
    window.addEventListener("focus", check);
    return () => window.removeEventListener("focus", check);
  }, [settings?.general.clipboard_watch, text]);

  const onPaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const pasted = e.clipboardData.getData("text");
    const urls = pasted.match(/https?:\/\/\S+/g) ?? [];
    if (urls.length === 1 && !text.trim() && d?.auto_analyze_on_paste !== false) window.setTimeout(() => analyze(urls[0]), 30);
  };

  const reset = () => {
    reqId.current++;
    if (scanTask) {
      api.post(`/api/tasks/${scanTask}/cancel`).catch(() => undefined);
      setScanTask(null);
    }
    setText("");
    setInfo(null);
    setError(null);
    setAnalyzing(false);
    setTarget("");
    window.setTimeout(() => inputRef.current?.focus(), 50);
  };

  const batch = detections.length > 1 && !analyzing && !info && !error;
  const view: "home" | "analyze" = analyzing || info || error ? "analyze" : "home";

  // ---------------------------------------------------------------- derived
  const qualities = info?.qualities ?? [];
  const chosenQ: Quality | undefined = useMemo(() => {
    if (!qualities.length) return undefined;
    if (quality === "best") return qualities[0];
    const q = Number(quality);
    return qualities.find((x) => x.height <= q) ?? qualities[qualities.length - 1];
  }, [qualities, quality]);
  const containerCodecs: Record<string, string[]> = { mp4: ["compat", "auto", "h264", "h265", "av1"], mkv: ["compat", "auto", "h264", "h265", "av1", "vp9"], webm: ["auto", "vp9", "av1"] };
  const codecOptions = containerCodecs[container] ?? ["auto"];
  useEffect(() => {
    if (!codecOptions.includes(vcodec)) setVcodec(codecOptions[0]);
  }, [container]); // eslint-disable-line react-hooks/exhaustive-deps

  const audioSize = (f: string, br?: number) => {
    if (!info) return null;
    if (LOSSY.has(f) && br && info.duration) return (br * 1000 * info.duration) / 8;
    if (f === "wav" && info.duration) return info.duration * 44100 * 4;
    if (f === "flac" && info.duration) return info.duration * 44100 * 4 * 0.55;
    return info.audio[0]?.size ?? null;
  };
  const estimate = useMemo(() => {
    if (!info) return null;
    if (mode === "audio") return audioSize(audioFormat, audioBitrate);
    if (mode === "image") return null;
    if (!chosenQ) return info.size ?? null;
    const byCodec = !["auto", "compat"].includes(vcodec) ? chosenQ.sizes_by_codec[vcodec] : null;
    return byCodec ?? chosenQ.size ?? null;
  }, [info, mode, chosenQ, vcodec, audioFormat, audioBitrate]); // eslint-disable-line react-hooks/exhaustive-deps
  const needsH264 = (q?: Quality) => vcodec === "compat" && container === "mp4" && !!q && !q.codecs.includes("h264");
  const reencode = mode !== "audio" && mode !== "image" && chosenQ && (needsH264(chosenQ) || (!["auto", "compat"].includes(vcodec) && !chosenQ.codecs.includes(vcodec)));
  const hwEncoder = (useApp.getState().capabilities?.video.encoders ?? []).some((e) => /_(nvenc|qsv|amf)$/.test(e));
  const sourceAbr = info?.audio?.[0]?.abr ?? null;

  // ---------------------------------------------------------------- actions
  const buildOptions = (): DownloadOptions => ({
    mode,
    quality,
    container,
    vcodec,
    audio_format: audioFormat,
    audio_bitrate: audioBitrate,
    image_format: imageFormat,
    embed_metadata: embedMeta,
    ...(outputDir ? { output_dir: outputDir } : {}),
  });

  const enqueue = async (items: { url: string; title?: string; thumbnail?: string; platform?: string; platform_name?: string; provider?: string; entry_count?: number | null; options?: Record<string, unknown> }[]) => {
    setAdding(true);
    try {
      const r = await api.post<{ jobs: Job[] }>("/api/downloads", { items, options: buildOptions(), preset_id: presetId || undefined });
      play("start");
      toast({
        kind: "success",
        title: t("download.added", { n: r.jobs.length }),
        body: r.jobs.length === 1 ? r.jobs[0].title : undefined,
        action: { label: t("download.viewQueue"), run: () => setPage("queue") },
      });
      reset();
    } catch (e: any) {
      toast({ kind: "error", title: t("download.addFailed"), body: e.message });
    } finally {
      setAdding(false);
    }
  };

  const downloadCurrent = () => {
    if (!info) return;
    if (info.kind === "playlist") {
      enqueue(
        info.entries
          .filter((e, i) => selected.has(i) && !e.protected)
          .map((e) => ({
            url: e.url,
            title: e.title,
            thumbnail: e.thumbnail,
            platform: info.platform,
            platform_name: info.platform_name,
            provider: e.provider ?? info.provider,
            ...(e.referer ? { options: { referer: e.referer } } : {}),
          })),
      );
    } else {
      enqueue([{ url: info.webpage_url || info.url, title: info.title, thumbnail: info.thumbnail, platform: info.platform, platform_name: info.platform_name, provider: info.provider, entry_count: info.entry_count }]);
    }
  };
  const downloadBatch = () => enqueue(detections.map((dd) => ({ url: dd.url, platform: dd.platform, platform_name: dd.platform_name, provider: dd.provider })));

  const pickFolder = async () => {
    const r = await api.post<{ path: string | null }>("/api/system/pick-folder", { initial: outputDir || d?.default_folder });
    if (!r.path) return;
    setOutputDir(r.path);
    // Remembered for the next downloads (and the next launches)
    useApp.getState().saveSettings({ download: { default_folder: r.path } }).catch(() => undefined);
  };

  useKey("ctrl+Enter", (e) => {
    e.preventDefault();
    if (batch) downloadBatch();
    else if (info && !adding) downloadCurrent();
  });
  useKey("Escape", () => view === "analyze" && reset(), view === "analyze");

  const presetOptions = [{ value: "", label: t("download.noPreset") }, ...presets.map((p) => ({ value: p.id, label: p.name }))];
  const folder = outputDir || d?.default_folder || "";

  // ================================================================ HOME (Studio Glass: notched red hero)
  if (view === "home")
    return (
      <div className="home">
        <HomeHero mode={homeMode} setMode={setHomeMode} />
        <div className="home-grid">
          <HomeIntro />
          <div className="home-main" style={rise(2)}>
            <h2 className="home-h2">
              {t("home.h2a")}
              <br />
              {t("home.h2b")}
            </h2>
            <div className={`url-input ${focused ? "focused" : ""}`}>
              <Link2 size={20} />
              <textarea
                ref={inputRef}
                rows={Math.min(6, Math.max(1, text.split("\n").length))}
                placeholder={t("home.placeholder")}
                value={text}
                autoFocus
                spellCheck={false}
                onFocus={() => setFocused(true)}
                onBlur={() => setFocused(false)}
                onPaste={onPaste}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey) {
                    e.preventDefault();
                    if (!batch) analyze();
                  }
                }}
              />
              <div className="url-actions">
                {text && <Button variant="ghost" iconOnly icon={<X size={16} />} tip={t("common.clear")} onClick={reset} />}
                {!text && (
                  <Button
                    variant="ghost"
                    iconOnly
                    icon={<ClipboardPaste size={16} />}
                    tip={t("download.paste")}
                    onClick={async () => {
                      try {
                        const r = await api.get<{ urls: string[] }>("/api/system/clipboard");
                        if (r.urls.length) {
                          setText(r.urls.join("\n"));
                          if (r.urls.length === 1) analyze(r.urls[0]);
                        }
                      } catch {
                        inputRef.current?.focus();
                      }
                    }}
                  />
                )}
                {!batch && (
                  <Button variant="primary" size="lg" icon={<ScanLine size={17} />} disabled={!text.trim()} onClick={() => analyze()}>
                    {t("download.analyze")}
                  </Button>
                )}
              </div>
            </div>
            <div className="hints">
              <span><span className="kbd">Ctrl</span><span className="kbd">V</span> {t("download.hintPaste")}</span>
              <span><span className="kbd">↵</span> {t("download.hintAnalyze")}</span>
              {detections.length === 1 && (
                <span className="row" style={{ gap: 8 }}>
                  <span className="dot-accent" />
                  <span className="label" style={{ color: "var(--text-secondary)" }}>{t("download.detected", { name: detections[0].platform_name })}</span>
                </span>
              )}
              {batch && <Badge tone="accent" icon={<Layers size={12} />}>{t("download.batchCount", { n: detections.length })}</Badge>}
              {clip && !text && (
                <button
                  className="chip"
                  style={{ cursor: "pointer", maxWidth: 360 }}
                  onClick={() => {
                    setText(clip);
                    analyze(clip);
                    setClip(null);
                  }}
                >
                  <ClipboardPaste size={12} /> {t("download.fromClipboard")} <span className="ellipsis" style={{ textTransform: "none", letterSpacing: 0 }}>{hostOf(clip)}</span>
                </button>
              )}
            </div>
            <AnimatePresence>
              {batch && (
                <motion.div key="batch" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="card card-pad home-panel">
                  <div className="row" style={{ marginBottom: 14 }}>
                    <span className="label">{t("download.batchTitle", { n: detections.length })}</span>
                  </div>
                  <div className="entry-list" style={{ maxHeight: 220 }}>
                    {detections.map((dd, i) => (
                      <div className="entry" key={dd.url + i} style={{ cursor: "default" }}>
                        <span className="mono faint">{String(i + 1).padStart(2, "0")}</span>
                        <PlatformChip platform={dd.platform} name={hostOf(dd.url)} />
                        <span className="ellipsis muted selectable mono" style={{ flex: 1 }}>{dd.url}</span>
                        <Button size="sm" variant="ghost" onClick={() => { setText(dd.url); analyze(dd.url); }}>{t("download.analyzeOne")}</Button>
                      </div>
                    ))}
                  </div>
                  <div className="divider" />
                  <GenericOptions {...{ t, mode, setMode, quality, setQuality, container, setContainer, audioFormat, setAudioFormat }} />
                  <div className="row" style={{ marginTop: 18 }}>
                    <FolderRow t={t} value={folder} onPick={pickFolder} />
                    <Button variant="primary" size="lg" icon={<Download size={16} />} loading={adding} onClick={downloadBatch}>{t("download.addAll", { n: detections.length })}</Button>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
            {!batch && <ActiveDownloads />}
          </div>
          <HomeFeatures />
        </div>
      </div>
    );

  // ================================================================ ANALYZE
  const domain = hostOf(target);
  const ready = !!info && !analyzing;
  const isPlaylist = info?.kind === "playlist";
  const isGallery = info?.kind === "gallery" || info?.kind === "image";
  const bestQ = qualities[0];
  const readout = info
    ? info.max_height
      ? `${bestQ?.width ?? info.width ?? "—"}×${info.max_height} ${bestQ?.fps ? `· ${Math.round(bestQ.fps)} fps` : ""}`
      : info.width && info.height
        ? `${info.width}×${info.height}`
        : isPlaylist
          ? t("download.nItems", { n: info.entry_count ?? info.entries.length })
          : "—"
    : "—";
  const logLines = [t("log.connect", { host: domain }), t("log.meta"), t("log.formats")];

  return (
    <div className="analyze">
      <div className="analyze-main">
        <div className="url-line rise">
          <Button iconOnly icon={<ArrowLeft size={15} />} tip={`${t("common.back")} · Esc`} onClick={reset} style={{ ["--h" as any]: "36px" }} />
          <span className="mono ellipsis selectable" style={{ flex: 1 }}>{target}</span>
          {!analyzing && !info?.pages && (
            <Button size="sm" variant="ghost" icon={<Layers size={14} />} tip={t("download.scanPageTip")} onClick={scanPage}>
              {t("download.scanPage")}
            </Button>
          )}
        </div>

        {isPlaylist && info ? (
          <div className="rise" style={{ animationDelay: "60ms" }}>
            <EntryList entries={info.entries} selected={selected} setSelected={setSelected} grouped={!!info.pages && info.pages.length > 1} />
          </div>
        ) : isGallery && info && info.entries.length > 1 ? (
          <div className="gallery-grid rise">
            {info.entries.slice(0, 30).map((e, i) => (
              <div key={i}>{e.thumbnail ? <img src={e.thumbnail} alt="" referrerPolicy="no-referrer" loading="lazy" onError={(ev) => (ev.currentTarget.style.display = "none")} /> : <div className="thumb-fallback"><Video size={18} /></div>}</div>
            ))}
          </div>
        ) : (
          <PixelImage
            className="media rise"
            src={info ? thumbUrl(info.thumbnail_key) : null}
            fit="contain"
            backdrop
            block={ready ? 1 : 28}
            animateFrom={ready ? 56 : undefined}
            duration={1900}
            seed={domain.length * 7 + 3}
            style={{ maxHeight: "calc(100vh - 380px)" }}
          >
            {analyzing && <div className="media-scan" />}
            <Ticks inset={14} />
            <div className="media-tl">
              {ready ? (
                <Badge tone="inverse">{t("download.ready")}</Badge>
              ) : error ? (
                <Badge tone="warning">{t("status.failed")}</Badge>
              ) : (
                <Badge tone="solid">
                  <span style={{ width: 6, height: 6, background: "#fff", animation: "se7en-blink .7s steps(1) infinite" }} />
                  {t("status.analyzing")}
                </Badge>
              )}
              {info?.is_live && <Badge tone="solid">LIVE</Badge>}
            </div>
            <div className="media-br label" style={{ color: "var(--fog-1)" }}>{readout}</div>
            {info?.duration ? <span className="thumb-badge" style={{ left: 30, right: "auto", bottom: 26 }}>{duration(info.duration)}</span> : null}
          </PixelImage>
        )}

        <div className="media-meta" style={{ opacity: ready ? 1 : 0.25, transition: "opacity var(--dur-slow)" }}>
          <span className="label row" style={{ gap: 10, flexWrap: "wrap" }}>
            <PlatformChip name={domain} />
            {info?.uploader && <span>· {info.uploader}</span>}
            {info?.duration ? <span>· {duration(info.duration)}</span> : null}
            {info?.upload_date && <span>· {info.upload_date}</span>}
            {info?.view_count != null && <span>· {num(info.view_count)} {t("download.views")}</span>}
            {info?.preset && <Badge tone="accent" icon={<Bookmark size={11} />}>{info.preset.name}</Badge>}
          </span>
          <h2 className="media-title selectable">{info?.title ?? (error ? t("download.unavailable") : "—")}</h2>
          {info && !isPlaylist && (
            <div className="facts">
              <Fact label={t("fact.source")} value={info.platform_name} />
              <Fact label={t("fact.kind")} value={t(`kind.${info.kind}`)} />
              <Fact label={t("fact.resolution")} value={info.max_height ? `${info.max_height}p` : info.width ? `${info.width}×${info.height}` : "—"} />
              <Fact label={t("fact.formats")} value={info.video_codecs.map((c) => codecLabel[c] ?? c).join(" · ") || info.ext?.toUpperCase() || "—"} />
              <Fact label={t("fact.audio")} value={info.audio_codecs.map((c) => codecLabel[c] ?? c).join(" / ") || (info.has_audio ? "✓" : t("common.none"))} />
              <Fact label={t("fact.size")} value={estimate ? `≈ ${bytes(estimate)}` : "—"} />
            </div>
          )}
          {info && info.warnings.length > 0 && <Notice kind="warning">{info.warnings.map((w, i) => <div key={i}>{w}</div>)}</Notice>}
          {info?.already && info.already.length > 0 && (
            <div className="already rise">
              <History size={15} style={{ color: "var(--accent)", flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: 600 }}>{t("download.already", { when: new Date(info.already[0].created_at * 1000).toLocaleDateString(t.lang) })}</div>
                <div className="faint mono ellipsis" style={{ fontSize: 11 }}>{[info.already[0].resolution, info.already[0].format, info.already[0].exists ? null : t("history.missing")].filter(Boolean).join(" · ")}</div>
              </div>
              {info.already[0].exists && info.already[0].file_path && (
                <>
                  <Button size="sm" variant="ghost" icon={<Play size={13} />} onClick={() => openInPlayer([info.already![0].file_path!])}>{t("player.play")}</Button>
                  <Button size="sm" variant="ghost" iconOnly icon={<FolderOpen size={13} />} tip={t("common.showInFolder")} onClick={() => api.post("/api/system/open", { path: info.already![0].file_path, reveal: true })} />
                </>
              )}
            </div>
          )}
        </div>
      </div>

      <div className="analyze-side">
        <div className="card">
          <div className="side-scroll">
            {!ready ? (
              <div className="log">
                <span className="label">{t("download.journal")}</span>
                {scanTask && !error ? (
                  <ScanProgress state={scanState} onCancel={cancelScan} />
                ) : (
                  <>
                {logLines.slice(0, error ? logLines.length : Math.max(1, logStep)).map((l, i) => (
                  <div key={i} className="log-row">
                    <span className="muted"><span className="prompt">›</span> {l}</span>
                    {error ? (i === logLines.length - 1 ? <span className="ko">{t("log.fail")}</span> : <span className="ok">ok</span>) : i < logStep - 1 ? <span className="ok">ok</span> : null}
                  </div>
                ))}
                {!error && <span className="cursor-block" />}
                  </>
                )}
                {error && (
                  <div style={{ marginTop: 6 }}>
                    <ErrorAlert
                      error={error}
                      actions={
                        <>
                          {error.retryable !== false && <Button size="sm" icon={<RotateCcw size={13} />} onClick={() => analyze(target)}>{t("common.retry")}</Button>}
                          {!scanFailed && <Button size="sm" icon={<Layers size={13} />} onClick={scanPage}>{t("download.scanPage")}</Button>}
                          {!!error.extra?.component && <Button size="sm" onClick={() => goSettings("components")}>{t("components.install")}</Button>}
                          {["login_required", "private", "age_restricted", "members_only"].includes(error.code) && <Button size="sm" onClick={() => goSettings("privacy")}>{t("download.cookieSettings")}</Button>}
                        </>
                      }
                    />
                  </div>
                )}
              </div>
            ) : isPlaylist || isGallery ? (
              <>
                <span className="label">{isPlaylist ? t("download.playlistOptions") : t("download.gallery", { n: info!.entry_count ?? info!.entries.length })}</span>
                {isPlaylist && <GenericOptions stacked {...{ t, mode, setMode, quality, setQuality, container, setContainer, audioFormat, setAudioFormat }} />}
              </>
            ) : (
              <>
                <div className="col" style={{ gap: 8, ...rise(0) }}>
                  <span className="label">{t("download.format")}</span>
                  <Segmented<Mode>
                    size="sm"
                    full
                    value={mode}
                    onChange={setMode}
                    options={[
                      { value: "av", label: t("mode.avShort"), icon: <Film size={12} />, disabled: !info!.has_video },
                      { value: "video", label: t("mode.videoShort"), icon: <Video size={12} />, disabled: !info!.has_video },
                      { value: "audio", label: t("mode.audioShort"), icon: <AudioLines size={12} />, disabled: !info!.has_audio },
                      { value: "image", label: t("mode.imageShort"), icon: <ImageIcon size={12} />, disabled: !info!.thumbnail },
                    ]}
                  />
                </div>

                {mode === "image" ? (
                  <>
                    <div className="q-list">
                      {["jpg", "png", "webp"].map((f, i) => (
                        <QRow key={f} i={i} on={imageFormat === f} onClick={() => setImageFormat(f)} label={f.toUpperCase()} desc={t(`image.${f}`)} size={null} />
                      ))}
                    </div>
                    <div className="field-hint">{t("download.imageHint")}</div>
                  </>
                ) : mode !== "audio" ? (
                  <>
                    <div className="q-list">
                      {info!.recommended && (
                        <QRow
                          i={0}
                          on={quality === info!.recommended.quality}
                          onClick={() => setQuality(info!.recommended!.quality)}
                          label={<><Wand2 size={15} style={{ color: "var(--accent)" }} /> Smart</>}
                          desc={`${info!.recommended.label ?? `${info!.recommended.quality}p`} · ${t("download.smartDesc")}`}
                          size={qualities.find((q) => String(q.height) === info!.recommended!.quality)?.size}
                          tip={info!.recommended.reasons.join("\n")}
                        />
                      )}
                      <QRow i={1} on={quality === "best"} onClick={() => setQuality("best")} label={t("quality.best")} desc={bestQ ? `${bestQ.label} · ${t("download.bestDesc")}` : ""} size={bestQ?.size} />
                      {qualities.map((q, i) => (
                        <QRow
                          key={q.height}
                          i={i + 2}
                          on={quality === String(q.height)}
                          onClick={() => setQuality(String(q.height))}
                          label={<>{q.label.replace(" (4K)", "").replace(" (8K)", "")}{q.height >= 2160 && <Badge tone="accent">{q.height >= 4320 ? "8K" : "4K"}</Badge>}{q.hdr && <Badge>HDR</Badge>}{needsH264(q) && <Badge>→ H.264</Badge>}</>}
                          desc={[q.width ? `${q.width}×${q.height}` : null, q.fps ? `${Math.round(q.fps)} fps` : null, q.codecs.map((c) => codecLabel[c] ?? c).join(" · ")].filter(Boolean).join(" · ")}
                          size={q.size}
                          approx={!q.size_exact}
                        />
                      ))}
                    </div>
                    {info!.recommended && quality === info!.recommended.quality && <div className="smart-why">{info!.recommended.reasons.join(" · ")}</div>}
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
                      <Field label={t("download.container")}>
                        <Select value={container} onChange={setContainer} options={["mp4", "mkv", "webm"].map((c) => ({ value: c, label: c.toUpperCase() }))} />
                      </Field>
                      <Field label={t("download.codec")}>
                        <Select
                          value={vcodec}
                          onChange={setVcodec}
                          options={codecOptions.map((c) => ({
                            value: c,
                            label: c === "compat" ? t("codec.compatShort") : c === "auto" ? t("codec.original") : codecLabel[c] ?? c,
                            hint: c === "compat" ? (needsH264(chosenQ) ? t("codec.reencode") : t("codec.native")) : c === "auto" ? undefined : chosenQ?.codecs.includes(c) ? t("codec.native") : t("codec.reencode"),
                          }))}
                        />
                      </Field>
                    </div>
                    <div className="field-hint">
                      {needsH264(chosenQ)
                        ? t(hwEncoder ? "download.compatConvertGpu" : "download.compatConvertCpu", { q: chosenQ?.label ?? "" })
                        : vcodec === "compat"
                          ? t("download.compatNative")
                          : reencode
                            ? t("download.reencodeWarn")
                            : vcodec === "auto"
                              ? t("download.originalHint")
                              : t("download.noReencode")}
                    </div>
                  </>
                ) : (
                  <>
                    <div className="q-list">
                      {AUDIO_ROWS.map((r, i) => {
                        const on = audioFormat === r.f && (!r.br || audioBitrate === r.br);
                        return (
                          <QRow
                            key={r.f + (r.br ?? "")}
                            i={i}
                            on={on}
                            onClick={() => {
                              setAudioFormat(r.f);
                              if (r.br) setAudioBitrate(r.br);
                            }}
                            label={r.f === "best" ? t("audio.originalShort") : r.f === "m4a" ? "AAC" : r.f.toUpperCase()}
                            desc={r.br ? `${r.br} kbps${sourceAbr && r.br > sourceAbr * 1.1 ? ` · ${t("audio.aboveSourceShort", { abr: Math.round(sourceAbr) })}` : ""}` : r.f === "best" ? t("audio.noConversion") : t("audio.lossless")}
                            size={audioSize(r.f, r.br)}
                            approx
                          />
                        );
                      })}
                    </div>
                    {(audioFormat === "flac" || audioFormat === "wav") && <div className="field-hint">{t("audio.losslessNote")}</div>}
                  </>
                )}
                <div className="divider" style={{ margin: "4px 0" }} />
                <label className="check-row" onClick={() => setEmbedMeta(!embedMeta)}>
                  <Check on={embedMeta} />
                  {t("download.metadataShort")}
                </label>
                <div className="row" style={{ gap: 8 }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <Select value={presetId} options={presetOptions} onChange={(v) => { setPresetId(v); const p = presets.find((x) => x.id === v); if (p) applyOptions(p.options); }} />
                  </div>
                  <Button iconOnly icon={<Bookmark size={15} />} tip={t("download.savePreset")} onClick={() => setSavePreset(true)} style={{ ["--h" as any]: "44px" }} />
                </div>
              </>
            )}
          </div>
          {ready && (
            <div className="side-foot">
              <FolderRow t={t} value={folder} onPick={pickFolder} />
              <Button variant="primary" size="xl" full icon={<Download size={18} />} loading={adding} disabled={isPlaylist && selected.size === 0} onClick={downloadCurrent}>
                {isPlaylist ? t("download.addSelected", { n: selected.size }) : `${t("download.download")}${estimate ? ` · ${bytes(estimate)}` : ""}`}
              </Button>
            </div>
          )}
        </div>
      </div>

      <SavePresetModal
        open={savePreset}
        onClose={() => setSavePreset(false)}
        platform={info?.platform}
        options={buildOptions()}
        onSaved={(p) => {
          setPresets((ps) => [...ps.filter((x) => x.id !== p.id), p]);
          setPresetId(p.id);
          toast({ kind: "success", title: t("preset.saved", { name: p.name }) });
        }}
      />
    </div>
  );
}

// ------------------------------------------------------------------ pieces
// ================================================================ home (Studio Glass)
/** Gap between the hero and what sits in its notches, and the radius shared by every corner of the shape. */
const NOTCH_GAP = 10;
const HERO_RADIUS = 30;
const INTRO_NOTCH = { w: 316, h: 116 };

/** The red notched hero: the top bar's two pill clusters sit in its top notches (they are measured, so the cut
 *  follows them), the intro card in its bottom-left one. Big Anton titles cross the artwork. */
function HomeHero({ mode, setMode }: { mode: "av" | "audio" | "image"; setMode: (m: "av" | "audio" | "image") => void }) {
  const t = useT();
  const ref = useRef<HTMLDivElement>(null);
  const [notches, setNotches] = useState<Notches>({ bl: INTRO_NOTCH });
  useLayoutEffect(() => {
    const hero = ref.current;
    const left = document.querySelector(".topbar-l");
    const right = document.querySelector(".topbar-r");
    if (!hero || !left || !right) return;
    const measure = () => {
      const h = hero.getBoundingClientRect();
      const l = left.getBoundingClientRect();
      const r = right.getBoundingClientRect();
      // the top bar sits in the hero only when nothing (a banner) pushed the hero below it
      const overlap = l.bottom > h.top + HERO_RADIUS;
      setNotches({
        ...(overlap && {
          tl: { w: Math.round(l.right - h.left + NOTCH_GAP), h: Math.round(l.bottom - h.top + NOTCH_GAP) },
          tr: { w: Math.round(h.right - r.left + NOTCH_GAP), h: Math.round(r.bottom - h.top + NOTCH_GAP) },
        }),
        bl: INTRO_NOTCH,
      });
    };
    measure();
    const ro = new ResizeObserver(measure);
    // the home container too: a banner appearing / closing moves the hero without resizing it
    [hero, left, right, hero.parentElement ?? hero].forEach((el) => ro.observe(el));
    return () => ro.disconnect();
  }, []);
  const chips: { id: "av" | "audio" | "image"; key: string }[] = [
    { id: "av", key: "home.chipVideo" },
    { id: "audio", key: "home.chipAudio" },
    { id: "image", key: "home.chipImage" },
  ];
  return (
    <div className="hero-wrap" ref={ref}>
      <CutoutPanel notches={notches} radius={HERO_RADIUS} className="hero">
        <div className="hero-glow" />
        <img className="hero-art" src={heroArt} alt="" draggable={false} />
        <div className="hero-title hero-title-a" style={rise(0)}>{t("download.h1a")}</div>
        <div className="hero-side" style={rise(1)}>
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            {chips.map((c) => (
              <button key={c.id} className={`hero-chip ${mode === c.id ? "on" : ""}`} aria-pressed={mode === c.id} onClick={() => setMode(c.id)}>
                {t(c.key)}
              </button>
            ))}
          </div>
        </div>
        <div className="hero-title hero-title-b" style={rise(2)}>{t("download.h1b")}</div>
      </CutoutPanel>
    </div>
  );
}

/** Bottom-left column: it starts inside the hero's notch. The card shows the last thing downloaded. */
function HomeIntro() {
  const t = useT();
  const jobs = useApp((s) => s.jobs);
  const setPage = useApp((s) => s.setPage);
  const last = useMemo(
    () =>
      Object.values(jobs)
        .filter((j) => j.kind === "download" && j.status === "completed" && j.thumbnail)
        .sort((a, b) => (b.finished_at ?? b.updated_at) - (a.finished_at ?? a.updated_at))[0],
    [jobs],
  );
  return (
    <div className="home-intro" style={{ marginTop: -INTRO_NOTCH.h + 20, width: INTRO_NOTCH.w - 22, ...rise(1) }}>
      <div className="row" style={{ alignItems: "flex-start", gap: 12 }}>
        <h3 className="home-h3">
          {t("home.introA")}
          <br />
          {t("home.introB")}
        </h3>
        <div className="disc-stack">
          <span className="disc-mini"><Video size={15} /></span>
          <span className="disc-mini"><AudioLines size={15} /></span>
        </div>
      </div>
      <p className="home-intro-text">{t("home.introText")}</p>
      <button className="art-card" onClick={() => setPage("history")} aria-label={t("nav.history")}>
        {last ? (
          <img src={thumbUrl(last.thumbnail)} alt="" draggable={false} />
        ) : (
          <div className="art-fill art-1"><Download size={34} /></div>
        )}
        <span className="art-card-caption">{last ? last.title || t("home.lastDownload") : t("home.noDownload")}</span>
        <span className="art-card-go"><ArrowUpRight size={16} /></span>
      </button>
    </div>
  );
}

/** Right column: the other tools, each with an art tile (the reference's feature list). */
function HomeFeatures() {
  const t = useT();
  const setPage = useApp((s) => s.setPage);
  const items: { page: "upscale" | "subtitles" | "convert"; icon: ReactNode; art: number; key: string }[] = [
    { page: "upscale", icon: <Sparkles size={22} />, art: 2, key: "upscale" },
    { page: "subtitles", icon: <Captions size={22} />, art: 3, key: "subtitles" },
    { page: "convert", icon: <RefreshCcw size={22} />, art: 4, key: "convert" },
  ];
  return (
    <div className="home-features" style={rise(3)}>
      {items.map((f) => (
        <button key={f.page} className="feature-row" onClick={() => setPage(f.page)}>
          <span className="feature-tile"><span className={`art-fill art-${f.art}`}>{f.icon}</span></span>
          <span className="feature-text">
            <span className="feature-title">{t(`home.feat.${f.key}.title`)}</span>
            <span className="feature-desc">{t(`home.feat.${f.key}.desc`)}</span>
          </span>
          <ArrowRight size={16} className="feature-go" />
        </button>
      ))}
    </div>
  );
}

function ScanProgress({ state, onCancel }: { state?: TaskState; onCancel: () => void }) {
  const t = useT();
  const p = state?.progress ?? {};
  const step = p.step === "render" ? t("download.scanRender") : p.step === "check" ? t("download.scanCheck") : t("download.scanReading");
  return (
    <div className="scan-progress">
      <div className="log-row">
        <span className="muted"><span className="prompt">›</span> {step}</span>
      </div>
      {p.label && <div className="mono faint ellipsis" style={{ fontSize: 11 }}>{p.label}</div>}
      <Progress value={p.percent ?? null} state="active" />
      <div className="row" style={{ justifyContent: "space-between", gap: 10 }}>
        <span className="label">{t("download.scanCount", { pages: p.pages ?? 0, found: p.found ?? 0 })}</span>
        <Button size="sm" variant="ghost" icon={<X size={13} />} onClick={onCancel}>{t("common.cancel")}</Button>
      </div>
      <span className="faint" style={{ fontSize: 11.5 }}>{t("download.scanHint")}</span>
    </div>
  );
}

/** Videos of a playlist; after a site scan, grouped by the page they were found on. */
function EntryList({ entries, selected, setSelected, grouped }: { entries: Entry[]; selected: Set<number>; setSelected: (s: Set<number>) => void; grouped: boolean }) {
  const t = useT();
  const usable = entries.flatMap((e, i) => (e.protected ? [] : [i]));
  const allOn = usable.length > 0 && usable.every((i) => selected.has(i));
  const toggle = (ids: number[], on: boolean) => {
    const s = new Set(selected);
    ids.forEach((i) => (on ? s.add(i) : s.delete(i)));
    setSelected(s);
  };
  const groups: { page: string; title: string; ids: number[] }[] = [];
  entries.forEach((e, i) => {
    const key = grouped ? e.page ?? "" : "";
    let g = groups.find((x) => x.page === key);
    if (!g) groups.push((g = { page: key, title: e.page_title || key, ids: [] }));
    g.ids.push(i);
  });
  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}>
        <Check on={allOn} onClick={() => setSelected(allOn ? new Set() : new Set(usable))} />
        <span className="label">{t("download.selected", { n: selected.size, total: usable.length })}</span>
      </div>
      <div className="entry-list" style={{ maxHeight: "calc(100vh - 360px)" }}>
        {groups.map((g) => {
          const ids = g.ids.filter((i) => !entries[i].protected);
          const gOn = ids.length > 0 && ids.every((i) => selected.has(i));
          return (
            <div key={g.page || "all"}>
              {grouped && (
                <div className="entry-group" onClick={() => ids.length && toggle(ids, !gOn)}>
                  <Check on={gOn} />
                  <span className="ellipsis" style={{ flex: 1 }} title={g.page}>{g.title}</span>
                  <span className="mono faint">{t("download.nItems", { n: g.ids.length })}</span>
                </div>
              )}
              {g.ids.map((i) => {
                const e = entries[i];
                return (
                  <div className={`entry${e.protected ? " entry-off" : ""}`} key={e.url + i} title={e.protected ? t("download.protectedTip") : e.url} onClick={() => !e.protected && toggle([i], !selected.has(i))}>
                    <Check on={selected.has(i)} />
                    <span className="mono faint" style={{ width: 28 }}>{String(i + 1).padStart(2, "0")}</span>
                    <div className="entry-thumb">{e.thumbnail && <img src={e.thumbnail} alt="" loading="lazy" referrerPolicy="no-referrer" onError={(ev) => (ev.currentTarget.style.display = "none")} />}</div>
                    <span className="ellipsis" style={{ flex: 1, fontWeight: 600 }}>{e.title}</span>
                    {e.protected ? <Badge tone="warning">{t("download.protected")}</Badge> : <span className="mono faint">{e.duration ? duration(e.duration) : ""}</span>}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </>
  );
}

function Fact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="fact">
      <div className="fact-label">{label}</div>
      <div className="fact-value ellipsis">{value}</div>
    </div>
  );
}

function QRow({ on, onClick, label, desc, size, approx, tip, i }: { on: boolean; onClick: () => void; label: ReactNode; desc?: string; size?: number | null; approx?: boolean; tip?: string; i: number }) {
  return (
    <button className={`q-row ${on ? "on" : ""}`} onClick={onClick} data-tip={tip} style={{ animationDelay: `${60 + i * 45}ms` }}>
      <span className="q-row-main">
        <span className="q-row-label">{label}</span>
        {desc && <span className="q-row-desc ellipsis">{desc}</span>}
      </span>
      <Badge mono>{size ? `${approx ? "≈ " : ""}${bytes(size)}` : "—"}</Badge>
    </button>
  );
}

function FolderRow({ t, value, onPick }: { t: ReturnType<typeof useT>; value: string; onPick: () => void }) {
  return (
    <button className="folder-row" onClick={onPick} data-tip={t("download.changeFolder")} style={{ flex: 1, minWidth: 0 }}>
      <FolderOpen size={16} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />
      <span style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
        <span className="label" style={{ fontSize: 9 }}>{t("download.saveTo")}</span>
        <span className="ellipsis mono" style={{ color: "var(--text-primary)" }}>{value || "—"}</span>
      </span>
    </button>
  );
}

function GenericOptions({ t, mode, setMode, quality, setQuality, container, setContainer, audioFormat, setAudioFormat, stacked }: any) {
  return (
    <div className="col" style={{ gap: 14, ...(stacked ? {} : { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }) }}>
      <Field label={t("download.mode")}>
        <Segmented value={mode} onChange={setMode} full options={[{ value: "av", label: t("mode.avShort") }, { value: "video", label: t("mode.videoShort") }, { value: "audio", label: t("mode.audioShort") }]} />
      </Field>
      {mode !== "audio" ? (
        <>
          <Field label={t("download.quality")} hint={t("download.genericQualityHint")}>
            <Select value={quality} onChange={setQuality} options={GENERIC_QUALITIES.map((q) => ({ value: q, label: q === "best" ? t("quality.best") : `${q}p` }))} />
          </Field>
          <Field label={t("download.container")}>
            <Select value={container} onChange={setContainer} options={["mp4", "mkv", "webm"].map((c) => ({ value: c, label: c.toUpperCase() }))} />
          </Field>
        </>
      ) : (
        <Field label={t("download.audioFormat")}>
          <Select value={audioFormat} onChange={setAudioFormat} options={["mp3", "m4a", "opus", "flac", "wav", "best"].map((f) => ({ value: f, label: f === "best" ? t("audio.originalShort") : f.toUpperCase() }))} />
        </Field>
      )}
    </div>
  );
}

function ActiveDownloads() {
  const t = useT();
  const jobs = useApp((s) => s.jobs);
  const setPage = useApp((s) => s.setPage);
  const active = Object.values(jobs)
    .filter((j) => j.kind === "download" && ["downloading", "analyzing", "processing", "waiting"].includes(j.status))
    .sort((a, b) => b.priority - a.priority || a.position - b.position)
    .slice(0, 3);
  if (!active.length) return null;
  return (
    <motion.div initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} className="card home-panel" style={{ padding: "14px 16px" }}>
      <div className="row" style={{ marginBottom: 10 }}>
        <span className="label">{t("download.activeTitle", { n: active.length })}</span>
        <div className="spacer" />
        <Button size="sm" variant="ghost" onClick={() => setPage("queue")}>{t("download.viewQueue")}</Button>
      </div>
      <div className="col" style={{ gap: 12 }}>
        {active.map((j) => (
          <div key={j.id}>
            <div className="row" style={{ marginBottom: 7 }}>
              <span className="ellipsis" style={{ fontWeight: 600, flex: 1 }}>{j.title || j.source}</span>
              <span className="mono faint">{speed(j.progress.speed)}</span>
            </div>
            <Progress value={j.progress.percent} state={j.status === "waiting" ? undefined : "active"} showValue />
          </div>
        ))}
      </div>
    </motion.div>
  );
}

function SavePresetModal({ open, onClose, platform, options, onSaved }: { open: boolean; onClose: () => void; platform?: string; options: DownloadOptions; onSaved: (p: Preset) => void }) {
  const t = useT();
  const [name, setName] = useState("");
  const [auto, setAuto] = useState(true);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (open) setName(platform ? `Preset ${platform.charAt(0).toUpperCase() + platform.slice(1)}` : "Preset");
  }, [open, platform]);
  const save = async () => {
    setBusy(true);
    try {
      const p = await api.post<Preset>("/api/presets", { name, platforms: platform && auto ? [platform] : [], options, auto_apply: auto });
      onSaved(p);
      onClose();
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow={t("preset.eyebrow")}
      title={t("preset.saveTitle")}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>{t("common.cancel")}</Button>
          <Button variant="primary" loading={busy} disabled={!name.trim()} onClick={save}>{t("common.save")}</Button>
        </>
      }
    >
      <p style={{ marginBottom: 16 }}>{t("preset.saveBody")}</p>
      <Field label={t("preset.name")}>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
      </Field>
      {platform && (
        <div className="toggle-row" style={{ marginTop: 10 }}>
          <div className="toggle-row-text">
            <div className="toggle-row-title">{t("preset.autoApply", { platform })}</div>
            <div className="toggle-row-desc">{t("preset.autoApplyDesc")}</div>
          </div>
          <Switch checked={auto} onChange={setAuto} />
        </div>
      )}
      <pre className="detail-pre">{JSON.stringify(options, null, 2)}</pre>
    </Modal>
  );
}
