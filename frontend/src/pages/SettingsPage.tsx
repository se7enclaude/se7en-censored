import { motion } from "motion/react";
import {
  ArrowUpCircle,
  Bookmark,
  Captions,
  CheckCircle2,
  Hammer,
  Volume2,
  Cpu,
  Download as DownloadIcon,
  FileCode2,
  FolderOpen,
  Info,
  Package,
  Pencil,
  Plus,
  Settings2,
  Shield,
  SlidersHorizontal,
  Trash2,
  TriangleAlert,
  Type,
  Wrench,
} from "lucide-react";
import { ReactNode, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Preset, Settings, UpdateState } from "../api/types";
import { Button, Field, Modal, Notice, PageHead, Progress, Segmented, Select, Switch } from "../components/ui";
import { useT } from "../i18n";
import { bytes, relative } from "../lib/format";
import { play } from "../lib/sounds";
import { useApp } from "../store/app";
import { Wordmark } from "../components/TopBar";
import { resetSubtitlesCatalog } from "./SubtitlesPage";

const SECTIONS = [
  { id: "general", icon: Settings2 },
  { id: "download", icon: DownloadIcon },
  { id: "naming", icon: Type },
  { id: "presets", icon: Bookmark },
  { id: "ai", icon: Cpu },
  { id: "tools", icon: Hammer },
  { id: "subtitles", icon: Captions },
  { id: "components", icon: Package },
  { id: "updates", icon: ArrowUpCircle },
  { id: "privacy", icon: Shield },
  { id: "advanced", icon: Wrench },
  { id: "about", icon: Info },
] as const;

type Patch = Parameters<ReturnType<typeof useApp.getState>["saveSettings"]>[0];

export function SettingsPage() {
  const t = useT();
  const settings = useApp((s) => s.settings);
  const save = useApp((s) => s.saveSettings);
  const toast = useApp((s) => s.toast);
  const wanted = useApp((s) => s.settingsSection);
  const [section, setSection] = useState<string>(wanted || "general");
  const refs = useRef<Record<string, HTMLDivElement | null>>({});

  useEffect(() => {
    if (wanted) {
      setSection(wanted);
      window.setTimeout(() => refs.current[wanted]?.scrollIntoView({ behavior: "smooth", block: "start" }), 60);
      useApp.setState({ settingsSection: null });
    }
  }, [wanted]);

  if (!settings) return null;
  const upd = async (patch: Patch) => {
    try {
      await save(patch);
    } catch (e: any) {
      toast({ kind: "error", title: e.message, body: e.hint });
    }
  };

  const go = (id: string) => {
    setSection(id);
    refs.current[id]?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const sec = (id: string, children: ReactNode) => (
    <div ref={(el) => { refs.current[id] = el; }} style={{ scrollMarginTop: 10 }}>
      <motion.div className="card settings-section" initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}>
        <div className="eyebrow" style={{ marginBottom: 10 }}>{String(SECTIONS.findIndex((x) => x.id === id) + 1).padStart(2, "0")} — {t(`settings.${id}`)}</div>
        <h3>{t(`settings.${id}`)}</h3>
        <p className="faint" style={{ fontSize: 13, marginBottom: 12 }}>{t(`settings.${id}.desc`)}</p>
        {children}
      </motion.div>
    </div>
  );

  return (
    <div className="page-inner">
      <PageHead eyebrow={t("settings.eyebrow")} title={t("settings.title")} />
      <div className="settings-layout">
        <nav className="settings-nav">
          {SECTIONS.map(({ id }, n) => (
            <button key={id} className={section === id ? "active" : ""} onClick={() => go(id)}>
              <span className="idx">{String(n + 1).padStart(2, "0")}</span> {t(`settings.${id}`)}
            </button>
          ))}
        </nav>
        <div>
          {sec("general", <General s={settings} upd={upd} />)}
          {sec("download", <DownloadSection s={settings} upd={upd} />)}
          {sec("naming", <Naming s={settings} upd={upd} />)}
          {sec("presets", <Presets />)}
          {sec("ai", <AI s={settings} upd={upd} />)}
          {sec("tools", <Tools s={settings} upd={upd} />)}
          {sec("subtitles", <SubtitlesSection s={settings} upd={upd} />)}
          {sec("components", <Components />)}
          {sec("updates", <Updates s={settings} upd={upd} />)}
          {sec("privacy", <Privacy s={settings} upd={upd} />)}
          {sec("advanced", <Advanced s={settings} upd={upd} />)}
          {sec("about", <About />)}
        </div>
      </div>
    </div>
  );
}

function Row({ title, desc, children }: { title: ReactNode; desc?: ReactNode; children: ReactNode }) {
  return (
    <div className="setting">
      <div>
        <div className="setting-title">{title}</div>
        {desc && <div className="setting-desc">{desc}</div>}
      </div>
      <div>{children}</div>
    </div>
  );
}

/** Text input that saves on blur / Enter. */
function LazyInput({ value, onSave, placeholder, mono, type }: { value: string | number; onSave: (v: string) => void; placeholder?: string; mono?: boolean; type?: string }) {
  const [v, setV] = useState(String(value ?? ""));
  useEffect(() => setV(String(value ?? "")), [value]);
  return (
    <input
      className={`input ${mono ? "mono" : ""}`}
      value={v}
      type={type}
      placeholder={placeholder}
      onChange={(e) => setV(e.target.value)}
      onBlur={() => v !== String(value ?? "") && onSave(v)}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
    />
  );
}

function FolderInput({ value, onSave, placeholder }: { value: string; onSave: (v: string) => void; placeholder?: string }) {
  const t = useT();
  return (
    <div className="row">
      <LazyInput value={value} onSave={onSave} placeholder={placeholder} />
      <Button
        iconOnly
        icon={<FolderOpen size={15} />}
        tip={t("common.browse")}
        onClick={async () => {
          const r = await api.post<{ path: string | null }>("/api/system/pick-folder", { initial: value });
          if (r.path) onSave(r.path);
        }}
      />
    </div>
  );
}

type SP = { s: Settings; upd: (p: Patch) => void };

