import clsx from "clsx";
import { Music2, Pause, Play, Plus, Repeat, Scissors, SkipBack, Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, thumbUrl } from "../api/client";
import type { Job, LocalFile } from "../api/types";
import { Button, ErrorAlert, Field, Notice, PageHead, Segmented, Select, Ticks } from "../components/ui";
import { useFileIntake } from "../hooks/useFileIntake";
import { usePlayback } from "../hooks/usePlayback";
import { useT } from "../i18n";
import { bytes, duration as fmtDur } from "../lib/format";
import { useApp } from "../store/app";

interface Seg {
  id: number;
  start: number;
  end: number;
}

function fmt(t: number): string {
  if (!isFinite(t)) return "—";
  const s = Math.max(0, t);
  const m = Math.floor(s / 60);
  const r = s - m * 60;
  const h = Math.floor(m / 60);
  const body = `${String(m % 60).padStart(2, "0")}:${r.toFixed(2).padStart(5, "0")}`;
  return h ? `${h}:${body}` : body;
}

function parseTime(v: string): number | null {
  const parts = v.trim().split(":").map((x) => Number(x.replace(",", ".")));
  if (parts.some((x) => !isFinite(x))) return null;
  return parts.reduce((a, x) => a * 60 + x, 0);
}

let segSeq = 1;

