import { motion } from "motion/react";
import {
  ArrowUpCircle,
  CheckCircle2,
  Volume2,
  Cpu,
  Download as DownloadIcon,
  FolderOpen,
  Info,
  Package,
  Settings2,
  SlidersHorizontal,
  TriangleAlert,
  Wrench,
} from "lucide-react";
import { ReactNode, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { Settings, UpdateState } from "../api/types";
import { Button, Notice, PageHead, Progress, Segmented, Select, Switch } from "../components/ui";
import { useT } from "../i18n";
import { bytes, relative } from "../lib/format";
import { play } from "../lib/sounds";
import { useApp } from "../store/app";
import { Wordmark } from "../components/TopBar";

// se7en Censored ne garde que ses propres réglages (les sections de se7en Download restent dans le code, cachées)
const SECTIONS = [
  { id: "general", icon: Settings2 },
  { id: "censor", icon: Cpu },
  { id: "components", icon: Package },
  { id: "updates", icon: ArrowUpCircle },
  { id: "advanced", icon: Wrench },
  { id: "about", icon: Info },
];
const SHOWN_COMPONENTS = ["ffmpeg", "aipack", "deno", "ytdlp"];

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
          {sec("censor", <CensorSettings s={settings} upd={upd} />)}
          {sec("components", <Components />)}
          {sec("updates", <Updates s={settings} upd={upd} />)}
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
      {comps.filter((c) => SHOWN_COMPONENTS.includes(c.id)).map((c) => {
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

function CensorSettings({ s, upd }: SP) {
  return (
    <>
      <Row title="Analyse sur la carte graphique" desc="Le détecteur tourne sur la carte graphique (NVIDIA, AMD ou Intel). Sans elle, l'analyse est environ 50 fois plus lente.">
        <Switch checked={s.ai.gpu_id !== "cpu"} onChange={(v) => upd({ ai: { gpu_id: v ? "auto" : "cpu" } })} />
      </Row>
      <Row title="Export avec la carte graphique" desc="NVIDIA NVENC, Intel Quick Sync ou AMD AMF : 5 à 20 fois plus rapide que le processeur.">
        <Switch checked={s.tools.use_gpu_encoder} onChange={(v) => upd({ tools: { use_gpu_encoder: v } })} />
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
          <div className="label" style={{ marginTop: 10 }}>Floutage automatique · v{info?.version}</div>
          <div className="faint">{info?.portable ? "Portable Edition" : t("about.installed")} · {info?.data_dir}</div>
        </div>
      </div>
      <Notice kind="warning" title={t("about.legalTitle")}>{t("about.legal")}</Notice>
      <p className="faint" style={{ fontSize: 12, marginTop: 12 }}>{t("about.credits")}</p>
    </>
  );
}