function General({ s, upd }: SP) {
  const t = useT();
  const g = s.general;
  return (
    <>
      <Row title={t("set.language")}>
        <Select value={g.language} onChange={(v) => upd({ general: { language: v } })} options={[{ value: "auto", label: t("set.auto") }, { value: "en", label: "English" }, { value: "fr", label: "Français" }]} />
      </Row>
      <Row title={t("set.theme")}>
        <Segmented value={g.theme} onChange={(v) => upd({ general: { theme: v } })} full options={[{ value: "dark", label: t("set.dark") }, { value: "light", label: t("set.light") }, { value: "system", label: t("set.system") }]} />
      </Row>
      <Row title={t("set.notifications")} desc={t("set.notificationsDesc")}>
        <Switch checked={g.notifications} onChange={(v) => upd({ general: { notifications: v } })} />
      </Row>
      <Row title={t("set.sound")} desc={t("set.soundDesc")}>
        <Switch checked={g.notification_sound} onChange={(v) => upd({ general: { notification_sound: v } })} />
      </Row>
      <Row title={t("set.uiSounds")} desc={t("set.uiSoundsDesc")}>
        <Switch checked={g.ui_sounds !== false} onChange={(v) => upd({ general: { ui_sounds: v } })} />
      </Row>
      <Row title={t("set.volume")}>
        <div className="row">
          <Volume2 size={15} className="faint" />
          <input type="range" min={0} max={100} value={g.sound_volume ?? 60} onChange={(e) => upd({ general: { sound_volume: Number(e.target.value) } })} onMouseUp={() => play("success")} />
          <span className="tabular" style={{ minWidth: 40, textAlign: "right" }}>{g.sound_volume ?? 60}%</span>
          <Button size="sm" variant="ghost" onClick={() => { play("start"); window.setTimeout(() => play("success"), 400); window.setTimeout(() => play("error"), 1000); }}>{t("set.testSounds")}</Button>
        </div>
      </Row>
      <Row title={t("set.startup")} desc={t("set.startupDesc")}>
        <Select
          value={g.startup_page}
          onChange={(v) => upd({ general: { startup_page: v } })}
          options={["download", "upscale", "queue", "convert", "trim", "studio", "subtitles", "player", "history", "last"].map((p) => ({ value: p, label: p === "last" ? t("set.lastPage") : t(`nav.${p}`) }))}
        />
      </Row>
      <Row title={t("set.clipboard")} desc={t("set.clipboardDesc")}>
        <Switch checked={g.clipboard_watch} onChange={(v) => upd({ general: { clipboard_watch: v } })} />
      </Row>
      <Row title={t("set.launch")} desc={t("set.launchDesc")}>
        <div className="row">
          <Button size="sm" variant="ghost" onClick={() => window.dispatchEvent(new Event("se7en:replay-splash"))}>
            {t("set.replayIntro")}
          </Button>
          <Switch checked={g.launch_animation !== false} onChange={(v) => upd({ general: { launch_animation: v } })} />
        </div>
      </Row>
      <Row title={t("set.motion")} desc={t("set.motionDesc")}>
        <Switch checked={g.reduce_motion} onChange={(v) => upd({ general: { reduce_motion: v } })} />
      </Row>
    </>
  );
}

function DownloadSection({ s, upd }: SP) {
  const t = useT();
  const d = s.download;
  return (
    <>
      <Row title={t("set.defaultFolder")}>
        <FolderInput value={d.default_folder} onSave={(v) => upd({ download: { default_folder: v } })} />
      </Row>
      <Row title={t("set.defaultMode")}>
        <Segmented value={d.default_mode} onChange={(v) => upd({ download: { default_mode: v } })} full options={[{ value: "av", label: t("mode.av") }, { value: "video", label: t("mode.video") }, { value: "audio", label: t("mode.audio") }, { value: "image", label: t("mode.image") }]} />
      </Row>
      <Row title={t("set.defaultQuality")}>
        <Select value={d.default_quality} onChange={(v) => upd({ download: { default_quality: v } })} options={["best", "2160", "1440", "1080", "720", "480", "360", "144"].map((q) => ({ value: q, label: q === "best" ? t("quality.best") : `${q}p` }))} />
      </Row>
      <Row title={t("set.defaultFormat")}>
        <div className="row">
          <Select value={d.default_container} onChange={(v) => upd({ download: { default_container: v } })} options={["mp4", "mkv", "webm"].map((c) => ({ value: c, label: c.toUpperCase() }))} />
          <Select value={d.default_video_codec} onChange={(v) => upd({ download: { default_video_codec: v } })} options={["compat", "auto", "h264", "h265", "av1", "vp9"].map((c) => ({ value: c, label: c === "compat" ? t("codec.compat") : c === "auto" ? t("codec.auto") : c.toUpperCase() }))} />
        </div>
      </Row>
      <Row title={t("set.defaultImage")}>
        <Segmented value={d.default_image_format ?? "jpg"} onChange={(v) => upd({ download: { default_image_format: v } })} full options={["jpg", "png", "webp"].map((f) => ({ value: f, label: f.toUpperCase() }))} />
      </Row>
      <Row title={t("set.warnDupes")} desc={t("set.warnDupesDesc")}>
        <Switch checked={d.warn_duplicates !== false} onChange={(v) => upd({ download: { warn_duplicates: v } })} />
      </Row>
      <Row title={t("set.defaultAudio")}>
        <div className="row">
          <Select value={d.default_audio_format} onChange={(v) => upd({ download: { default_audio_format: v } })} options={["mp3", "m4a", "opus", "flac", "wav", "best"].map((c) => ({ value: c, label: c === "best" ? t("audio.original") : c.toUpperCase() }))} />
          <Select value={d.default_audio_bitrate} onChange={(v) => upd({ download: { default_audio_bitrate: v } })} options={[128, 192, 256, 320].map((b) => ({ value: b, label: `${b} kbps` }))} />
        </div>
      </Row>
      <Row title={t("set.concurrent")} desc={t("set.concurrentDesc")}>
        <Segmented value={String(d.concurrent_downloads)} onChange={(v) => upd({ download: { concurrent_downloads: Number(v) } })} full options={["1", "2", "3", "4", "6", "8"].map((n) => ({ value: n, label: n }))} />
      </Row>
      <Row title={t("set.connections")} desc={t("set.connectionsDesc")}>
        <Segmented value={String(d.concurrent_fragments)} onChange={(v) => upd({ download: { concurrent_fragments: Number(v) } })} full options={["1", "4", "8", "16", "32"].map((n) => ({ value: n, label: n }))} />
      </Row>
      <Row title={t("set.rateLimit")} desc={t("set.rateLimitDesc")}>
        <LazyInput type="number" value={d.rate_limit_kbps} onSave={(v) => upd({ download: { rate_limit_kbps: Number(v) || 0 } })} placeholder="0" />
      </Row>
      <Row title={t("set.retries")}>
        <LazyInput type="number" value={d.max_retries} onSave={(v) => upd({ download: { max_retries: Number(v) || 0 } })} />
      </Row>
      <Row title={t("set.embedMeta")} desc={t("set.embedMetaDesc")}>
        <Switch checked={d.embed_metadata} onChange={(v) => upd({ download: { embed_metadata: v } })} />
      </Row>
      <Row title={t("set.embedThumb")}>
        <Switch checked={d.embed_thumbnail} onChange={(v) => upd({ download: { embed_thumbnail: v } })} />
      </Row>
      <Row title={t("set.chapters")}>
        <Switch checked={d.embed_chapters} onChange={(v) => upd({ download: { embed_chapters: v } })} />
      </Row>
      <Row title={t("set.subtitles")} desc={t("set.subtitlesDesc")}>
        <div className="row">
          <Switch checked={d.write_subtitles} onChange={(v) => upd({ download: { write_subtitles: v } })} />
          <LazyInput value={d.subtitle_langs} onSave={(v) => upd({ download: { subtitle_langs: v } })} placeholder="en,fr" />
        </div>
      </Row>
      <Row title={t("set.description")}>
        <Switch checked={d.write_description} onChange={(v) => upd({ download: { write_description: v } })} />
      </Row>
      <Row title={t("set.autoPreset")} desc={t("set.autoPresetDesc")}>
        <Switch checked={d.auto_apply_presets} onChange={(v) => upd({ download: { auto_apply_presets: v } })} />
      </Row>
      <Row title={t("set.autoAnalyze")}>
        <Switch checked={d.auto_analyze_on_paste} onChange={(v) => upd({ download: { auto_analyze_on_paste: v } })} />
      </Row>
      <Row title={t("set.aria2")} desc={t("set.aria2Desc")}>
        <Switch checked={d.use_aria2c} onChange={(v) => upd({ download: { use_aria2c: v } })} />
      </Row>
      <Row title={t("set.playlistLimit")}>
        <LazyInput type="number" value={d.playlist_limit} onSave={(v) => upd({ download: { playlist_limit: Number(v) || 500 } })} />
      </Row>
    </>
  );
}

