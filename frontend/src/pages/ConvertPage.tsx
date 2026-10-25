import clsx from "clsx";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, AudioLines, Captions, FileVideo, FolderOpen, Image as ImageIcon, Play, Plus, RefreshCcw, Trash2, Video, X } from "lucide-react";
import { ReactNode, useEffect, useMemo, useState } from "react";
import { api, thumbUrl } from "../api/client";
import type { ConvertTarget, Job, LocalFile } from "../api/types";
import { Badge, Button, Field, Notice, PageHead, Progress, Segmented, Select, StatusBadge, Switch, Ticks } from "../components/ui";
import { useFileIntake } from "../hooks/useFileIntake";
import { useT } from "../i18n";
import { bytes, codecLabel, duration } from "../lib/format";
import { useApp } from "../store/app";

type Cat = "video" | "audio" | "image" | "subtitle";
const CAT_ICON: Record<Cat, ReactNode> = { video: <Video size={14} />, audio: <AudioLines size={14} />, image: <ImageIcon size={14} />, subtitle: <Captions size={14} /> };
const SIZE_PRESETS = [8, 10, 25, 50, 100, 500];

function fileKindLabel(f: LocalFile): Cat | "animated" {
  return (f.kind ?? "video") as Cat | "animated";
}

export function ConvertPage() {
  const t = useT();
  const settings = useApp((s) => s.settings);
  const jobs = useApp((s) => s.jobs);
  const toast = useApp((s) => s.toast);
  const setPage = useApp((s) => s.setPage);
  const openInPlayer = useApp((s) => s.openInPlayer);
  const [files, setFiles] = useState<LocalFile[]>([]);
  const [targets, setTargets] = useState<ConvertTarget[]>([]);
  const [allowed, setAllowed] = useState<Record<string, string[]>>({});
  const [cat, setCat] = useState<Cat>("video");
  const [target, setTarget] = useState("mp4");
  const [quality, setQuality] = useState("auto");
  const [resolution, setResolution] = useState("0");
  const [fps, setFps] = useState("0");
  const [sizeMb, setSizeMb] = useState<string>("");
  const [removeAudio, setRemoveAudio] = useState(false);
  const [abr, setAbr] = useState("0");
  const [sampleRate, setSampleRate] = useState("keep");
  const [channels, setChannels] = useState("keep");
  const [normalize, setNormalize] = useState(false);
  const [imgQuality, setImgQuality] = useState(90);
  const [maxSize, setMaxSize] = useState("0");
  const [strip, setStrip] = useState(false);
  const [gifWidth, setGifWidth] = useState("480");
  const [outputDir, setOutputDir] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.get<{ targets: ConvertTarget[]; allowed: Record<string, string[]> }>("/api/convert/catalog").then((r) => {
      setTargets(r.targets);
      setAllowed(r.allowed);
    });
  }, []);

  const intake = useFileIntake({
    page: "convert",
    kind: "any",
    onFiles: (fs) =>
      setFiles((prev) => {
        const known = new Set(prev.map((p) => p.path));
        return [...prev, ...fs.filter((f) => !known.has(f.path))];
      }),
  });

  // What every selected file can become (intersection).
  const possible = useMemo(() => {
    if (!files.length) return new Set<string>(["video", "audio", "image", "subtitle"]);
    let set: Set<string> | null = null;
    for (const f of files) {
      const kinds = new Set<string>(allowed[fileKindLabel(f)] ?? []);
      if (f.kind === "video" && !f.subtitles) kinds.delete("subtitle");
      if (f.kind === "video" && !f.has_audio) kinds.delete("audio");
      const prev: Set<string> | null = set;
      set = prev ? new Set<string>([...prev].filter((k: string) => kinds.has(k))) : kinds;
    }
    return set ?? new Set<string>();
  }, [files, allowed]);

  useEffect(() => {
    if (!possible.has(cat)) {
      const first = (["video", "audio", "image", "subtitle"] as Cat[]).find((c) => possible.has(c));
      if (first) setCat(first);
    }
  }, [possible]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const cur = targets.find((x) => x.id === target);
    if (!cur || cur.kind !== cat) {
      const first = targets.find((x) => x.kind === cat);
      if (first) setTarget(first.id);
    }
  }, [cat, targets]); // eslint-disable-line react-hooks/exhaustive-deps

  const tgt = targets.find((x) => x.id === target);
  const lossyAudio = tgt?.kind === "audio" && !tgt.lossless;
  const imgLossy = tgt?.kind === "image" && ["jpg", "webp", "avif", "heic"].includes(tgt.id);

  const options = () => {
    const o: Record<string, unknown> = {};
    if (tgt?.kind === "video") {
      if (tgt.id === "gif") {
        o.gif_width = Number(gifWidth);
        o.fps = Number(fps) || 15;
      } else {
        o.quality = sizeMb ? "high" : quality;
        if (Number(resolution)) o.resolution = Number(resolution);
        if (Number(fps)) o.fps = Number(fps);
        if (sizeMb) o.target_size_mb = Number(sizeMb);
        o.remove_audio = removeAudio;
      }
      if (Number(abr)) o.audio_bitrate = Number(abr);
      o.hw = settings?.tools?.use_gpu_encoder !== false;
    } else if (tgt?.kind === "audio") {
      if (Number(abr) && lossyAudio) o.audio_bitrate = Number(abr);
      if (sampleRate !== "keep") o.sample_rate = Number(sampleRate);
      if (channels !== "keep") o.channels = Number(channels);
      if (sizeMb && lossyAudio) o.target_size_mb = Number(sizeMb);
      o.normalize = normalize;
      o.strip_metadata = strip;
    } else if (tgt?.kind === "image") {
      o.image_quality = imgQuality;
      if (Number(maxSize)) o.max_size = Number(maxSize);
      o.strip_metadata = strip;
    }
    if (outputDir) o.output_dir = outputDir;
    return o;
  };

  const start = async () => {
    if (!files.length || !tgt) return;
    setBusy(true);
    try {
      const r = await api.post<{ jobs: Job[] }>("/api/convert/jobs", { paths: files.map((f) => f.path), target: tgt.id, options: options() });
      toast({ kind: "success", title: t("convert.queued", { n: r.jobs.length }), action: { label: t("download.viewQueue"), run: () => setPage("queue") } });
    } catch (e: any) {
      toast({ kind: "error", title: e.message, body: e.hint });
    } finally {
      setBusy(false);
    }
  };

  const recent = useMemo(() => Object.values(jobs).filter((j) => j.kind === "convert").sort((a, b) => b.created_at - a.created_at).slice(0, 6), [jobs]);
  const totalSize = files.reduce((a, f) => a + (f.size ?? 0), 0);

  return (
    <div className="page-inner wide">
      <PageHead
        eyebrow={t("convert.eyebrow")}
        title={t("convert.title")}
        echo={t("convert.echo")}
        right={<Button icon={<Plus size={15} />} onClick={intake.pick}>{t("upscale.addFiles")}</Button>}
      />
      <input {...intake.inputProps} />
      <div className="tool-layout">
        <div className="col rise" style={{ gap: 14, minWidth: 0, animationDelay: "60ms" }}>
          {files.length === 0 ? (
            <div className="drop-zone" onClick={intake.pick}>
              <Ticks inset={14} />
              <div>
                <div className="empty-icon" style={{ borderColor: "var(--accent)", color: "var(--accent)" }}><RefreshCcw size={24} /></div>
                <div className="empty-title">{t("convert.emptyTitle")}</div>
                <div className="faint" style={{ maxWidth: 460, margin: "0 auto" }}>{t("convert.emptyBody")}</div>
                <Button variant="primary" style={{ marginTop: 18 }} icon={<Plus size={16} />} onClick={(e) => { e.stopPropagation(); intake.pick(); }}>{t("upscale.addFiles")}</Button>
                <div className="label" style={{ marginTop: 14 }}>{t("upscale.dropHint")}</div>
              </div>
            </div>
          ) : (
            <div className="card">
              <div className="row" style={{ padding: "12px 16px", borderBottom: "1px solid var(--border-subtle)" }}>
                <span className="label">{t("upscale.files", { n: files.length })} · {bytes(totalSize)}</span>
                <span className="spacer" />
                <Button size="sm" variant="ghost" icon={<Plus size={13} />} onClick={intake.pick}>{t("upscale.addFiles")}</Button>
                <Button size="sm" variant="ghost" iconOnly icon={<Trash2 size={13} />} tip={t("upscale.clearFiles")} onClick={() => setFiles([])} />
              </div>
              <div className="file-list">
                <AnimatePresence initial={false}>
                  {files.map((f) => (
                    <motion.div key={f.path} layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="file-row">
                      <div className="pl-thumb">{f.thumbnail ? <img src={thumbUrl(f.thumbnail)} alt="" /> : CAT_ICON[(f.kind === "animated" ? "image" : f.kind ?? "video") as Cat]}</div>
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div className="ellipsis" style={{ fontWeight: 600, fontSize: 13 }} title={f.path}>{f.name}</div>
                        <div className="mono faint" style={{ fontSize: 10.5 }}>
                          {[
                            f.width && f.height ? `${f.width}×${f.height}` : null,
                            f.duration ? duration(f.duration) : null,
                            f.vcodec ? codecLabel[f.vcodec] ?? f.vcodec.toUpperCase() : null,
                            f.acodec ? codecLabel[f.acodec] ?? f.acodec.toUpperCase() : null,
                            bytes(f.size),
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        </div>
                      </div>
                      <ArrowRight size={14} className="faint" />
                      <Badge tone="accent" mono>{tgt ? `.${tgt.ext}` : "—"}</Badge>
                      {f.kind !== "subtitle" && <Button size="sm" variant="ghost" iconOnly icon={<Play size={13} />} tip={t("player.play")} onClick={() => openInPlayer([f.path])} />}
                      <Button size="sm" variant="ghost" iconOnly icon={<X size={13} />} onClick={() => setFiles((xs) => xs.filter((x) => x.path !== f.path))} />
                    </motion.div>
                  ))}
                </AnimatePresence>
              </div>
            </div>
          )}

          {recent.length > 0 && (
            <div className="card" style={{ padding: "6px 0" }}>
              <div className="label" style={{ padding: "10px 16px" }}>{t("convert.recent")}</div>
              {recent.map((j) => (
                <div key={j.id} className="file-row">
                  <FileVideo size={15} className="faint" />
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className="row" style={{ gap: 8 }}>
                      <span className="ellipsis" style={{ fontWeight: 600, fontSize: 13, flex: 1 }}>{j.title}</span>
                      <StatusBadge status={j.status} />
                    </div>
                    {["processing", "waiting"].includes(j.status) ? (
                      <div style={{ marginTop: 6 }}><Progress value={j.progress?.percent} state="active" /></div>
                    ) : j.status === "completed" ? (
                      <div className="mono faint" style={{ fontSize: 10.5 }}>{[j.result?.format, bytes(j.result?.size), j.result?.meta?.remux ? t("convert.lossless") : null].filter(Boolean).join(" · ")}</div>
                    ) : j.error ? (
                      <div className="faint" style={{ fontSize: 12, color: "var(--danger)" }}>{j.error.message}</div>
                    ) : null}
                  </div>
                  {j.status === "completed" && j.result?.file_path && (
                    <>
                      <Button size="sm" variant="ghost" iconOnly icon={<Play size={13} />} tip={t("player.play")} onClick={() => openInPlayer([j.result!.file_path])} />
                      <Button size="sm" variant="ghost" iconOnly icon={<FolderOpen size={13} />} tip={t("common.showInFolder")} onClick={() => api.post("/api/system/open", { path: j.result!.file_path, reveal: true })} />
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="side-panel rise" style={{ animationDelay: "120ms" }}>
          <div className="card card-pad col" style={{ gap: 14 }}>
            <span className="label">{t("convert.to")}</span>
            <div className="cat-seg"><Segmented<Cat>
              value={cat}
              onChange={setCat}
              full
              options={(["video", "audio", "image", "subtitle"] as Cat[]).map((c) => ({ value: c, label: t(`convert.cat.${c}`), icon: CAT_ICON[c], disabled: !possible.has(c) }))}
            /></div>
            <div className="target-grid">
              {targets
                .filter((x) => x.kind === cat)
                .map((x) => (
                  <button key={x.id} className={clsx("target", target === x.id && "on")} onClick={() => setTarget(x.id)}>
                    <span className="target-ext">.{x.ext}</span>
                    <span className="target-label">{x.label}</span>
                    <span className="target-note">{t(`convert.note.${x.note}`)}</span>
                  </button>
                ))}
            </div>
            {files.some((f) => f.kind === "video") && cat === "audio" && <Notice>{t("convert.extractNote")}</Notice>}
          </div>

          {tgt && tgt.kind !== "subtitle" && (
            <div className="card card-pad col" style={{ gap: 14 }}>
              <span className="label">{t("convert.options")}</span>
              {tgt.kind === "video" && tgt.id !== "gif" && (
                <>
                  <Field label={t("convert.quality")} hint={quality === "auto" ? t("convert.qualityAutoHint") : undefined}>
                    <Segmented value={quality} onChange={setQuality} full options={["auto", "high", "normal", "small"].map((q) => ({ value: q, label: t(`convert.q.${q}`) }))} />
                  </Field>
                  <div className="options-grid">
                    <Field label={t("convert.resolution")}>
                      <Select value={resolution} onChange={setResolution} options={["0", "2160", "1440", "1080", "720", "480", "360"].map((r) => ({ value: r, label: r === "0" ? t("convert.original") : `${r}p` }))} />
                    </Field>
                    <Field label={t("convert.fps")}>
                      <Select value={fps} onChange={setFps} options={["0", "60", "30", "25", "24"].map((r) => ({ value: r, label: r === "0" ? t("convert.original") : `${r} fps` }))} />
                    </Field>
                  </div>
                  <Field label={t("convert.targetSize")} hint={t("convert.targetSizeHint")}>
                    <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
                      <input className="input input-sm mono" style={{ width: 100 }} type="number" min={1} placeholder="—" value={sizeMb} onChange={(e) => setSizeMb(e.target.value)} />
                      <span className="faint">MB</span>
                      {SIZE_PRESETS.map((n) => (
                        <button key={n} className={clsx("chip", Number(sizeMb) === n && "on")} onClick={() => setSizeMb(Number(sizeMb) === n ? "" : String(n))}>{n}</button>
                      ))}
                    </div>
                  </Field>
                  <div className="toggle-row">
                    <div className="toggle-row-text"><div className="toggle-row-title">{t("convert.removeAudio")}</div></div>
                    <Switch checked={removeAudio} onChange={setRemoveAudio} />
                  </div>
                </>
              )}
              {tgt.id === "gif" && (
                <div className="options-grid">
                  <Field label={t("convert.width")}>
                    <Select value={gifWidth} onChange={setGifWidth} options={["320", "480", "640", "800", "1080"].map((w) => ({ value: w, label: `${w}px` }))} />
                  </Field>
                  <Field label={t("convert.fps")}>
                    <Select value={fps === "0" ? "15" : fps} onChange={setFps} options={["10", "12", "15", "20", "25", "30"].map((r) => ({ value: r, label: `${r} fps` }))} />
                  </Field>
                </div>
              )}
              {tgt.kind === "audio" && (
                <>
                  {lossyAudio && (
                    <Field label={t("convert.bitrate")} hint={t("convert.bitrateHint")}>
                      <Select value={abr} onChange={setAbr} options={["0", "96", "128", "160", "192", "256", "320"].map((b) => ({ value: b, label: b === "0" ? t("convert.auto") : `${b} kbps` }))} />
                    </Field>
                  )}
                  <div className="options-grid">
                    <Field label={t("convert.sampleRate")}>
                      <Select value={sampleRate} onChange={setSampleRate} options={["keep", "44100", "48000", "96000"].map((r) => ({ value: r, label: r === "keep" ? t("convert.original") : `${Number(r) / 1000} kHz` }))} />
                    </Field>
                    <Field label={t("convert.channels")}>
                      <Select value={channels} onChange={setChannels} options={[{ value: "keep", label: t("convert.original") }, { value: "2", label: t("convert.stereo") }, { value: "1", label: t("convert.mono") }]} />
                    </Field>
                  </div>
                  {lossyAudio && (
                    <Field label={t("convert.targetSize")}>
                      <div className="row" style={{ gap: 6 }}>
                        <input className="input input-sm mono" style={{ width: 100 }} type="number" min={1} placeholder="—" value={sizeMb} onChange={(e) => setSizeMb(e.target.value)} />
                        <span className="faint">MB</span>
                      </div>
                    </Field>
                  )}
                  <div className="toggle-row">
                    <div className="toggle-row-text">
                      <div className="toggle-row-title">{t("convert.normalize")}</div>
                      <div className="toggle-row-desc">{t("convert.normalizeDesc")}</div>
                    </div>
                    <Switch checked={normalize} onChange={setNormalize} />
                  </div>
                </>
              )}
              {tgt.kind === "image" && (
                <>
                  {imgLossy && (
                    <Field label={`${t("convert.imageQuality")} · ${imgQuality}`}>
                      <input type="range" min={40} max={100} value={imgQuality} onChange={(e) => setImgQuality(Number(e.target.value))} />
                    </Field>
                  )}
                  <Field label={t("convert.maxSize")}>
                    <Select value={maxSize} onChange={setMaxSize} options={["0", "4096", "2048", "1920", "1280", "1024", "512"].map((r) => ({ value: r, label: r === "0" ? t("convert.original") : `${r}px` }))} />
                  </Field>
                </>
              )}
              {(tgt.kind === "image" || tgt.kind === "audio") && (
                <div className="toggle-row">
                  <div className="toggle-row-text">
                    <div className="toggle-row-title">{t("convert.strip")}</div>
                    <div className="toggle-row-desc">{t("convert.stripDesc")}</div>
                  </div>
                  <Switch checked={strip} onChange={setStrip} />
                </div>
              )}
            </div>
          )}

          <div className="card card-pad col" style={{ gap: 12 }}>
            <button
              className="folder-row"
              onClick={async () => {
                const r = await api.post<{ path: string | null }>("/api/system/pick-folder", { initial: outputDir || settings?.tools?.convert_output_folder || "" });
                if (!r.path) return;
                setOutputDir(r.path);
                useApp.getState().saveSettings({ tools: { convert_output_folder: r.path } }).catch(() => undefined); // remembered
              }}
            >
              <FolderOpen size={15} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />
              <span style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="label" style={{ fontSize: 9 }}>{t("download.saveTo")}</span>
                <span className="ellipsis mono" style={{ color: "var(--text-primary)" }}>{outputDir || settings?.tools?.convert_output_folder || t("upscale.nextToSource")}</span>
              </span>
            </button>
            <Button variant="primary" size="xl" full icon={<RefreshCcw size={18} />} loading={busy} disabled={!files.length || !tgt} onClick={start}>
              {files.length > 1 ? t("convert.startN", { n: files.length }) : t("convert.start")}
            </Button>
            <div className="field-hint">{t("convert.safe")}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
