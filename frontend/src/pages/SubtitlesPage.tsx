import clsx from "clsx";
import {
  ArrowLeft, Captions, Check, ChevronLeft, ChevronRight, Download, FileText, FolderOpen, Languages, Pause, Play,
  Plus, SkipBack, SkipForward, Sparkles, Trash2, Upload, Volume2, VolumeX, Wand2, ZoomIn, ZoomOut,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, thumbUrl } from "../api/client";
import type { Job, SubCatalog, SubCue, SubLanguage, SubProject, SubProjectSummary, SubStyle, TaskState } from "../api/types";
import { CueEditor } from "../components/subtitles/CueEditor";
import { SubtitleOverlay } from "../components/subtitles/Overlay";
import { StylePanel } from "../components/subtitles/StylePanel";
import { SubtitleTimeline } from "../components/subtitles/Timeline";
import { Badge, Button, Field, Notice, PageHead, Progress, Segmented, Select, Switch, Ticks } from "../components/ui";
import { useFileIntake } from "../hooks/useFileIntake";
import { usePlayback } from "../hooks/usePlayback";
import { useT } from "../i18n";
import { bytes, duration as fmtDuration } from "../lib/format";
import { fmtTime, mergeCues, newId, splitCue } from "../lib/subtitles";
import { useApp } from "../store/app";

const PROJECT_KEY = "se7en.subs.project";
let catalogCache: SubCatalog | null = null;

export function resetSubtitlesCatalog(): void {
  catalogCache = null;
}

async function loadCatalog(force = false): Promise<SubCatalog> {
  if (!catalogCache || force) catalogCache = await api.get<SubCatalog>("/api/subtitles/catalog");
  return catalogCache;
}

export function SubtitlesPage() {
  const [pid, setPid] = useState<string | null>(() => {
    try {
      return sessionStorage.getItem(PROJECT_KEY);
    } catch {
      return null;
    }
  });
  const open = (id: string | null) => {
    setPid(id);
    try {
      if (id) sessionStorage.setItem(PROJECT_KEY, id);
      else sessionStorage.removeItem(PROJECT_KEY);
    } catch {
      /* ignore */
    }
  };
  return pid ? <Workspace key={pid} pid={pid} onClose={() => open(null)} /> : <Home onOpen={open} />;
}