const TOKENS = ["title", "uploader", "date", "year", "month", "day", "platform", "id", "resolution", "playlist", "index"];
const PLATFORMS = [["youtube", "YouTube"], ["tiktok", "TikTok"], ["twitter", "X"], ["instagram", "Instagram"], ["facebook", "Facebook"], ["reddit", "Reddit"], ["vimeo", "Vimeo"], ["twitch", "Twitch"]];

function Naming({ s, upd }: SP) {
  const t = useT();
  const d = s.download;
  const [tpl, setTpl] = useState(d.naming_template);
  const [folders, setFolders] = useState(d.folder_structure);
  const [preview, setPreview] = useState("");
  useEffect(() => {
    const h = window.setTimeout(async () => {
      const r = await api.get<{ preview: string }>(`/api/naming/preview?template=${encodeURIComponent(tpl)}&folders=${encodeURIComponent(folders)}`);
      setPreview(r.preview);
    }, 150);
    return () => window.clearTimeout(h);
  }, [tpl, folders]);
  const commit = () => upd({ download: { naming_template: tpl, folder_structure: folders } });
  return (
    <>
      <Field label={t("set.folderStructure")} hint={t("set.folderStructureHint")}>
        <input className="input mono" value={folders} onChange={(e) => setFolders(e.target.value)} onBlur={commit} />
      </Field>
      <div className="row" style={{ flexWrap: "wrap", gap: 6, margin: "8px 0 16px" }}>
        {["{platform}", "{platform}/{uploader}", "{platform}/{uploader}/{year}/{month}", ""].map((p) => (
          <button key={p} className="chip token-chip" onClick={() => { setFolders(p); upd({ download: { folder_structure: p } }); }}>{p || t("set.noSubfolder")}</button>
        ))}
      </div>
      <Field label={t("set.fileName")} hint={t("set.fileNameHint")}>
        <input className="input mono" value={tpl} onChange={(e) => setTpl(e.target.value)} onBlur={commit} />
      </Field>
      <div className="row" style={{ flexWrap: "wrap", gap: 6, margin: "8px 0 12px" }}>
        {TOKENS.map((tok) => (
          <button key={tok} className="chip token-chip" onClick={() => setTpl((x) => `${x}${x ? " - " : ""}{${tok}}`)}>{`{${tok}}`}</button>
        ))}
        {["{title}", "{uploader} - {title}", "{date} - {title}", "%(uploader)s - %(title)s"].map((p) => (
          <button key={p} className="chip token-chip" style={{ color: "var(--accent)" }} onClick={() => { setTpl(p); upd({ download: { naming_template: p } }); }}>{p}</button>
        ))}
      </div>
      <div className="res-flow mono" style={{ fontSize: 12 }}>
        <FileCode2 size={14} className="faint" /> <span className="ellipsis selectable">{preview}</span>
      </div>
      <p className="faint" style={{ fontSize: 12, marginTop: 8 }}>{t("set.windowsSafe")}</p>
      <div className="divider" />
      <div className="section-label">{t("set.platformFolders")}</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 10 }}>
        {PLATFORMS.map(([key, def]) => (
          <div key={key} className="row">
            <span style={{ width: 90 }} className="muted">{def}</span>
            <LazyInput value={d.platform_folders[key] ?? ""} placeholder={def} onSave={(v) => upd({ download: { platform_folders: { ...d.platform_folders, [key]: v } } })} />
          </div>
        ))}
      </div>
    </>
  );
}