export function TrimPage() {
  const t = useT();
  const settings = useApp((s) => s.settings);
  const page = useApp((s) => s.page);
  const toast = useApp((s) => s.toast);
  const setPage = useApp((s) => s.setPage);
  const jobs = useApp((s) => s.jobs);
  const openInPlayer = useApp((s) => s.openInPlayer);
  const [file, setFile] = useState<LocalFile | null>(null);
  const [segs, setSegs] = useState<Seg[]>([]);
  const [active, setActive] = useState(0);
  const [peaks, setPeaks] = useState<number[]>([]);
  const [strip, setStrip] = useState<{ t: number; key: string }[]>([]);
  const [removeMode, setRemoveMode] = useState(false);
  const [mode, setMode] = useState<"fast" | "precise">(settings?.tools?.trim_mode ?? "fast");
  const [output, setOutput] = useState<"merge" | "separate">(settings?.tools?.trim_output ?? "merge");
  const [fade, setFade] = useState("0");
  const [loopSel, setLoopSel] = useState(false);
  const [busy, setBusy] = useState(false);
  const [lastJob, setLastJob] = useState<string | null>(null);
  const trackRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const drag = useRef<{ id: number; edge: "start" | "end" | "move"; grab: number; x: number; moved: boolean } | null>(null);

  const pb = usePlayback(file?.path ?? null, "trim");
  const { plan, src, mediaRef, time, duration, playing, seek, toggle, error } = pb;
  const isAudio = plan?.kind === "audio";

  const intake = useFileIntake({ page: "trim", kind: "av", multiple: false, onFiles: (fs) => fs[0] && setFile(fs[0]) });

  // new file: one part = whole file, load waveform + filmstrip
  useEffect(() => {
    setPeaks([]);
    setStrip([]);
    setLastJob(null);
    if (!file?.duration) {
      setSegs([]);
      return;
    }
    setSegs([{ id: segSeq++, start: 0, end: file.duration }]);
    setActive(0);
    api.get<{ peaks: number[] }>(`/api/media/waveform?path=${encodeURIComponent(file.path)}&buckets=1400`).then((r) => setPeaks(r.peaks)).catch(() => undefined);
    if (file.kind === "video")
      api.get<{ frames: { t: number; key: string }[] }>(`/api/media/filmstrip?path=${encodeURIComponent(file.path)}&count=14&height=80`).then((r) => setStrip(r.frames)).catch(() => undefined);
  }, [file?.path]); // eslint-disable-line react-hooks/exhaustive-deps

  // waveform drawing
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const draw = () => {
      const w = (c.width = c.clientWidth * devicePixelRatio);
      const h = (c.height = c.clientHeight * devicePixelRatio);
      const g = c.getContext("2d");
      if (!g) return;
      g.clearRect(0, 0, w, h);
      if (!peaks.length) return;
      const step = w / peaks.length;
      g.fillStyle = isAudio ? "rgba(244,243,241,0.55)" : "rgba(244,243,241,0.35)";
      for (let i = 0; i < peaks.length; i++) {
        const v = Math.max(0.02, peaks[i]) * h * (isAudio ? 0.92 : 0.9);
        g.fillRect(i * step, (h - v) / 2, Math.max(1, step * 0.7), v);
      }
    };
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(c);
    return () => ro.disconnect();
  }, [peaks, isAudio]);

  const cur = segs[Math.min(active, segs.length - 1)];
  const sorted = useMemo(() => [...segs].sort((a, b) => a.start - b.start), [segs]);
  const kept = useMemo(() => {
    const total = sorted.reduce((a, s) => a + Math.max(0, s.end - s.start), 0);
    return removeMode ? Math.max(0, duration - total) : total;
  }, [sorted, removeMode, duration]);

  const update = useCallback((id: number, patch: Partial<Seg>) => {
    setSegs((xs) =>
      xs.map((s) => {
        if (s.id !== id) return s;
        const n = { ...s, ...patch };
        n.start = Math.max(0, Math.min(n.start, duration));
        n.end = Math.max(0, Math.min(n.end, duration));
        if (n.end - n.start < 0.05) {
          if (patch.start !== undefined) n.start = Math.max(0, n.end - 0.05);
          else n.end = Math.min(duration, n.start + 0.05);
        }
        return n;
      }),
    );
  }, [duration]);

  const setIn = () => cur && update(cur.id, { start: Math.min(time, cur.end - 0.05) });
  const setOut = () => cur && update(cur.id, { end: Math.max(time, cur.start + 0.05) });
  const addPart = () => {
    const len = Math.max(1, duration * 0.1);
    const start = Math.min(time, Math.max(0, duration - len));
    const s = { id: segSeq++, start, end: Math.min(duration, start + len) };
    setSegs((xs) => [...xs, s]);
    setActive(segs.length);
  };

  // loop / stop at the end of the active part while previewing it
  useEffect(() => {
    if (!loopSel || !cur || !playing) return;
    if (time >= cur.end) seek(cur.start);
  }, [time, loopSel, cur, playing, seek]);

  useEffect(() => {
    if (page !== "trim") mediaRef.current?.pause();
  }, [page, mediaRef]);

  // keyboard: I / O / space / arrows
  useEffect(() => {
    if (page !== "trim" || !file) return;
    const onKey = (e: KeyboardEvent) => {
      const tg = e.target as HTMLElement;
      if (tg && (tg.tagName === "INPUT" || tg.tagName === "TEXTAREA") || e.ctrlKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === "i") setIn();
      else if (k === "o") setOut();
      else if (k === " ") {
        e.preventDefault();
        toggle();
      } else if (k === "arrowright") seek(time + (e.shiftKey ? 1 : 1 / 25));
      else if (k === "arrowleft") seek(time - (e.shiftKey ? 1 : 1 / 25));
      else if (k === "+" || k === "=") addPart();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // timeline pointer handling
  const toTime = (clientX: number) => {
    const r = trackRef.current!.getBoundingClientRect();
    return Math.max(0, Math.min(1, (clientX - r.left) / r.width)) * duration;
  };
  const onPointerDown = (e: React.PointerEvent) => {
    if (!duration) return;
    const target = e.target as HTMLElement;
    const id = Number(target.dataset.seg);
    const edge = target.dataset.edge as "start" | "end" | "move" | undefined;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    if (id && edge) {
      const s = segs.find((x) => x.id === id)!;
      drag.current = { id, edge, grab: toTime(e.clientX) - s.start, x: e.clientX, moved: false };
      setActive(segs.findIndex((x) => x.id === id));
    } else {
      drag.current = null;
      seek(toTime(e.clientX));
    }
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current || !(e.buttons & 1)) return;
    const tt = toTime(e.clientX);
    const d = drag.current;
    if (!d.moved) {
      if (d.edge === "move" && Math.abs(e.clientX - d.x) < 4) return; // a click on a part seeks, a drag moves it
      d.moved = true;
    }
    const s = segs.find((x) => x.id === d.id);
    if (!s) return;
    if (d.edge === "start") update(d.id, { start: Math.min(tt, s.end - 0.05) });
    else if (d.edge === "end") update(d.id, { end: Math.max(tt, s.start + 0.05) });
    else {
      const len = s.end - s.start;
      const start = Math.max(0, Math.min(duration - len, tt - d.grab));
      update(d.id, { start, end: start + len });
    }
    seek(d.edge === "end" ? Math.max(tt, s.start) : d.edge === "start" ? Math.min(tt, s.end) : tt);
  };
  const onPointerUp = (e: React.PointerEvent) => {
    if (drag.current && !drag.current.moved) seek(toTime(e.clientX));
    drag.current = null;
  };

  const exportIt = async () => {
    if (!file || !sorted.length) return;
    setBusy(true);
    try {
      const r = await api.post<{ jobs: Job[] }>("/api/trim/jobs", {
        path: file.path,
        segments: sorted.map((s) => ({ start: s.start, end: s.end })),
        options: { mode, output, remove: removeMode, fade: Number(fade) },
      });
      setLastJob(r.jobs[0]?.id ?? null);
      toast({ kind: "success", title: t("trim.queued"), action: { label: t("download.viewQueue"), run: () => setPage("queue") } });
    } catch (e: any) {
      toast({ kind: "error", title: e.message, body: e.hint });
    } finally {
      setBusy(false);
    }
  };
  const job = lastJob ? jobs[lastJob] : null;
  // Nothing would change: the parts cover the whole file (keep) or nothing (remove).
  const noCut = duration > 0 && (removeMode ? kept >= duration - 0.05 : kept >= duration - 0.05);

  const pct = (v: number) => `${duration ? (v / duration) * 100 : 0}%`;

  return (
    <div className="page-inner wide">
      <PageHead
        eyebrow={t("trim.eyebrow")}
        title={t("trim.title")}
        echo={file ? undefined : t("trim.echo")}
        right={<Button icon={<Plus size={15} />} onClick={intake.pick}>{file ? t("trim.otherFile") : t("trim.open")}</Button>}
      />
      <input {...intake.inputProps} />
      {!file ? (
        <div className="drop-zone rise" onClick={intake.pick}>
          <Ticks inset={14} />
          <div>
            <div className="empty-icon" style={{ borderColor: "var(--accent)", color: "var(--accent)" }}><Scissors size={24} /></div>
            <div className="empty-title">{t("trim.emptyTitle")}</div>
            <div className="faint" style={{ maxWidth: 460, margin: "0 auto" }}>{t("trim.emptyBody")}</div>
            <Button variant="primary" style={{ marginTop: 18 }} icon={<Plus size={16} />} onClick={(e) => { e.stopPropagation(); intake.pick(); }}>{t("trim.open")}</Button>
            <div className="label" style={{ marginTop: 14 }}>{t("trim.keys")}</div>
          </div>
        </div>
      ) : (
        <div className="tool-layout">
          <div className="col rise" style={{ gap: 14, minWidth: 0 }}>
            {error && <ErrorAlert error={error} />}
            <div className={clsx("trim-stage", isAudio && "audio")} onClick={toggle}>
              {plan && <video ref={mediaRef} key={file.path} src={src} className={clsx(isAudio && "hidden")} playsInline preload="auto" />}
              {isAudio && (
                <div className="trim-audio">
                  <Music2 size={36} />
                  <div className="selectable" style={{ fontWeight: 700 }}>{plan?.title || file.name}</div>
                </div>
              )}
              {!playing && plan && <div className="player-bigplay"><Play size={28} fill="currentColor" /></div>}
              <Ticks inset={12} />
            </div>

            <div className="card trim-panel">
              <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                <button className="pbtn pbtn-main" onClick={toggle}>{playing ? <Pause size={17} fill="currentColor" /> : <Play size={17} fill="currentColor" />}</button>
                <button className="pbtn" onClick={() => cur && seek(cur.start)} data-tip={t("trim.toStart")}><SkipBack size={15} /></button>
                <button className={clsx("pbtn", loopSel && "on")} onClick={() => { setLoopSel(!loopSel); if (!loopSel && cur) { seek(cur.start); if (!playing) toggle(); } }} data-tip={t("trim.loopSel")}><Repeat size={15} /></button>
                <span className="player-time tabular">{fmt(time)} <span className="faint">/ {fmt(duration)}</span></span>
                <span className="spacer" />
                <Button size="sm" onClick={setIn}>{t("trim.setIn")} <span className="kbd">I</span></Button>
                <Button size="sm" onClick={setOut}>{t("trim.setOut")} <span className="kbd">O</span></Button>
                <Button size="sm" variant="ghost" icon={<Plus size={13} />} onClick={addPart}>{t("trim.addPart")}</Button>
              </div>

              <div
                ref={trackRef}
                className={clsx("trim-track", removeMode && "remove")}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
              >
                <div className="trim-strip">
                  {strip.map((f) => (
                    <img key={f.key} src={thumbUrl(f.key)} alt="" draggable={false} />
                  ))}
                </div>
                <canvas ref={canvasRef} className={clsx("trim-wave", isAudio && "full")} />
                {segs.map((s, i) => (
                  <div
                    key={s.id}
                    className={clsx("trim-seg", i === active && "on")}
                    style={{ left: pct(s.start), width: `calc(${pct(s.end - s.start)})` }}
                    data-seg={s.id}
                    data-edge="move"
                  >
                    <span className="trim-handle l" data-seg={s.id} data-edge="start" />
                    <span className="trim-handle r" data-seg={s.id} data-edge="end" />
                    <span className="trim-seg-label">{i + 1}</span>
                  </div>
                ))}
                <div className="trim-head" style={{ left: pct(time) }} />
              </div>
              <div className="row mono faint" style={{ fontSize: 10.5, justifyContent: "space-between" }}>
                {[0, 0.25, 0.5, 0.75, 1].map((f) => <span key={f}>{fmtDur(duration * f)}</span>)}
              </div>
            </div>

            <div className="card" style={{ padding: "6px 0" }}>
              {segs.map((s, i) => (
                <div key={s.id} className={clsx("seg-row", i === active && "on")} onClick={() => setActive(i)}>
                  <span className="seg-num">{String(i + 1).padStart(2, "0")}</span>
                  <TimeInput value={s.start} onChange={(v) => update(s.id, { start: Math.min(v, s.end - 0.05) })} />
                  <span className="faint">→</span>
                  <TimeInput value={s.end} onChange={(v) => update(s.id, { end: Math.max(v, s.start + 0.05) })} />
                  <span className="mono faint" style={{ marginLeft: 6 }}>{fmt(s.end - s.start)}</span>
                  <span className="spacer" />
                  <Button size="sm" variant="ghost" iconOnly icon={<Play size={13} />} tip={t("trim.previewPart")} onClick={(e) => { e.stopPropagation(); setActive(i); seek(s.start); if (!playing) toggle(); }} />
                  {segs.length > 1 && <Button size="sm" variant="ghost" iconOnly icon={<X size={13} />} onClick={(e) => { e.stopPropagation(); setSegs((xs) => xs.filter((x) => x.id !== s.id)); setActive(0); }} />}
                </div>
              ))}
            </div>
          </div>

          <div className="side-panel rise" style={{ animationDelay: "90ms" }}>
            <div className="card card-pad col" style={{ gap: 14 }}>
              <div className="col" style={{ gap: 6 }}>
                <span className="label">{t("upscale.file")}</span>
                <span style={{ font: "700 17px/1.2 var(--font-display)", wordBreak: "break-all" }}>{file.name}</span>
                <span className="mono faint" style={{ fontSize: 11 }}>{[fmtDur(file.duration), file.width ? `${file.width}×${file.height}` : null, bytes(file.size)].filter(Boolean).join(" · ")}</span>
              </div>
              <Field label={t("trim.what")}>
                <Segmented value={removeMode ? "remove" : "keep"} onChange={(v) => setRemoveMode(v === "remove")} full options={[{ value: "keep", label: t("trim.keep") }, { value: "remove", label: t("trim.remove") }]} />
              </Field>
              <div className="res-flow" style={{ justifyContent: "space-between" }}>
                <span className="label">{t("trim.result")}</span>
                <span className="mono" style={{ color: "var(--accent-text)" }}>{fmt(kept)}</span>
              </div>
              <Field label={t("trim.precision")} hint={mode === "fast" ? t("trim.fastHint") : t("trim.preciseHint")}>
                <Segmented value={mode} onChange={setMode} full options={[{ value: "fast", label: t("trim.fast") }, { value: "precise", label: t("trim.precise") }]} />
              </Field>
              {(segs.length > 1 || removeMode) && (
                <Field label={t("trim.output")}>
                  <Segmented value={output} onChange={setOutput} full options={[{ value: "merge", label: t("trim.merge") }, { value: "separate", label: t("trim.separate") }]} />
                </Field>
              )}
              <Field label={t("trim.fade")} hint={t("trim.fadeHint")}>
                <Select value={fade} onChange={setFade} options={["0", "0.5", "1", "2", "3"].map((f) => ({ value: f, label: f === "0" ? t("level.off") : `${f} s` }))} />
              </Field>
              <Button variant="primary" size="xl" full icon={<Scissors size={18} />} loading={busy} disabled={!segs.length || kept < 0.05 || (noCut && fade === "0")} onClick={exportIt}>
                {t("trim.export")}
              </Button>
              {job && (
                job.status === "completed" && job.result?.file_path ? (
                  <Notice title={t("trim.done")}>
                    <div className="row" style={{ marginTop: 8, flexWrap: "wrap" }}>
                      <Button size="sm" icon={<Play size={13} />} onClick={() => openInPlayer([job.result!.file_path, ...(job.result!.extra_files ?? [])])}>{t("player.play")}</Button>
                      <Button size="sm" variant="ghost" onClick={() => api.post("/api/system/open", { path: job.result!.file_path, reveal: true })}>{t("common.showInFolder")}</Button>
                    </div>
                  </Notice>
                ) : job.status === "failed" && job.error ? (
                  <ErrorAlert error={job.error} compact />
                ) : (
                  <div className="faint" style={{ fontSize: 12 }}>{job.progress?.label ?? t("status.waiting")} {job.progress?.percent != null ? `· ${Math.round(job.progress.percent)}%` : ""}</div>
                )
              )}
              <div className="field-hint">{noCut && fade === "0" ? t("trim.noCut") : t("trim.safe")}</div>
              <Button size="sm" variant="ghost" className="btn-danger" icon={<Trash2 size={13} />} onClick={() => setFile(null)}>{t("trim.close")}</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function TimeInput({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  const [v, setV] = useState(fmt(value));
  const [editing, setEditing] = useState(false);
  useEffect(() => {
    if (!editing) setV(fmt(value));
  }, [value, editing]);
  return (
    <input
      className="input input-sm mono time-input"
      value={v}
      onFocus={() => setEditing(true)}
      onClick={(e) => e.stopPropagation()}
      onChange={(e) => setV(e.target.value)}
      onBlur={() => {
        setEditing(false);
        const p = parseTime(v);
        if (p !== null) onChange(p);
        else setV(fmt(value));
      }}
      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
    />
  );
}
