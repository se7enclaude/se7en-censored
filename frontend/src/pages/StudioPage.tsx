import clsx from "clsx";
import { AnimatePresence, motion } from "motion/react";
import { AudioLines, FolderOpen, MicVocal, Play, Plus, Trash2, Video, WandSparkles, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, thumbUrl } from "../api/client";
import type { Job, LocalFile } from "../api/types";
import { Button, Field, Notice, PageHead, Progress, Segmented, Select, StatusBadge, Switch, Ticks } from "../components/ui";
import { useFileIntake } from "../hooks/useFileIntake";
import { useT } from "../i18n";
import { bytes, duration } from "../lib/format";
import { useApp } from "../store/app";

type Op = "separate" | "clean";
type Stems = "both" | "vocals" | "instrumental";
type Sound = "voice" | "other";
type Strength = "light" | "medium" | "strong";

interface StudioInfo {
  engine: string | null;
  gpu: boolean;
  models: Record<string, { installed: boolean; size: number }>;
}

const baseName = (p: string) => p.split(/[\\/]/).pop() ?? p;

/** Audio tools: separate the voice from the music (karaoke, a cappella) and clean up recordings. */
export function StudioPage() {
  const t = useT();
  const settings = useApp((s) => s.settings);
  const jobs = useApp((s) => s.jobs);
  const toast = useApp((s) => s.toast);
  const setPage = useApp((s) => s.setPage);
  const openInPlayer = useApp((s) => s.openInPlayer);
  const [files, setFiles] = useState<LocalFile[]>([]);
  const [op, setOp] = useState<Op>("separate");
  const [stems, setStems] = useState<Stems>("both");
  const [quality, setQuality] = useState<"normal" | "max">("normal");
  const [sound, setSound] = useState<Sound>("voice");
  const [strength, setStrength] = useState<Strength>("medium");
  const [hum, setHum] = useState("0");
  const [declick, setDeclick] = useState(false);
  const [normalize, setNormalize] = useState(false);
  const [format, setFormat] = useState("auto");
  const [keepVideo, setKeepVideo] = useState(true);
  const [outputDir, setOutputDir] = useState("");
  const [busy, setBusy] = useState(false);
  const [info, setInfo] = useState<StudioInfo | null>(null);

  useEffect(() => {
    api.get<StudioInfo>("/api/studio/info").then(setInfo).catch(() => undefined);
  }, []);

  const intake = useFileIntake({
    page: "studio",
    kind: "av",
    onFiles: (fs) => {
      const usable = fs.filter((f) => f.kind === "audio" || (f.kind === "video" && f.has_audio !== false));
      if (usable.length < fs.length) toast({ kind: "warning", title: t("studio.noSound", { n: fs.length - usable.length }) });
      setFiles((prev) => {
        const known = new Set(prev.map((p) => p.path));
        return [...prev, ...usable.filter((f) => !known.has(f.path))];
      });
    },
  });

  const hasVideo = files.some((f) => f.kind === "video");
  const needsModel = op === "separate" || (sound === "voice" && strength === "strong");
  const model = info?.models?.kim_vocal_2;

  const options = () => {
    const o: Record<string, unknown> = { op, format, keep_video: keepVideo };
    if (op === "separate") Object.assign(o, { stems, quality });
    else Object.assign(o, { sound, strength, hum: Number(hum), declick, normalize });
    if (outputDir) o.output_dir = outputDir;
    return o;
  };

  const start = async () => {
    if (!files.length) return;
    setBusy(true);
    try {
      const r = await api.post<{ jobs: Job[] }>("/api/studio/jobs", { paths: files.map((f) => f.path), options: options() });
      toast({ kind: "success", title: t("studio.queued", { n: r.jobs.length }), action: { label: t("download.viewQueue"), run: () => setPage("queue") } });
    } catch (e: any) {
      toast({ kind: "error", title: e.message, body: e.hint });
    } finally {
      setBusy(false);
    }
  };

  const pickOutput = async () => {
    const r = await api.post<{ path: string | null }>("/api/system/pick-folder", { initial: outputDir || settings?.tools?.studio_output_folder || "" });
    if (!r.path) return;
    setOutputDir(r.path);
    useApp.getState().saveSettings({ tools: { studio_output_folder: r.path } }).catch(() => undefined); // remembered
  };

  const recent = useMemo(() => Object.values(jobs).filter((j) => j.kind === "studio").sort((a, b) => b.created_at - a.created_at).slice(0, 6), [jobs]);
  const strengthHint = sound === "voice" ? t(`studio.voice.${strength}`) : t("studio.otherHint");

  return (
    <div className="page-inner wide">
      <PageHead
        eyebrow={t("studio.eyebrow")}
        title={t("studio.title")}
        echo={t("studio.echo")}
        right={<Button icon={<Plus size={15} />} onClick={intake.pick}>{t("upscale.addFiles")}</Button>}
      />
      <input {...intake.inputProps} />
      <div className="tool-layout">
        <div className="col rise" style={{ gap: 14, minWidth: 0, animationDelay: "60ms" }}>
          {files.length === 0 ? (
            <div className="drop-zone" onClick={intake.pick}>
              <Ticks inset={14} />
              <div>
                <div className="empty-icon" style={{ borderColor: "var(--accent)", color: "var(--accent)" }}><AudioLines size={24} /></div>
                <div className="empty-title">{t("studio.emptyTitle")}</div>
                <div className="faint" style={{ maxWidth: 460, margin: "0 auto" }}>{t("studio.emptyBody")}</div>
                <Button variant="primary" style={{ marginTop: 18 }} icon={<Plus size={16} />} onClick={(e) => { e.stopPropagation(); intake.pick(); }}>{t("upscale.addFiles")}</Button>
                <div className="label" style={{ marginTop: 14 }}>{t("upscale.dropHint")}</div>
              </div>
            </div>
          ) : (
            <div className="card">
              <div className="row" style={{ padding: "12px 16px", borderBottom: "1px solid var(--border-subtle)" }}>
                <span className="label">{t("upscale.files", { n: files.length })} · {bytes(files.reduce((a, f) => a + (f.size ?? 0), 0))}</span>
                <span className="spacer" />
                <Button size="sm" variant="ghost" icon={<Plus size={13} />} onClick={intake.pick}>{t("upscale.addFiles")}</Button>
                <Button size="sm" variant="ghost" iconOnly icon={<Trash2 size={13} />} tip={t("upscale.clearFiles")} onClick={() => setFiles([])} />
              </div>
              <div className="file-list">
                <AnimatePresence initial={false}>
                  {files.map((f) => (
                    <motion.div key={f.path} layout initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="file-row">
                      <div className="pl-thumb">{f.thumbnail ? <img src={thumbUrl(f.thumbnail)} alt="" /> : f.kind === "video" ? <Video size={14} /> : <AudioLines size={14} />}</div>
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div className="ellipsis" style={{ fontWeight: 600, fontSize: 13 }} title={f.path}>{f.name}</div>
                        <div className="mono faint" style={{ fontSize: 10.5 }}>
                          {[f.kind === "video" ? t("kind.video") : t("kind.audio"), f.duration ? duration(f.duration) : null, f.acodec?.toUpperCase(), bytes(f.size)].filter(Boolean).join(" · ")}
                        </div>
                      </div>
                      <Button size="sm" variant="ghost" iconOnly icon={<Play size={13} />} tip={t("player.play")} onClick={() => openInPlayer([f.path])} />
                      <Button size="sm" variant="ghost" iconOnly icon={<X size={13} />} onClick={() => setFiles((xs) => xs.filter((x) => x.path !== f.path))} />
                    </motion.div>
                  ))}
                </AnimatePresence>
              </div>
            </div>
          )}

          {recent.length > 0 && (
            <div className="card" style={{ padding: "6px 0" }}>
              <div className="label" style={{ padding: "10px 16px" }}>{t("studio.recent")}</div>
              {recent.map((j) => {
                const outputs: string[] = j.result?.meta?.files ?? (j.result?.file_path ? [j.result.file_path] : []);
                return (
                  <div key={j.id} className="file-row" style={{ alignItems: "flex-start" }}>
                    {j.options?.op === "separate" ? <MicVocal size={15} className="faint" style={{ marginTop: 2 }} /> : <WandSparkles size={15} className="faint" style={{ marginTop: 2 }} />}
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div className="row" style={{ gap: 8 }}>
                        <span className="ellipsis" style={{ fontWeight: 600, fontSize: 13, flex: 1 }}>{j.title}</span>
                        <StatusBadge status={j.status} />
                      </div>
                      {["processing", "waiting"].includes(j.status) ? (
                        <div style={{ marginTop: 6 }}>
                          <Progress value={j.progress?.percent} state="active" />
                          {j.progress?.label && <div className="mono faint" style={{ fontSize: 10.5, marginTop: 4 }}>{j.progress.label}</div>}
                        </div>
                      ) : j.status === "completed" ? (
                        <div className="col" style={{ gap: 2, marginTop: 4 }}>
                          {outputs.map((p) => (
                            <div key={p} className="row" style={{ gap: 6 }}>
                              <span className="ellipsis mono faint" style={{ fontSize: 11, flex: 1 }} title={p}>{baseName(p)}</span>
                              <Button size="sm" variant="ghost" iconOnly icon={<Play size={13} />} tip={t("player.play")} onClick={() => openInPlayer([p])} />
                              <Button size="sm" variant="ghost" iconOnly icon={<FolderOpen size={13} />} tip={t("common.showInFolder")} onClick={() => api.post("/api/system/open", { path: p, reveal: true })} />
                            </div>
                          ))}
                        </div>
                      ) : j.error ? (
                        <div className="faint" style={{ fontSize: 12, color: "var(--danger)" }}>{j.error.message}</div>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="side-panel rise" style={{ animationDelay: "120ms" }}>
          <div className="card card-pad col" style={{ gap: 14 }}>
            <span className="label">{t("studio.tool")}</span>
            <div className="target-grid two">
              {(["separate", "clean"] as Op[]).map((o) => (
                <button key={o} className={clsx("target", op === o && "on")} onClick={() => setOp(o)}>
                  <span className="studio-tool-title">
                    {o === "separate" ? <MicVocal size={15} /> : <WandSparkles size={15} />}
                    {t(`studio.${o}`)}
                  </span>
                  <span className="target-note">{t(`studio.${o}Note`)}</span>
                </button>
              ))}
            </div>
            <div className="field-hint">{t(op === "separate" ? "studio.separateDesc" : "studio.cleanDesc")}</div>

            {op === "separate" ? (
              <>
                <Field label={t("studio.keep")}>
                  <div className="cat-seg">
                    <Segmented<Stems>
                      value={stems}
                      onChange={setStems}
                      full
                      size="sm"
                      options={(["both", "vocals", "instrumental"] as Stems[]).map((s) => ({ value: s, label: t(`studio.stems.${s}`) }))}
                    />
                  </div>
                </Field>
                <Field label={t("studio.quality")} hint={t(quality === "max" ? "studio.qualityMaxHint" : "studio.qualityNormalHint")}>
                  <div className="cat-seg">
                    <Segmented value={quality} onChange={setQuality} full size="sm" options={[{ value: "normal", label: t("studio.q.normal") }, { value: "max", label: t("studio.q.max") }]} />
                  </div>
                </Field>
              </>
            ) : (
              <>
                <Field label={t("studio.sound")}>
                  <div className="cat-seg">
                    <Segmented<Sound> value={sound} onChange={setSound} full size="sm" options={[{ value: "voice", label: t("studio.sound.voice") }, { value: "other", label: t("studio.sound.other") }]} />
                  </div>
                </Field>
                <Field label={t("studio.strength")} hint={strengthHint}>
                  <div className="cat-seg">
                    <Segmented<Strength> value={strength} onChange={setStrength} full size="sm" options={(["light", "medium", "strong"] as Strength[]).map((s) => ({ value: s, label: t(`studio.strength.${s}`) }))} />
                  </div>
                </Field>
                <Field label={t("studio.hum")} hint={t("studio.humHint")}>
                  <Select value={hum} onChange={setHum} options={[{ value: "0", label: t("studio.hum.off") }, { value: "50", label: t("studio.hum.50") }, { value: "60", label: t("studio.hum.60") }]} />
                </Field>
                <div className="toggle-row">
                  <div className="toggle-row-text">
                    <div className="toggle-row-title">{t("studio.declick")}</div>
                    <div className="toggle-row-desc">{t("studio.declickDesc")}</div>
                  </div>
                  <Switch checked={declick} onChange={setDeclick} />
                </div>
                <div className="toggle-row">
                  <div className="toggle-row-text">
                    <div className="toggle-row-title">{t("convert.normalize")}</div>
                    <div className="toggle-row-desc">{t("convert.normalizeDesc")}</div>
                  </div>
                  <Switch checked={normalize} onChange={setNormalize} />
                </div>
              </>
            )}
          </div>

          <div className="card card-pad col" style={{ gap: 14 }}>
            <span className="label">{t("studio.output")}</span>
            {hasVideo && (
              <div className="toggle-row">
                <div className="toggle-row-text">
                  <div className="toggle-row-title">{t("studio.keepVideo")}</div>
                  <div className="toggle-row-desc">{t("studio.keepVideoDesc")}</div>
                </div>
                <Switch checked={keepVideo} onChange={setKeepVideo} />
              </div>
            )}
            {(!hasVideo || !keepVideo || files.some((f) => f.kind === "audio")) && (
              <Field label={t("studio.format")}>
                <Select
                  value={format}
                  onChange={setFormat}
                  options={[
                    { value: "auto", label: t("studio.format.auto") },
                    { value: "mp3", label: "MP3 · 320 kbps" },
                    { value: "m4a", label: "AAC (M4A) · 256 kbps" },
                    { value: "flac", label: t("studio.format.flac") },
                    { value: "wav", label: "WAV" },
                    { value: "opus", label: "Opus · 192 kbps" },
                  ]}
                />
              </Field>
            )}
          </div>

          <div className="card card-pad col" style={{ gap: 12 }}>
            {needsModel && model && !model.installed && <Notice>{t("studio.modelDownload", { size: bytes(model.size) })}</Notice>}
            <button className="folder-row" onClick={pickOutput}>
              <FolderOpen size={15} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />
              <span style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="label" style={{ fontSize: 9 }}>{t("download.saveTo")}</span>
                <span className="ellipsis mono" style={{ color: "var(--text-primary)" }}>{outputDir || settings?.tools?.studio_output_folder || t("upscale.nextToSource")}</span>
              </span>
            </button>
            <Button variant="primary" size="xl" full icon={op === "separate" ? <MicVocal size={18} /> : <WandSparkles size={18} />} loading={busy} disabled={!files.length} onClick={start}>
              {t(op === "separate" ? "studio.startSeparate" : "studio.startClean", { n: files.length })}
            </Button>
            <div className="field-hint">{needsModel ? t(info?.gpu ? "studio.onGpu" : "studio.onCpu") : t("studio.fast")}</div>
          </div>
        </div>
      </div>
    </div>
  );
}