function Presets() {
  const t = useT();
  const [presets, setPresets] = useState<Preset[]>([]);
  const [edit, setEdit] = useState<Partial<Preset> | null>(null);
  const load = () => api.get<{ presets: Preset[] }>("/api/presets").then((r) => setPresets(r.presets));
  useEffect(() => {
    load();
  }, []);
  const save = async () => {
    if (!edit?.name) return;
    const body = { name: edit.name, platforms: edit.platforms ?? [], options: edit.options ?? {}, auto_apply: edit.auto_apply ?? true };
    if (edit.id) await api.put(`/api/presets/${edit.id}`, body);
    else await api.post("/api/presets", body);
    setEdit(null);
    load();
  };
  const o = edit?.options ?? {};
  const setO = (k: string, v: unknown) => setEdit({ ...edit, options: { ...o, [k]: v } });
  return (
    <>
      {presets.map((p) => (
        <div key={p.id} className="setting">
          <div>
            <div className="setting-title row" style={{ gap: 8 }}>
              {p.name}
              {p.auto_apply && p.platforms.length > 0 && <span className="chip" style={{ height: 20, color: "var(--accent)" }}>{t("preset.auto")}</span>}
            </div>
            <div className="setting-desc">
              {[p.platforms.join(", ") || t("preset.anyPlatform"), p.options.mode && t(`mode.${p.options.mode}`), p.options.quality && (p.options.quality === "best" ? t("quality.best") : `${p.options.quality}p`), p.options.container?.toString().toUpperCase(), p.options.vcodec === "compat" ? t("codec.compatShort") : p.options.vcodec && p.options.vcodec !== "auto" ? String(p.options.vcodec).toUpperCase() : null, p.options.output_dir]
                .filter(Boolean)
                .join(" · ")}
            </div>
          </div>
          <div className="row" style={{ justifyContent: "flex-end" }}>
            <Switch checked={p.auto_apply} onChange={async (v) => { await api.put(`/api/presets/${p.id}`, { ...p, auto_apply: v }); load(); }} />
            <Button size="sm" variant="ghost" iconOnly icon={<Pencil size={14} />} onClick={() => setEdit(p)} />
            <Button size="sm" variant="ghost" iconOnly className="btn-danger" icon={<Trash2 size={14} />} onClick={async () => { await api.del(`/api/presets/${p.id}`); load(); }} />
          </div>
        </div>
      ))}
      <Button icon={<Plus size={15} />} style={{ marginTop: 12 }} onClick={() => setEdit({ name: "", platforms: [], options: { mode: "av", quality: "best", container: "mp4", vcodec: "compat" }, auto_apply: true })}>
        {t("preset.new")}
      </Button>
      <Modal
        open={!!edit}
        onClose={() => setEdit(null)}
        title={edit?.id ? t("preset.edit") : t("preset.new")}
        width={600}
        actions={<><Button onClick={() => setEdit(null)}>{t("common.cancel")}</Button><Button variant="primary" disabled={!edit?.name} onClick={save}>{t("common.save")}</Button></>}
      >
        {edit && (
          <div className="col" style={{ gap: 12, marginTop: 10 }}>
            <Field label={t("preset.name")}><input className="input" value={edit.name ?? ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label={t("preset.platforms")} hint={t("preset.platformsHint")}>
              <input className="input" value={(edit.platforms ?? []).join(", ")} onChange={(e) => setEdit({ ...edit, platforms: e.target.value.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean) })} />
            </Field>
            <div className="options-grid">
              <Field label={t("download.mode")}>
                <Select value={(o.mode as string) ?? "av"} onChange={(v) => setO("mode", v)} options={[{ value: "av", label: t("mode.av") }, { value: "video", label: t("mode.video") }, { value: "audio", label: t("mode.audio") }, { value: "image", label: t("mode.image") }]} />
              </Field>
              <Field label={t("download.quality")}>
                <Select value={(o.quality as string) ?? "best"} onChange={(v) => setO("quality", v)} options={["best", "2160", "1440", "1080", "720", "480", "360", "144"].map((q) => ({ value: q, label: q === "best" ? t("quality.best") : `${q}p` }))} />
              </Field>
              <Field label={t("download.format")}>
                <Select value={(o.container as string) ?? "mp4"} onChange={(v) => setO("container", v)} options={["mp4", "mkv", "webm"].map((c) => ({ value: c, label: c.toUpperCase() }))} />
              </Field>
              <Field label={t("download.codec")}>
                <Select value={(o.vcodec as string) ?? "compat"} onChange={(v) => setO("vcodec", v)} options={["compat", "auto", "h264", "h265", "av1", "vp9"].map((c) => ({ value: c, label: c === "compat" ? t("codec.compat") : c === "auto" ? t("codec.auto") : c.toUpperCase() }))} />
              </Field>
              <Field label={t("download.audioFormat")}>
                <Select value={(o.audio_format as string) ?? "mp3"} onChange={(v) => setO("audio_format", v)} options={["mp3", "m4a", "opus", "flac", "wav", "best"].map((c) => ({ value: c, label: c === "best" ? t("audio.original") : c.toUpperCase() }))} />
              </Field>
              <Field label={t("download.audioBitrate")}>
                <Select value={(o.audio_bitrate as number) ?? 320} onChange={(v) => setO("audio_bitrate", v)} options={[128, 192, 256, 320].map((b) => ({ value: b, label: `${b} kbps` }))} />
              </Field>
            </div>
            <Field label={t("download.saveTo")}>
              <FolderInput value={(o.output_dir as string) ?? ""} onSave={(v) => setO("output_dir", v)} placeholder={t("preset.defaultFolder")} />
            </Field>
            <div className="toggle-row">
              <div className="toggle-row-text"><div className="toggle-row-title">{t("preset.autoApplyGeneric")}</div></div>
              <Switch checked={edit.auto_apply ?? true} onChange={(v) => setEdit({ ...edit, auto_apply: v })} />
            </div>
          </div>
        )}
      </Modal>
    </>
  );
}