// ================================================================== home: import + recent projects
function Home({ onOpen }: { onOpen: (id: string) => void }) {
  const t = useT();
  const toast = useApp((s) => s.toast);
  const [recent, setRecent] = useState<SubProjectSummary[]>([]);
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(() => {
    api.get<SubProjectSummary[]>("/api/subtitles/projects").then(setRecent).catch(() => undefined);
  }, []);
  useEffect(refresh, [refresh]);

  const create = async (path: string) => {
    setBusy(true);
    try {
      const p = await api.post<SubProject>("/api/subtitles/projects", { path });
      onOpen(p.id);
    } catch (e: any) {
      toast({ kind: "error", title: e.message, body: e.hint });
    } finally {
      setBusy(false);
    }
  };
  const intake = useFileIntake({
    page: "subtitles",
    kind: "av",
    multiple: false,
    onFiles: (fs) => {
      const f = fs.find((x) => x.kind === "video" || x.kind === "audio");
      if (f) create(f.path);
      else if (fs.length) toast({ kind: "warning", title: t("subs.notVideo") });
    },
  });

  return (
    <div className="page-inner wide">
      <PageHead eyebrow={t("subs.eyebrow")} title={t("subs.title")} echo={t("subs.echo")}
        right={<Button variant="primary" icon={<Upload size={15} />} loading={busy} onClick={intake.pick}>{t("subs.import")}</Button>} />
      <input {...intake.inputProps} />
      <div className="drop-zone subs-drop rise" onClick={intake.pick}>
        <Ticks inset={14} />
        <div>
          <div className="empty-icon" style={{ borderColor: "var(--accent)", color: "var(--accent)" }}><Captions size={24} /></div>
          <div className="empty-title">{t("subs.emptyTitle")}</div>
          <div className="faint" style={{ maxWidth: 520, margin: "0 auto" }}>{t("subs.emptyBody")}</div>
          <Button variant="primary" style={{ marginTop: 18 }} icon={<Upload size={16} />} loading={busy} onClick={(e) => { e.stopPropagation(); intake.pick(); }}>{t("subs.import")}</Button>
          <div className="label" style={{ marginTop: 14 }}>{t("subs.dropHint")}</div>
          <div className="subs-flow">
            {["import", "language", "generate", "review", "style", "export"].map((s, i) => (
              <span key={s}><b>{i + 1}</b> {t(`subs.step.${s}`)}</span>
            ))}
          </div>
        </div>
      </div>
      {recent.length > 0 && (
        <div className="card rise" style={{ marginTop: 18, padding: "6px 0", animationDelay: "80ms" }}>
          <div className="label" style={{ padding: "10px 16px" }}>{t("subs.recent")}</div>
          {recent.map((p) => (
            <div key={p.id} className="file-row" style={{ cursor: "pointer" }} onClick={() => onOpen(p.id)}>
              <Captions size={16} className="faint" />
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="ellipsis" style={{ fontWeight: 600, fontSize: 13 }}>{p.name}</div>
                <div className="mono faint" style={{ fontSize: 10.5 }}>
                  {[p.duration ? fmtDuration(p.duration) : null, p.cues ? t("subs.nCues", { n: p.cues }) : t("subs.notGenerated"),
                    p.source_lang ? `${p.source_lang}${p.target_lang && p.target_lang !== p.source_lang ? ` → ${p.target_lang}` : ""}` : null,
                    new Date(p.updated * 1000).toLocaleString()].filter(Boolean).join(" · ")}
                </div>
              </div>
              {!p.exists && <Badge tone="warning">{t("subs.videoMissing")}</Badge>}
              <Button size="sm" variant="ghost" iconOnly icon={<Trash2 size={13} />} tip={t("subs.deleteProject")}
                onClick={async (e) => { e.stopPropagation(); await api.del(`/api/subtitles/projects/${p.id}`); refresh(); }} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ================================================================== workspace
type Tab = "generate" | "edit" | "style" | "export";

function Workspace({ pid, onClose }: { pid: string; onClose: () => void }) {
  const t = useT();
  const toast = useApp((s) => s.toast);
  const jobs = useApp((s) => s.jobs);
  const settings = useApp((s) => s.settings);
  const lang = useApp((s) => s.settings?.general.language);
  const french = lang === "fr" || (lang !== "en" && navigator.language.startsWith("fr"));
  const [catalog, setCatalog] = useState<SubCatalog | null>(catalogCache);
  const [project, setProject] = useState<SubProject | null>(null);
  const [cues, setCues] = useState<SubCue[]>([]);
  const [style, setStyle] = useState<SubStyle | null>(null);
  const [past, setPast] = useState<SubCue[][]>([]);
  const [future, setFuture] = useState<SubCue[][]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [tab, setTab] = useState<Tab>("generate");
  const [pps, setPps] = useState(80);
  const [peaks, setPeaks] = useState<number[]>([]);
  const [saved, setSaved] = useState<"saved" | "saving" | "dirty">("saved");
  const [detect, setDetect] = useState<TaskState | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [burnId, setBurnId] = useState<string | null>(null);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const lastCommitted = useRef<SubCue[]>([]);
  const dirty = useRef(false);

  // ---------------------------------------------------------------- loading
  const reload = useCallback(async () => {
    const p = await api.get<SubProject>(`/api/subtitles/projects/${pid}`);
    setProject(p);
    setCues(p.cues);
    lastCommitted.current = p.cues;
    setStyle(p.style);
    setPast([]);
    setFuture([]);
    return p;
  }, [pid]);

  useEffect(() => {
    loadCatalog().then(setCatalog).catch(() => undefined);
    reload()
      .then((p) => {
        setTab(p.cues.length ? "edit" : "generate");
        if (!p.detected && p.status === "new") startDetect();
        api.get<{ peaks: number[] }>(`/api/media/waveform?path=${encodeURIComponent(p.video.path)}&buckets=4000`).then((w) => setPeaks(w.peaks)).catch(() => undefined);
      })
      .catch((e) => {
        toast({ kind: "error", title: e.message });
        onClose();
      });
  }, [pid]); // eslint-disable-line react-hooks/exhaustive-deps

  // a generation / burn already running for this project (page reopened)
  useEffect(() => {
    for (const j of Object.values(jobs)) {
      if (j.kind !== "subtitles" || j.options?.project !== pid || !["waiting", "processing"].includes(j.status)) continue;
      if (j.options?.op === "burn") setBurnId((x) => x ?? j.id);
      else setJobId((x) => x ?? j.id);
    }
  }, [jobs, pid]);

  const job: Job | undefined = jobId ? jobs[jobId] : undefined;
  const burnJob: Job | undefined = burnId ? jobs[burnId] : undefined;
  useEffect(() => {
    if (job?.status === "completed") {
      reload().then(() => setTab("edit"));
      setJobId(null);
      toast({ kind: "success", title: t("subs.generated"), body: job.result?.format });
    } else if (job?.status === "failed") {
      toast({ kind: "error", title: job.error?.message ?? t("subs.failed"), body: job.error?.hint });
      setJobId(null);
    } else if (job?.status === "canceled") setJobId(null);
  }, [job?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const startDetect = async () => {
    try {
      let task = await api.post<TaskState>(`/api/subtitles/projects/${pid}/detect`);
      setDetect(task);
      while (task.state === "running") {
        await new Promise((r) => setTimeout(r, 600));
        task = await api.get<TaskState>(`/api/tasks/${task.id}`);
        setDetect(task);
      }
      if (task.state === "done") await reload();
      else if (task.error) toast({ kind: "error", title: task.error.message, body: task.error.hint });
    } catch (e: any) {
      toast({ kind: "error", title: e.message, body: e.hint });
    }
  };

  // ---------------------------------------------------------------- saving (debounced)
  useEffect(() => {
    if (!project || !dirty.current) return;
    setSaved("dirty");
    const h = window.setTimeout(async () => {
      setSaved("saving");
      try {
        await api.put(`/api/subtitles/projects/${pid}`, { cues, style });
        dirty.current = false;
        setSaved("saved");
      } catch {
        setSaved("dirty");
      }
    }, 700);
    return () => window.clearTimeout(h);
  }, [cues, style]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------------------------------------------------------- edits with undo / redo
  // (the previous state is read *before* the ref changes: state updaters run later, lazily)
  const commit = useCallback((next: SubCue[]) => {
    const sorted = [...next].sort((a, b) => a.start - b.start || a.end - b.end);
    const before = lastCommitted.current;
    setPast((p) => [...p.slice(-199), before]);
    setFuture([]);
    lastCommitted.current = sorted;
    dirty.current = true;
    setCues(sorted);
  }, []);
  const cuesRef = useRef(cues);
  cuesRef.current = cues;
  /** Live edit (typing, dragging); `done` records it in the undo history. */
  const editLive = (id: string, patch: Partial<SubCue>, done: boolean) => {
    let next = cuesRef.current;
    if (Object.keys(patch).length) {
      next = next.map((c) => (c.id === id ? { ...c, ...patch, end: Math.max((patch.start ?? c.start) + 0.05, patch.end ?? c.end) } : c));
      cuesRef.current = next;
      dirty.current = true;
      setCues(next);
    }
    if (done && next !== lastCommitted.current) commit(next);
  };
  const undo = () => {
    if (!past.length) return;
    const now = lastCommitted.current;
    setFuture((f) => [now, ...f]);
    const prev = past[past.length - 1];
    setPast((p) => p.slice(0, -1));
    lastCommitted.current = prev;
    dirty.current = true;
    setCues(prev);
  };
  const redo = () => {
    if (!future.length) return;
    const now = lastCommitted.current;
    setPast((p) => [...p, now]);
    const next = future[0];
    setFuture((f) => f.slice(1));
    lastCommitted.current = next;
    dirty.current = true;
    setCues(next);
  };

  // ---------------------------------------------------------------- playback
  const video = project?.video;
  const pb = usePlayback(video?.path ?? null, "subtitles");
  const getTime = useCallback(() => pb.offset + (pb.mediaRef.current?.currentTime ?? 0), [pb.offset, pb.mediaRef]);
  const fps = video?.fps || 25;
  const dur = video?.duration || pb.duration || 0;
  const current = useMemo(() => cues.find((c) => c.start <= pb.time && pb.time < c.end)?.id ?? null, [cues, pb.time]);
  useEffect(() => {
    const el = pb.mediaRef.current;
    if (el) {
      el.volume = volume;
      el.muted = muted;
    }
  }, [volume, muted, pb.src, pb.mediaRef]);

  const stage = useRef<HTMLDivElement>(null);
  const [rect, setRect] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  useEffect(() => {
    const el = stage.current;
    if (!el || !video) return;
    const update = () => {
      const W = el.clientWidth;
      const H = el.clientHeight;
      const vw = video.width || 16;
      const vh = video.height || 9;
      const k = Math.min(W / vw, H / vh);
      setRect({ x: (W - vw * k) / 2, y: (H - vh * k) / 2, w: vw * k, h: vh * k });
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [video, tab]);

  // ---------------------------------------------------------------- selection + cue operations
  const select = (id: string, mode: "one" | "toggle" | "range") => {
    setSelected((s) => {
      if (mode === "toggle") {
        const n = new Set(s);
        if (n.has(id)) n.delete(id);
        else n.add(id);
        return n;
      }
      if (mode === "range" && s.size) {
        const ids = cues.map((c) => c.id);
        const a = ids.indexOf([...s][s.size - 1]);
        const b = ids.indexOf(id);
        return new Set(ids.slice(Math.min(a, b), Math.max(a, b) + 1));
      }
      return new Set([id]);
    });
  };
  const addCue = () => {
    const t0 = pb.time;
    const next = cues.find((c) => c.start > t0);
    const end = Math.min(t0 + 2, next ? next.start - 0.083 : dur || t0 + 2);
    if (cues.some((c) => c.start <= t0 && t0 < c.end) || end - t0 < 0.3) {
      toast({ kind: "warning", title: t("subs.noRoom") });
      return;
    }
    const c: SubCue = { id: newId(), start: +t0.toFixed(3), end: +end.toFixed(3), text: "…", words: [] };
    commit([...cues, c]);
    setSelected(new Set([c.id]));
    setTab("edit");
  };
  const deleteSel = () => {
    if (!selected.size) return;
    commit(cues.filter((c) => !selected.has(c.id)));
    setSelected(new Set());
  };
  const mergeSel = () => {
    const idx = cues.map((c, i) => (selected.has(c.id) ? i : -1)).filter((i) => i >= 0);
    if (!idx.length) return;
    const from = idx[0];
    const to = idx.length > 1 ? idx[idx.length - 1] : Math.min(cues.length - 1, from + 1);
    if (to <= from) return;
    let m = cues[from];
    for (let i = from + 1; i <= to; i++) m = mergeCues(m, cues[i]);
    commit([...cues.slice(0, from), m, ...cues.slice(to + 1)]);
    setSelected(new Set([m.id]));
  };
  const split = (id: string, caret?: number) => {
    const i = cues.findIndex((c) => c.id === id);
    if (i < 0) return;
    const c = cues[i];
    const inside = pb.time > c.start + 0.2 && pb.time < c.end - 0.2 ? pb.time : undefined;
    const [a, b] = splitCue(c, caret, caret === undefined ? inside : undefined);
    if (!a.text || !b.text) return;
    commit([...cues.slice(0, i), a, b, ...cues.slice(i + 1)]);
    setSelected(new Set([b.id]));
  };
  const shift = (s: number) => {
    const moved = cues.map((c) => (selected.has(c.id) ? { ...c, start: +Math.max(0, c.start + s).toFixed(3), end: +Math.max(0.2, c.end + s).toFixed(3) } : c));
    commit(moved);
  };
  const replace = (find: string, rep: string, all: boolean, matchCase: boolean) => {
    const esc = find.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const re = new RegExp(esc, matchCase ? "g" : "gi");
    let n = 0;
    let done = false;
    const next = cues.map((c) => {
      if (done || !re.test(c.text)) return c;
      re.lastIndex = 0;
      const text = all ? c.text.replace(re, () => (n++, rep)) : c.text.replace(new RegExp(esc, matchCase ? "" : "i"), () => (n++, rep));
      if (!all) done = true;
      return { ...c, text };
    });
    if (n) {
      commit(next);
      toast({ kind: "success", title: t("subs.replaced", { n }) });
    }
    return n;
  };

  // ---------------------------------------------------------------- keyboard
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      const typing = el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        dirty.current = true;
        setCues((c) => [...c]);
        return;
      }
      if (typing) return;
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "y") {
        e.preventDefault();
        redo();
      } else if (e.key === " ") {
        e.preventDefault();
        pb.toggle();
      } else if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        const dir = e.key === "ArrowLeft" ? -1 : 1;
        if (e.altKey) {
          const target = dir < 0 ? [...cues].reverse().find((c) => c.start < pb.time - 0.05) : cues.find((c) => c.start > pb.time + 0.05);
          if (target) pb.seek(target.start + 0.001);
        } else pb.seek(pb.time + dir * (e.shiftKey ? 1 : 1 / fps));
      } else if (e.key === "Delete") {
        deleteSel();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!project || !style || !catalog) {
    return (
      <div className="page-inner wide">
        <div className="card card-pad" style={{ marginTop: 40 }}><Progress value={null} state="indeterminate" /></div>
      </div>
    );
  }

  const srcLang = project.source_lang || project.detected?.code || "";
  const dstLang = project.target_lang || srcLang;
  const langOf = (c: string) => catalog.languages.find((l) => l.code === c);
  const maxCps = catalog.settings.max_cps;
  const displayLang = project.target_lang || srcLang;
  const step = !cues.length ? (project.detected ? 3 : 2) : tab === "style" ? 5 : tab === "export" ? 6 : 4;

  return (
    <div className="subs-work">
      <div className="subs-head">
        <Button size="sm" variant="ghost" icon={<ArrowLeft size={14} />} onClick={onClose}>{t("subs.projects")}</Button>
        <input className="subs-name" value={project.name} onChange={(e) => setProject({ ...project, name: e.target.value })}
          onBlur={() => api.put(`/api/subtitles/projects/${pid}`, { name: project.name }).catch(() => undefined)} />
        <div className="subs-steps">
          {(["import", "language", "generate", "review", "style", "export"] as const).map((s, i) => (
            <button key={s} className={clsx("subs-step", i + 1 < step && "done", i + 1 === step && "on")}
              onClick={() => setTab(s === "style" ? "style" : s === "export" ? "export" : s === "review" && cues.length ? "edit" : "generate")}>
              <b>{i + 1 < step ? <Check size={10} /> : i + 1}</b> {t(`subs.step.${s}`)}
            </button>
          ))}
        </div>
        <span className="spacer" />
        <span className={clsx("subs-saved", saved)}>{saved === "saved" ? t("subs.saved") : saved === "saving" ? t("subs.saving") : t("subs.unsaved")}</span>
        <Button size="sm" variant="primary" icon={<Download size={14} />} disabled={!cues.length} onClick={() => setTab("export")}>{t("subs.export")}</Button>
      </div>

      <div className="subs-main">
        <div className="subs-preview">
          <div className="subs-stage" ref={stage} onClick={() => pb.toggle()}>
            {video?.has_video === false ? (
              <div className="subs-audio"><Volume2 size={40} /></div>
            ) : null}
            <video ref={pb.mediaRef} src={pb.src} preload="auto" playsInline style={{ display: video?.has_video === false ? "none" : undefined }} />
            <SubtitleOverlay cues={cues} style={style} lang={displayLang} time={pb.time} playing={pb.playing} getTime={getTime} rect={rect} />
            {pb.waiting && <div className="subs-wait"><Progress value={null} state="indeterminate" /></div>}
          </div>
          <div className="subs-controls">
            <Button size="sm" variant="ghost" iconOnly icon={<SkipBack size={14} />} tip={t("subs.prevCue")}
              onClick={() => { const c = [...cues].reverse().find((x) => x.start < pb.time - 0.05); pb.seek(c ? c.start + 0.001 : 0); }} />
            <Button size="sm" variant="ghost" iconOnly icon={<ChevronLeft size={14} />} tip={t("subs.prevFrame")} onClick={() => pb.seek(pb.time - 1 / fps)} />
            <button className="subs-play" onClick={pb.toggle}>{pb.playing ? <Pause size={16} /> : <Play size={16} />}</button>
            <Button size="sm" variant="ghost" iconOnly icon={<ChevronRight size={14} />} tip={t("subs.nextFrame")} onClick={() => pb.seek(pb.time + 1 / fps)} />
            <Button size="sm" variant="ghost" iconOnly icon={<SkipForward size={14} />} tip={t("subs.nextCue")}
              onClick={() => { const c = cues.find((x) => x.start > pb.time + 0.05); if (c) pb.seek(c.start + 0.001); }} />
            <span className="mono subs-clock">{fmtTime(pb.time)} <span className="faint">/ {fmtTime(dur)}</span></span>
            <Scrubber time={pb.time} duration={dur} buffered={pb.buffered} cues={cues} onSeek={pb.seek} />
            <Button size="sm" variant="ghost" iconOnly icon={muted || volume === 0 ? <VolumeX size={14} /> : <Volume2 size={14} />} onClick={() => setMuted((m) => !m)} />
            <input type="range" className="subs-volume" min={0} max={1} step={0.02} value={muted ? 0 : volume} onChange={(e) => { setVolume(Number(e.target.value)); setMuted(false); }} />
          </div>
        </div>

        <div className="subs-side">
          <Segmented<Tab> value={tab} onChange={setTab} full size="sm" options={[
            { value: "generate", label: t("subs.tab.generate"), icon: <Sparkles size={12} /> },
            { value: "edit", label: t("subs.tab.edit"), icon: <FileText size={12} />, disabled: !cues.length && !job },
            { value: "style", label: t("subs.tab.style"), icon: <Wand2 size={12} /> },
            { value: "export", label: t("subs.tab.export"), icon: <Download size={12} />, disabled: !cues.length },
          ]} />
          <div className="subs-panel">
            {tab === "generate" && (
              <GeneratePanel project={project} catalog={catalog} detect={detect} job={job} french={french}
                srcLang={srcLang} dstLang={dstLang} langOf={langOf}
                onLangs={(src, dst) => {
                  setProject({ ...project, source_lang: src, target_lang: dst });
                  api.put(`/api/subtitles/projects/${pid}`, { source_lang: src, target_lang: dst }).catch(() => undefined);
                }}
                onStart={async (opts) => {
                  try {
                    const r = await api.post<{ jobs: Job[] }>(`/api/subtitles/projects/${pid}/generate`, opts);
                    setJobId(r.jobs[0].id);
                  } catch (e: any) {
                    toast({ kind: "error", title: e.message, body: e.hint });
                  }
                }}
                onCancel={() => job && api.post(`/api/jobs/${job.id}/cancel`)}
                onImported={(p) => { setProject(p); setCues(p.cues); lastCommitted.current = p.cues; setTab("edit"); }}
                onRedetect={startDetect}
              />
            )}
            {tab === "edit" && (
              <CueEditor cues={cues} selected={selected} current={current} playing={pb.playing} maxCps={maxCps}
                canUndo={past.length > 0} canRedo={future.length > 0} onSelect={select} onSeek={pb.seek} onEdit={editLive}
                onAdd={addCue} onDelete={deleteSel} onMerge={mergeSel} onSplit={split} onShift={shift} onReplace={replace} onUndo={undo} onRedo={redo} />
            )}
            {tab === "style" && (
              <StylePanel style={style} catalog={catalog} onChange={(s) => { dirty.current = true; setStyle(s); }} />
            )}
            {tab === "export" && (
              <ExportPanel pid={pid} project={project} burnJob={burnJob} settingsFolder={settings?.subtitles?.output_folder ?? ""}
                onBurn={(id) => setBurnId(id)} />
            )}
          </div>
        </div>
      </div>

      <div className="subs-timeline card">
        <div className="subs-tl-bar">
          <span className="label">{t("subs.timeline")}</span>
          <span className="faint" style={{ fontSize: 11 }}>{t("subs.timelineHint")}</span>
          <span className="spacer" />
          <Button size="sm" variant="ghost" icon={<Plus size={13} />} onClick={addCue}>{t("subs.addHere")}</Button>
          <Button size="sm" variant="ghost" iconOnly icon={<ZoomOut size={13} />} onClick={() => setPps((p) => Math.max(8, p / 1.4))} />
          <input type="range" min={8} max={800} value={pps} onChange={(e) => setPps(Number(e.target.value))} style={{ width: 110 }} />
          <Button size="sm" variant="ghost" iconOnly icon={<ZoomIn size={13} />} onClick={() => setPps((p) => Math.min(800, p * 1.4))} />
        </div>
        <SubtitleTimeline duration={dur} time={pb.time} playing={pb.playing} cues={cues} selected={selected} peaks={peaks} pps={pps}
          maxCps={maxCps} onZoom={(p) => setPps(p)} onSeek={pb.seek} onSelect={(id, m) => { select(id, m); setTab((x) => (x === "generate" ? "edit" : x)); }}
          onChange={(id, patch, done) => editLive(id, patch, done)} />
      </div>
    </div>
  );
}

// ================================================================== scrubber
function Scrubber({ time, duration, buffered, cues, onSeek }: { time: number; duration: number; buffered: number; cues: SubCue[]; onSeek: (t: number) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const seekAt = (clientX: number) => {
    const r = ref.current!.getBoundingClientRect();
    onSeek(Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * duration);
  };
  const d = Math.max(0.001, duration);
  return (
    <div className="subs-scrub" ref={ref}
      onPointerDown={(e) => { (e.target as HTMLElement).setPointerCapture(e.pointerId); seekAt(e.clientX); }}
      onPointerMove={(e) => e.buttons === 1 && seekAt(e.clientX)}>
      <div className="subs-scrub-buf" style={{ width: `${(buffered / d) * 100}%` }} />
      {cues.map((c) => <div key={c.id} className="subs-scrub-cue" style={{ left: `${(c.start / d) * 100}%`, width: `${Math.max(0.15, ((c.end - c.start) / d) * 100)}%` }} />)}
      <div className="subs-scrub-fill" style={{ width: `${(time / d) * 100}%` }} />
    </div>
  );
}

// ================================================================== generate
function GeneratePanel({
  project, catalog, detect, job, french, srcLang, dstLang, langOf, onLangs, onStart, onCancel, onImported, onRedetect,
}: {
  project: SubProject;
  catalog: SubCatalog;
  detect: TaskState | null;
  job?: Job;
  french: boolean;
  srcLang: string;
  dstLang: string;
  langOf: (c: string) => SubLanguage | undefined;
  onLangs: (src: string, dst: string) => void;
  onStart: (opts: Record<string, unknown>) => void;
  onCancel: () => void;
  onImported: (p: SubProject) => void;
  onRedetect: () => void;
}) {
  const t = useT();
  const toast = useApp((s) => s.toast);
  const setPage = useApp((s) => s.setPage);
  const [engine, setEngine] = useState<"local" | "claude">(catalog.engines.claude.configured ? catalog.settings.engine : "local");
  const [polish, setPolish] = useState(project.options?.polish ?? catalog.settings.polish);
  const [glossary, setGlossary] = useState(project.options?.glossary ?? "");
  const name = (l?: SubLanguage) => (l ? `${l.flag} ${french ? l.fr : l.en}` : "—");
  const langOptions = catalog.languages.map((l) => ({ value: l.code, label: name(l) }));
  const detecting = detect?.state === "running";
  const running = job && ["waiting", "processing"].includes(job.status);
  const needLlm = polish || dstLang !== srcLang;
  const downloads: string[] = [];
  if (!catalog.engines.whisper.installed) downloads.push(`Whisper (${bytes(catalog.engines.whisper.size)})`);
  if (needLlm && engine === "local" && !catalog.engines.local.installed) downloads.push(`${catalog.engines.local.model} (${bytes(catalog.engines.local.size)})`);

  const importFile = async () => {
    try {
      const r = await api.post<{ paths: string[] }>("/api/system/pick-files", { kind: "any", multiple: false });
      if (!r.paths?.[0]) return;
      const p = await api.post<SubProject>(`/api/subtitles/projects/${project.id}/import`, { path: r.paths[0] });
      onImported(p);
    } catch (e: any) {
      toast({ kind: "error", title: e.message, body: e.hint });
    }
  };

  return (
    <div className="col" style={{ gap: 14 }}>
      <div className="subs-video-card">
        {project.thumbnail ? <img src={thumbUrl(project.thumbnail)} alt="" /> : <div className="subs-thumb-empty"><Captions size={20} /></div>}
        <div style={{ minWidth: 0 }}>
          <div className="ellipsis" style={{ fontWeight: 650 }} title={project.video.path}>{project.video.name}</div>
          <div className="mono faint" style={{ fontSize: 10.5, lineHeight: 1.6 }}>
            {[project.video.duration ? fmtDuration(project.video.duration) : null,
              project.video.width ? `${project.video.width}×${project.video.height}` : null,
              project.video.fps ? `${Math.round(project.video.fps * 100) / 100} fps` : null,
              project.video.format, project.video.size ? bytes(project.video.size) : null].filter(Boolean).join(" · ")}
          </div>
        </div>
      </div>

      <div className="subs-detect">
        <Languages size={16} />
        {detecting ? (
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12.5 }}>{detect?.progress?.label || t("subs.analyzing")}</div>
            <Progress value={detect?.progress?.percent ?? null} state="active" />
          </div>
        ) : project.detected ? (
          <div style={{ flex: 1 }}>
            <div className="label" style={{ fontSize: 9 }}>{t("subs.detected")}</div>
            <div style={{ fontWeight: 650 }}>{name(langOf(project.detected.code))} <span className="faint mono" style={{ fontSize: 10.5 }}>{Math.round(project.detected.p * 100)} %</span></div>
          </div>
        ) : (
          <div style={{ flex: 1 }} className="faint">{t("subs.notDetected")}</div>
        )}
        {!detecting && <Button size="sm" variant="ghost" onClick={onRedetect}>{project.detected ? t("subs.redetect") : t("subs.detect")}</Button>}
      </div>

      <div className="options-grid">
        <Field label={t("subs.spoken")}>
          <Select value={srcLang || undefined} placeholder={t("subs.auto")} options={langOptions} onChange={(v) => onLangs(v, dstLang === srcLang || !dstLang ? v : dstLang)} />
        </Field>
        <Field label={t("subs.output")}>
          <Select value={dstLang || undefined} options={[{ value: srcLang || "", label: `${t("subs.sameLang")}` }, ...langOptions.filter((o) => o.value !== srcLang)]} onChange={(v) => onLangs(srcLang, v || srcLang)} />
        </Field>
      </div>
      {dstLang && srcLang && dstLang !== srcLang && <Notice>{t("subs.translateNote", { src: name(langOf(srcLang)), dst: name(langOf(dstLang)) })}</Notice>}

      <Field label={t("subs.engine")} hint={engine === "claude" ? t("subs.engineClaudeHint", { model: catalog.engines.claude.model }) : t("subs.engineLocalHint")}>
        <Segmented value={engine} onChange={(v) => setEngine(v as "local" | "claude")} full size="sm" options={[
          { value: "local", label: t("subs.engineLocal") },
          { value: "claude", label: "Claude", disabled: !catalog.engines.claude.configured, tip: catalog.engines.claude.configured ? undefined : t("subs.claudeNotConfigured") },
        ]} />
      </Field>
      {!catalog.engines.claude.configured && (
        <button className="link-button" onClick={() => setPage("settings")}>{t("subs.configureClaude")}</button>
      )}
      <div className="toggle-row">
        <div className="toggle-row-text">
          <div className="toggle-row-title">{t("subs.polish")}</div>
          <div className="toggle-row-desc">{t(srcLang === "fr" || dstLang === "fr" ? "subs.polishDescFr" : "subs.polishDesc")}</div>
        </div>
        <Switch checked={polish} onChange={setPolish} />
      </div>
      <Field label={t("subs.glossary")} hint={t("subs.glossaryHint")}>
        <textarea className="input" rows={2} value={glossary} onChange={(e) => setGlossary(e.target.value)} placeholder={t("subs.glossaryPh")} />
      </Field>
      {downloads.length > 0 && <Notice>{t("subs.downloads", { list: downloads.join(" + ") })}</Notice>}

      {running ? (
        <div className="subs-progress">
          <div className="row" style={{ gap: 8 }}>
            <span style={{ fontWeight: 600, flex: 1 }}>{job?.progress?.label || t("subs.waiting")}</span>
            {job?.progress?.eta ? <span className="mono faint" style={{ fontSize: 11 }}>{t("subs.eta", { s: Math.ceil(job.progress.eta) })}</span> : null}
          </div>
          <Progress value={job?.progress?.percent ?? null} state="active" showValue />
          <Button size="sm" variant="ghost" onClick={onCancel}>{t("common.cancel")}</Button>
        </div>
      ) : (
        <Button variant="primary" size="xl" full icon={<Sparkles size={18} />} disabled={detecting}
          onClick={() => onStart({ source_lang: srcLang || null, target_lang: dstLang || srcLang || null, engine, polish, glossary })}>
          {project.cues.length ? t("subs.regenerate") : t("subs.generate")}
        </Button>
      )}
      {project.cues.length > 0 && !running && <div className="field-hint">{t("subs.regenerateHint")}</div>}
      {project.generated?.notes?.length ? <Notice kind="warning">{project.generated.notes.join(" · ")}</Notice> : null}
      <button className="link-button" onClick={importFile}>{t("subs.importSrt")}</button>
    </div>
  );
}

// ================================================================== export
function ExportPanel({ pid, project, burnJob, settingsFolder, onBurn }: { pid: string; project: SubProject; burnJob?: Job; settingsFolder: string; onBurn: (id: string) => void }) {
  const t = useT();
  const toast = useApp((s) => s.toast);
  const openInPlayer = useApp((s) => s.openInPlayer);
  const [folder, setFolder] = useState(settingsFolder);
  const [timestamps, setTimestamps] = useState(false);
  const [last, setLast] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const exportAs = async (format: string) => {
    setBusy(format);
    try {
      const r = await api.post<{ path: string }>(`/api/subtitles/projects/${pid}/export`, { format, folder: folder || null, timestamps });
      setLast(r.path);
      toast({ kind: "success", title: t("subs.exported", { name: r.path.split(/[\\/]/).pop() ?? "" }), action: { label: t("common.showInFolder"), run: () => api.post("/api/system/open", { path: r.path, reveal: true }) } });
    } catch (e: any) {
      toast({ kind: "error", title: e.message, body: e.hint });
    } finally {
      setBusy(null);
    }
  };
  const burn = async () => {
    try {
      const r = await api.post<{ jobs: Job[] }>(`/api/subtitles/projects/${pid}/burn`, { output_dir: folder || null });
      onBurn(r.jobs[0].id);
    } catch (e: any) {
      toast({ kind: "error", title: e.message, body: e.hint });
    }
  };
  const pick = async () => {
    const r = await api.post<{ path: string | null }>("/api/system/pick-folder", { initial: folder || project.video.path });
    if (!r.path) return;
    setFolder(r.path);
    useApp.getState().saveSettings({ subtitles: { output_folder: r.path } } as any).catch(() => undefined);
  };
  const burning = burnJob && ["waiting", "processing"].includes(burnJob.status);

  return (
    <div className="col" style={{ gap: 14 }}>
      <div className="label">{t("subs.files")}</div>
      <div className="target-grid">
        {[["srt", "subs.fmt.srt"], ["vtt", "subs.fmt.vtt"], ["ass", "subs.fmt.ass"], ["txt", "subs.fmt.txt"]].map(([f, note]) => (
          <button key={f} className="target" onClick={() => exportAs(f)} disabled={!!busy}>
            <span className="target-ext">.{f}</span>
            <span className="target-note">{t(note)}</span>
          </button>
        ))}
      </div>
      <label className="row" style={{ gap: 8, fontSize: 12.5 }}><Switch checked={timestamps} onChange={setTimestamps} /> {t("subs.txtTimes")}</label>

      <div className="label" style={{ marginTop: 6 }}>{t("subs.burnTitle")}</div>
      <div className="field-hint">{t("subs.burnHint")}</div>
      {burning ? (
        <div className="subs-progress">
          <span style={{ fontWeight: 600 }}>{burnJob?.progress?.label || t("subs.waiting")}</span>
          <Progress value={burnJob?.progress?.percent ?? null} state="active" showValue />
          <Button size="sm" variant="ghost" onClick={() => api.post(`/api/jobs/${burnJob!.id}/cancel`)}>{t("common.cancel")}</Button>
        </div>
      ) : burnJob?.status === "completed" && burnJob.result?.file_path ? (
        <div className="subs-progress">
          <span className="row" style={{ gap: 8, fontWeight: 600 }}><Check size={14} style={{ color: "var(--success)" }} /> {String(burnJob.result.file_path).split(/[\\/]/).pop()}</span>
          <div className="row" style={{ gap: 8 }}>
            <Button size="sm" icon={<Play size={13} />} onClick={() => openInPlayer([String(burnJob.result!.file_path)])}>{t("player.play")}</Button>
            <Button size="sm" variant="ghost" icon={<FolderOpen size={13} />} onClick={() => api.post("/api/system/open", { path: burnJob.result!.file_path, reveal: true })}>{t("common.showInFolder")}</Button>
          </div>
        </div>
      ) : (
        <Button variant="primary" full icon={<Captions size={16} />} disabled={project.video.has_video === false} onClick={burn}>{t("subs.burn")}</Button>
      )}
      {burnJob?.status === "failed" && <Notice kind="warning">{burnJob.error?.message}</Notice>}

      <button className="folder-row" onClick={pick}>
        <FolderOpen size={15} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />
        <span style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="label" style={{ fontSize: 9 }}>{t("download.saveTo")}</span>
          <span className="ellipsis mono" style={{ color: "var(--text-primary)" }}>{folder || t("upscale.nextToSource")}</span>
        </span>
      </button>
      {last && <div className="mono faint ellipsis" style={{ fontSize: 10.5 }} title={last}>{t("subs.lastExport")} {last}</div>}
      <div className="field-hint">{t("subs.projectSaved")}</div>
    </div>
  );
}