function AI({ s, upd }: SP) {
  const t = useT();
  const a = s.ai;
  const caps = useApp((x) => x.capabilities);
  const hw = useApp((x) => x.hardware);
  const models = (caps?.models ?? []).filter((m) => m.usable);
  const devices = caps?.vulkan_devices ?? [];
  const vram = hw?.gpus?.[0]?.vram_total_mb ?? 0;
  return (
    <>
      {hw && (
        <div className="res-flow" style={{ marginBottom: 12, flexWrap: "wrap", gap: 14, fontWeight: 500, fontSize: 12.5 }}>
          <Cpu size={15} className="faint" />
          {hw.gpus.length ? hw.gpus.map((g) => <span key={g.index}><b>{g.name}</b> · {((g.vram_total_mb ?? 0) / 1024).toFixed(0)} GB {g.cuda ? "· CUDA" : ""}</span>) : <span>{t("hw.noGpu")}</span>}
          <span className="faint">{hw.cpu.name} · {hw.cpu.threads} threads · {(hw.ram.total_mb / 1024).toFixed(0)} GB RAM</span>
          <span className="chip">{t("set.accelerator")}: {hw.accelerator.toUpperCase()}</span>
        </div>
      )}
      <Row title={t("set.defaultModel")}>
        <Select value={a.default_model} onChange={(v) => upd({ ai: { default_model: v } })} options={models.map((m) => ({ value: m.id, label: m.name }))} />
      </Row>
      <Row title={t("set.defaultScale")}>
        <Segmented value={String(a.default_scale)} onChange={(v) => upd({ ai: { default_scale: Number(v) } })} full options={["2", "4", "8"].map((x) => ({ value: x, label: `${x}×` }))} />
      </Row>
      <Row title={t("set.gpu")} desc={t("set.gpuDesc")}>
        <Select value={a.gpu_id} onChange={(v) => upd({ ai: { gpu_id: v } })} options={[{ value: "auto", label: t("set.auto") }, ...devices.map((d) => ({ value: String(d.index), label: d.name })), { value: "cpu", label: "CPU" }]} />
      </Row>
      <Row title={t("set.vramLimit")} desc={t("set.vramLimitDesc")}>
        <div className="row">
          <input type="range" min={0} max={Math.max(2048, vram)} step={512} value={a.vram_limit_mb} onChange={(e) => upd({ ai: { vram_limit_mb: Number(e.target.value) } })} />
          <span className="tabular" style={{ minWidth: 70, textAlign: "right" }}>{a.vram_limit_mb ? `${(a.vram_limit_mb / 1024).toFixed(1)} GB` : t("set.auto")}</span>
        </div>
      </Row>
      <Row title={t("set.tile")} desc={t("set.tileDesc")}>
        <Select value={a.tile_size} onChange={(v) => upd({ ai: { tile_size: v } })} options={[0, 64, 128, 200, 256, 400, 512, 768].map((x) => ({ value: x, label: x ? `${x}px` : t("set.autoVram") }))} />
      </Row>
      <Row title={t("set.threads")} desc={t("set.threadsDesc")}>
        <LazyInput mono value={a.threads} onSave={(v) => upd({ ai: { threads: v } })} />
      </Row>
      <Row title={t("set.encoder")}>
        <Select value={a.video_encoder} onChange={(v) => upd({ ai: { video_encoder: v } })} options={["auto", "h264_nvenc", "hevc_nvenc", "av1_nvenc", "h264_qsv", "hevc_qsv", "h264_amf", "hevc_amf", "libx264", "libx265"].map((e) => ({ value: e, label: e === "auto" ? t("set.auto") : e, hint: /_(nvenc|qsv|amf)$/.test(e) ? "GPU" : e === "auto" ? undefined : "CPU", disabled: e !== "auto" && !!caps && !caps.video.encoders.includes(e) }))} />
      </Row>
      <Row title={t("upscale.videoQuality")} desc={t("upscale.videoQualityHint")}>
        <div className="row">
          <input type="range" min={12} max={30} value={a.video_quality} onChange={(e) => upd({ ai: { video_quality: Number(e.target.value) } })} />
          <span className="tabular" style={{ minWidth: 40 }}>{a.video_quality}</span>
        </div>
      </Row>
      <Row title={t("set.segment")} desc={t("set.segmentDesc")}>
        <LazyInput type="number" value={a.segment_seconds} onSave={(v) => upd({ ai: { segment_seconds: Number(v) || 20 } })} />
      </Row>
      <Row title={t("set.gpuJobs")} desc={t("set.gpuJobsDesc")}>
        <Segmented value={String(a.concurrent_jobs)} onChange={(v) => upd({ ai: { concurrent_jobs: Number(v) } })} full options={["1", "2", "3"].map((x) => ({ value: x, label: x }))} />
      </Row>
      <Row title={t("set.aiOutput")}>
        <FolderInput value={a.output_folder} onSave={(v) => upd({ ai: { output_folder: v } })} placeholder={t("upscale.nextToSource")} />
      </Row>
      <Row title={t("set.suffix")}>
        <LazyInput mono value={a.output_suffix} onSave={(v) => upd({ ai: { output_suffix: v } })} />
      </Row>
      <Row title={t("enh.temporal")} desc={t("set.temporalDesc")}>
        <Switch checked={a.temporal_stabilization} onChange={(v) => upd({ ai: { temporal_stabilization: v } })} />
      </Row>
      <div className="divider" />
      <div className="section-label">{t("set.backends")}</div>
      {(caps?.backends ?? []).map((b) => (
        <div key={b.id} className="row" style={{ padding: "6px 0" }}>
          {b.available ? <CheckCircle2 size={15} style={{ color: "var(--success)" }} /> : <TriangleAlert size={15} style={{ color: "var(--warning)" }} />}
          <b>{b.name}</b>
          <span className="faint ellipsis">{b.info}</span>
        </div>
      ))}
      <div className="row" style={{ marginTop: 10 }}>
        <Button size="sm" icon={<FolderOpen size={14} />} onClick={() => api.post("/api/system/open-data-folder?what=models")}>{t("set.openModels")}</Button>
        <span className="faint" style={{ fontSize: 12 }}>{t("set.customModels")}</span>
      </div>
    </>
  );
}

function Components() {
  const t = useT();
  const comps = useApp((s) => s.components);
  const progress = useApp((s) => s.componentProgress);
  const load = useApp((s) => s.loadComponents);
  useEffect(() => {
    load();
  }, [load]);
  return (
    <>
      {comps.map((c) => {
        const p = progress[c.id];
        const busy = p && ["starting", "downloading", "extracting"].includes(p.state);
        return (
          <div key={c.id} className="comp">
            <div className={`comp-icon ${c.installed ? "ok" : c.optional ? "" : "warn"}`}>{c.installed ? <CheckCircle2 size={18} /> : <Package size={18} />}</div>
            <div style={{ minWidth: 0 }}>
              <div className="row" style={{ gap: 8 }}>
                <b>{c.name}</b>
                {c.version && <span className="chip" style={{ height: 20 }}>{c.version}</span>}
                {!c.installed && !c.optional && <span className="chip" style={{ height: 20, color: "var(--warning)" }}>{t("components.required", { what: c.required_for })}</span>}
                {c.optional && <span className="chip" style={{ height: 20 }}>{t("components.optional")}</span>}
              </div>
              <div className="faint" style={{ fontSize: 12 }}>{t.or(`component.${c.id}.desc`, c.description)}</div>
              {c.path && <div className="faint mono ellipsis" style={{ fontSize: 10.5 }}>{c.path}</div>}
              {c.id === "aipack" && c.models && (
                <details className="comp-models">
                  <summary className="label">{t("components.models", { n: c.models.length, size: bytes(c.models.reduce((a, m) => a + m.size, 0)) })}</summary>
                  {c.models.map((m) => (
                    <div key={m.id} className="row mono" style={{ fontSize: 11, gap: 8, padding: "3px 0" }}>
                      {m.installed ? <CheckCircle2 size={12} style={{ color: "var(--success)" }} /> : <Package size={12} className="faint" />}
                      <span className="ellipsis" style={{ flex: 1 }}>{m.author}</span>
                      <span className="faint">{m.license}{m.noncommercial ? ` · ${t("components.nc")}` : ""}</span>
                      <span className="faint">{bytes(m.size)}</span>
                    </div>
                  ))}
                </details>
              )}
              {busy && (
                <div style={{ marginTop: 8 }}>
                  <Progress value={p.total ? ((p.downloaded ?? 0) / p.total) * 100 : null} state="active" />
                  <div className="faint tabular" style={{ fontSize: 11, marginTop: 4 }}>
                    {p.state === "extracting" ? t("components.extracting") : `${bytes(p.downloaded)} / ${bytes(p.total)}`}
                  </div>
                </div>
              )}
              {p?.state === "restart_required" && <div style={{ fontSize: 12, color: "var(--accent)", marginTop: 4 }}>{t("components.restart")}</div>}
            </div>
            <Button size="sm" variant={c.installed ? "default" : "primary"} loading={busy} icon={<DownloadIcon size={14} />} onClick={() => api.post(`/api/components/${c.id}/install`)}>
              {c.installed ? t("components.update") : t("components.install")}
            </Button>
          </div>
        );
      })}
      <p className="faint" style={{ fontSize: 12, marginTop: 10 }}>{t("components.sources")}</p>
    </>
  );
}

function Tools({ s, upd }: SP) {
  const t = useT();
  const o = s.tools;
  return (
    <>
      <Row title={t("set.convertJobs")} desc={t("set.convertJobsDesc")}>
        <Segmented value={String(o.convert_concurrent)} onChange={(v) => upd({ tools: { convert_concurrent: Number(v) } })} full options={["1", "2", "3", "4"].map((n) => ({ value: n, label: n }))} />
      </Row>
      <Row title={t("set.convertOutput")}>
        <FolderInput value={o.convert_output_folder} onSave={(v) => upd({ tools: { convert_output_folder: v } })} placeholder={t("upscale.nextToSource")} />
      </Row>
      <Row title={t("set.gpuEncoder")} desc={t("set.gpuEncoderDesc")}>
        <Switch checked={o.use_gpu_encoder} onChange={(v) => upd({ tools: { use_gpu_encoder: v } })} />
      </Row>
      <Row title={t("set.trimMode")} desc={t("trim.fastHint")}>
        <Segmented value={o.trim_mode} onChange={(v) => upd({ tools: { trim_mode: v } })} full options={[{ value: "fast", label: t("trim.fast") }, { value: "precise", label: t("trim.precise") }]} />
      </Row>
      <Row title={t("set.trimOutput")}>
        <Segmented value={o.trim_output} onChange={(v) => upd({ tools: { trim_output: v } })} full options={[{ value: "merge", label: t("trim.merge") }, { value: "separate", label: t("trim.separate") }]} />
      </Row>
    </>
  );
}

function SubtitlesSection({ s, upd }: SP) {
  const t = useT();
  const toast = useApp((x) => x.toast);
  const o = s.subtitles ?? { engine: "local", claude_model: "claude-opus-5-5", default_preset: "netflix", output_folder: "", max_chars: 42, max_cps: 17, polish: true };
  const [key, setKey] = useState("");
  const [claude, setClaude] = useState<{ configured: boolean; hint?: string | null } | null>(null);
  const [busy, setBusy] = useState<"save" | "test" | null>(null);
  const [presets, setPresets] = useState<string[]>([]);
  useEffect(() => {
    api.get<{ engines: { claude: { configured: boolean; hint?: string | null } }; presets: Record<string, unknown> }>("/api/subtitles/catalog")
      .then((c) => {
        setClaude(c.engines.claude);
        setPresets(Object.keys(c.presets));
      })
      .catch(() => undefined);
  }, []);
  const saveKey = async (value: string) => {
    setBusy("save");
    try {
      const st = await api.put<{ configured: boolean; hint?: string | null }>("/api/subtitles/claude-key", { key: value });
      setClaude(st);
      setKey("");
      resetSubtitlesCatalog();
      toast({ kind: "success", title: value ? t("set.claudeSaved") : t("set.claudeRemoved") });
    } catch (e: any) {
      toast({ kind: "error", title: e.message, body: e.hint });
    } finally {
      setBusy(null);
    }
  };
  const test = async () => {
    setBusy("test");
    try {
      const r = await api.post<{ ok: boolean; model: string }>("/api/subtitles/claude-key/test");
      toast({ kind: r.ok ? "success" : "error", title: r.ok ? t("set.claudeOk", { model: r.model }) : t("set.claudeBad") });
    } catch (e: any) {
      toast({ kind: "error", title: e.message, body: e.hint });
    } finally {
      setBusy(null);
    }
  };
  return (
    <>
      <Row title={t("set.subsEngine")} desc={t("set.subsEngineDesc")}>
        <Segmented value={o.engine} onChange={(v) => upd({ subtitles: { engine: v } } as Patch)} full options={[{ value: "local", label: t("subs.engineLocal") }, { value: "claude", label: "Claude", disabled: !claude?.configured }]} />
      </Row>
      <Row title={t("set.claudeKey")} desc={t("set.claudeKeyDesc")}>
        <div className="col" style={{ gap: 8, minWidth: 0 }}>
          <div className="row" style={{ gap: 8 }}>
            <input className="input input-sm mono" type="password" autoComplete="off" spellCheck={false} placeholder={claude?.configured ? t("set.claudeConfigured", { hint: claude.hint ?? "" }) : "sk-ant-…"} value={key} onChange={(e) => setKey(e.target.value)} style={{ flex: 1, minWidth: 0 }} />
            <Button size="sm" variant="primary" loading={busy === "save"} disabled={!key.trim()} onClick={() => saveKey(key)}>{t("common.save")}</Button>
          </div>
          {claude?.configured && (
            <div className="row" style={{ gap: 8 }}>
              <span className="faint" style={{ fontSize: 12 }}>{t("set.claudeConfigured", { hint: claude.hint ?? "" })}</span>
              <span className="spacer" />
              <Button size="sm" variant="ghost" loading={busy === "test"} onClick={test}>{t("set.claudeTest")}</Button>
              <Button size="sm" variant="ghost" icon={<Trash2 size={13} />} onClick={() => saveKey("")}>{t("set.claudeRemove")}</Button>
            </div>
          )}
        </div>
      </Row>
      <Row title={t("set.claudeModel")} desc={t("set.claudeModelDesc")}>
        <Select value={o.claude_model} onChange={(v) => upd({ subtitles: { claude_model: v } } as Patch)} options={[{ value: "claude-opus-5-5", label: "Claude Opus 5.5" }, { value: "claude-sonnet-5-5", label: "Claude Sonnet 5.5" }]} />
      </Row>
      <Row title={t("set.subsPreset")}>
        <Select value={o.default_preset} onChange={(v) => upd({ subtitles: { default_preset: v } } as Patch)} options={presets.map((p) => ({ value: p, label: t(`subs.preset.${p}`) }))} />
      </Row>
      <Row title={t("set.subsChars")} desc={t("set.subsCharsDesc")}>
        <Segmented value={String(o.max_chars)} onChange={(v) => upd({ subtitles: { max_chars: Number(v) } } as Patch)} full options={["32", "37", "42", "47"].map((n) => ({ value: n, label: n }))} />
      </Row>
      <Row title={t("set.subsCps")} desc={t("set.subsCpsDesc")}>
        <Segmented value={String(o.max_cps)} onChange={(v) => upd({ subtitles: { max_cps: Number(v) } } as Patch)} full options={["13", "15", "17", "20", "25"].map((n) => ({ value: n, label: n }))} />
      </Row>
      <Row title={t("set.subsFolder")}>
        <FolderInput value={o.output_folder} onSave={(v) => upd({ subtitles: { output_folder: v } } as Patch)} placeholder={t("upscale.nextToSource")} />
      </Row>
    </>
  );
}

function Updates({ s, upd }: SP) {
  const t = useT();
  const u = useApp((x) => x.update);
  const [checking, setChecking] = useState(false);
  const check = async () => {
    setChecking(true);
    try {
      const r = await api.post<UpdateState>("/api/update/check");
      useApp.setState({ update: r });
    } finally {
      setChecking(false);
    }
  };
  const stateText = !u
    ? "—"
    : u.state === "unconfigured"
      ? t("update.unconfigured")
      : u.state === "uptodate"
        ? t("update.uptodate")
        : u.state === "available" || u.state === "downloading" || u.state === "ready"
          ? t("update.available", { v: u.latest ?? "" })
          : u.state === "error"
            ? u.error?.message ?? t("update.error")
            : t(`update.state.${u.state}`);
  return (
    <>
      <div className="res-flow" style={{ justifyContent: "space-between", flexWrap: "wrap", gap: 12 }}>
        <span className="row" style={{ gap: 10 }}>
          <ArrowUpCircle size={16} style={{ color: u?.state === "uptodate" ? "var(--success)" : "var(--accent)" }} />
          <span>
            <b>se7en {u?.current}</b> · <span className="muted">{stateText}</span>
          </span>
        </span>
        <span className="faint mono" style={{ fontSize: 11 }}>{u?.checked ? t("update.lastCheck", { when: relative(u.checked, t.lang) }) : ""}</span>
        <Button size="sm" loading={checking || u?.state === "checking"} disabled={u?.state === "unconfigured"} onClick={check}>{t("update.check")}</Button>
      </div>
      <Row title={t("set.autoUpdate")} desc={t("set.autoUpdateDesc")}>
        <Switch checked={s.general.auto_update !== false} onChange={(v) => upd({ general: { auto_update: v } })} />
      </Row>
      <p className="faint" style={{ fontSize: 12, marginTop: 8 }}>{u?.portable ? t("update.portableNote") : t("update.installedNote")}</p>
    </>
  );
}

function Privacy({ s, upd }: SP) {
  const t = useT();
  const p = s.privacy;
  const toast = useApp((x) => x.toast);
  const [cookieInfo, setCookieInfo] = useState<{ stored: boolean } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    api.get<{ stored: boolean }>("/api/cookies").then(setCookieInfo);
  }, []);
  const importCookies = async (f?: File) => {
    if (!f) return;
    const form = new FormData();
    form.append("file", f);
    try {
      const r = await api.upload<{ count: number; domains: string[] }>("/api/cookies", form);
      toast({ kind: "success", title: t("privacy.cookiesImported", { n: r.count }), body: r.domains.slice(0, 6).join(", ") });
      setCookieInfo({ stored: true });
      upd({ privacy: { cookies_mode: "file" } });
    } catch (e: any) {
      toast({ kind: "error", title: e.message, body: e.hint });
    }
  };
  return (
    <>
      <Notice>{t("privacy.notice")}</Notice>
      <Row title={t("privacy.cookies")} desc={t("privacy.cookiesDesc")}>
        <Segmented value={p.cookies_mode} onChange={(v) => upd({ privacy: { cookies_mode: v } })} full options={[{ value: "none", label: t("privacy.none") }, { value: "browser", label: t("privacy.browser") }, { value: "file", label: t("privacy.file") }]} />
      </Row>
      {p.cookies_mode === "browser" && (
        <Row title={t("privacy.whichBrowser")} desc={t("privacy.browserDesc")}>
          <Select value={p.cookies_browser} onChange={(v) => upd({ privacy: { cookies_browser: v } })} options={["firefox", "chrome", "edge", "brave", "opera", "vivaldi", "chromium"].map((b) => ({ value: b, label: b[0].toUpperCase() + b.slice(1) }))} />
        </Row>
      )}
      {p.cookies_mode === "file" && (
        <Row title={t("privacy.cookieFile")} desc={cookieInfo?.stored ? t("privacy.cookieStored") : t("privacy.cookieNone")}>
          <div className="row">
            <input ref={fileRef} type="file" accept=".txt" hidden onChange={(e) => importCookies(e.target.files?.[0])} />
            <Button size="sm" icon={<Plus size={14} />} onClick={() => fileRef.current?.click()}>{t("privacy.import")}</Button>
            {cookieInfo?.stored && <Button size="sm" variant="ghost" className="btn-danger" icon={<Trash2 size={14} />} onClick={async () => { await api.del("/api/cookies"); setCookieInfo({ stored: false }); }}>{t("common.delete")}</Button>}
          </div>
        </Row>
      )}
      <Row title={t("privacy.keepHistory")}>
        <Switch checked={p.keep_history} onChange={(v) => upd({ privacy: { keep_history: v } })} />
      </Row>
      <Row title={t("privacy.retention")} desc={t("privacy.retentionDesc")}>
        <Select value={p.history_retention_days} onChange={(v) => upd({ privacy: { history_retention_days: v } })} options={[0, 7, 30, 90, 365].map((d) => ({ value: d, label: d ? t("privacy.days", { n: d }) : t("privacy.forever") }))} />
      </Row>
      <Row title={t("privacy.clearTemp")} desc={t("privacy.clearTempDesc")}>
        <Switch checked={p.clear_temp_on_exit} onChange={(v) => upd({ privacy: { clear_temp_on_exit: v } })} />
      </Row>
      <Row title={t("privacy.cache")} desc={t("privacy.cacheDesc")}>
        <Button size="sm" onClick={async () => { const r = await api.post<{ freed: number }>("/api/system/clear-cache"); toast({ kind: "success", title: t("privacy.cacheCleared", { size: bytes(r.freed) }) }); }}>
          {t("privacy.clearCache")}
        </Button>
      </Row>
    </>
  );
}

function Advanced({ s, upd }: SP) {
  const t = useT();
  const a = s.advanced;
  const info = useApp((x) => x.info);
  return (
    <>
      <Row title={t("adv.debug")} desc={t("adv.debugDesc")}>
        <Switch checked={a.debug_mode} onChange={(v) => upd({ advanced: { debug_mode: v } })} />
      </Row>
      <Row title={t("adv.ffmpeg")} desc={t("adv.pathDesc")}>
        <LazyInput mono value={a.ffmpeg_path} onSave={(v) => upd({ advanced: { ffmpeg_path: v } })} placeholder={t("set.auto")} />
      </Row>
      <Row title={t("adv.realesrgan")} desc={t("adv.pathDesc")}>
        <LazyInput mono value={a.realesrgan_path} onSave={(v) => upd({ advanced: { realesrgan_path: v } })} placeholder={t("set.auto")} />
      </Row>
      <Row title={t("adv.folders")}>
        <div className="row" style={{ flexWrap: "wrap" }}>
          <Button size="sm" icon={<FolderOpen size={14} />} onClick={() => api.post("/api/system/open-data-folder?what=data")}>{t("adv.data")}</Button>
          <Button size="sm" icon={<FolderOpen size={14} />} onClick={() => api.post("/api/system/open-data-folder?what=logs")}>{t("nav.logs")}</Button>
          <Button size="sm" icon={<FolderOpen size={14} />} onClick={() => api.post("/api/system/open-data-folder?what=plugins")}>Plugins</Button>
        </div>
      </Row>
      <Row title={t("adv.plugins")} desc={t("adv.pluginsDesc")}>
        <div className="col" style={{ gap: 4 }}>
          {(info?.plugins ?? []).length === 0 && <span className="faint">{t("common.none")}</span>}
          {(info?.plugins ?? []).map((p) => (
            <span key={p.name} className="row" style={{ gap: 6 }}>
              {p.ok ? <CheckCircle2 size={13} style={{ color: "var(--success)" }} /> : <TriangleAlert size={13} style={{ color: "var(--danger)" }} />}
              {p.name} {p.error && <span className="faint">{p.error}</span>}
            </span>
          ))}
        </div>
      </Row>
      <Row title={t("adv.reset")} desc={t("adv.resetDesc")}>
        <Button size="sm" variant="ghost" className="btn-danger" icon={<SlidersHorizontal size={14} />} onClick={async () => { const r = await api.post<Settings>("/api/settings/reset"); useApp.setState({ settings: r }); }}>
          {t("adv.resetBtn")}
        </Button>
      </Row>
    </>
  );
}

function About() {
  const t = useT();
  const info = useApp((x) => x.info);
  return (
    <>
      <div className="row" style={{ gap: 14, marginBottom: 12 }}>
        <div>
          <Wordmark size={44} />
          <div className="label" style={{ marginTop: 10 }}>Download everything — Upscale anything · v{info?.version}</div>
          <div className="faint">{info?.portable ? "Portable Edition" : t("about.installed")} · {info?.data_dir}</div>
        </div>
      </div>
      <Notice kind="warning" title={t("about.legalTitle")}>{t("about.legal")}</Notice>
      <p className="faint" style={{ fontSize: 12, marginTop: 12 }}>{t("about.credits")}</p>
    </>
  );
}
