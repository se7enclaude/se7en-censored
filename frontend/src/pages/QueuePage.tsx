import { AnimatePresence, motion } from "motion/react";
import {
  ArrowDown,
  AudioLines,
  Captions,
  ArrowUp,
  Ban,
  CheckCircle2,
  Download,
  File,
  FolderOpen,
  ListOrdered,
  Pause,
  Play,
  PlayCircle,
  RefreshCcw,
  RotateCcw,
  Scissors,
  Sparkles,
  Star,
  Trash2,
} from "lucide-react";
import { useMemo, useState } from "react";
import { api, thumbUrl } from "../api/client";
import type { Job } from "../api/types";
import { Badge, Button, Empty, ErrorAlert, PageHead, PlatformChip, Progress, Segmented, StatusBadge, Thumb } from "../components/ui";
import { useT } from "../i18n";
import { bytes, eta, speed } from "../lib/format";
import { useApp } from "../store/app";

type Filter = "all" | "download" | "upscale" | "tools";
const FILTER_KINDS: Record<Filter, string[] | null> = { all: null, download: ["download"], upscale: ["upscale"], tools: ["convert", "trim", "studio", "subtitles"] };
const ORDER: Record<string, number> = { downloading: 0, processing: 0, analyzing: 0, waiting: 1, paused: 2, failed: 3, completed: 4, canceled: 5 };

export function QueuePage() {
  const t = useT();
  const jobs = useApp((s) => s.jobs);
  const toast = useApp((s) => s.toast);
  const [filter, setFilter] = useState<Filter>("all");

  const list = useMemo(
    () =>
      Object.values(jobs)
        .filter((j) => !FILTER_KINDS[filter] || FILTER_KINDS[filter]!.includes(j.kind))
        .sort((a, b) => (ORDER[a.status] ?? 9) - (ORDER[b.status] ?? 9) || b.priority - a.priority || a.position - b.position),
    [jobs, filter],
  );
  const all = Object.values(jobs);
  const count = (s: string[]) => all.filter((j) => s.includes(j.status)).length;
  const totalSpeed = all.filter((j) => j.status === "downloading").reduce((a, j) => a + (j.progress.speed || 0), 0);

  const bulk = async (action: string) => {
    const r = await api.post<{ count: number }>("/api/jobs-bulk", { action, kind: FILTER_KINDS[filter]?.join(",") ?? null });
    toast({ kind: "info", title: t(`queue.bulk.${action}`, { n: r.count }) });
  };

  return (
    <div className="page-inner">
      <PageHead
        eyebrow={t("queue.eyebrow", { n: count(["downloading", "processing", "analyzing", "waiting"]) })}
        title={t("queue.title")}
        right={
          <>
            <Segmented<Filter>
              value={filter}
              onChange={setFilter}
              options={[
                { value: "all", label: t("queue.filter.all") },
                { value: "download", label: t("queue.filter.download") },
                { value: "upscale", label: t("queue.filter.upscale") },
                { value: "tools", label: t("queue.filter.tools") },
              ]}
            />
          </>
        }
      />
      <div className="row rise" style={{ marginBottom: 16, flexWrap: "wrap", animationDelay: "60ms" }}>
        <Button size="sm" variant="outline" icon={<Pause size={13} />} onClick={() => bulk("pause_all")}>{t("queue.pauseAll")}</Button>
        <Button size="sm" variant="outline" icon={<Play size={13} />} onClick={() => bulk("resume_all")}>{t("queue.resumeAll")}</Button>
        <Button size="sm" variant="outline" icon={<RotateCcw size={13} />} onClick={() => bulk("retry_failed")} disabled={!count(["failed"])}>{t("queue.retryFailed")}</Button>
        <div className="spacer" />
        <Button size="sm" variant="ghost" icon={<Trash2 size={13} />} onClick={() => bulk("clear_finished")}>{t("queue.clearFinished")}</Button>
      </div>

      <div className="stat-row rise" style={{ animationDelay: "90ms" }}>
        <Stat label={t("queue.stat.active")} value={count(["downloading", "processing", "analyzing"])} color="var(--info)" />
        <Stat label={t("queue.stat.waiting")} value={count(["waiting", "paused"])} />
        <Stat label={t("queue.stat.completed")} value={count(["completed"])} color="var(--success)" />
        <Stat label={t("queue.stat.failed")} value={count(["failed"])} color={count(["failed"]) ? "var(--danger)" : undefined} />
        <Stat label={t("queue.stat.speed")} value={speed(totalSpeed)} />
      </div>

      {list.length === 0 ? (
        <Empty icon={<ListOrdered size={26} />} title={t("queue.emptyTitle")} body={t("queue.emptyBody")} />
      ) : (
        <div>
          <AnimatePresence initial={false}>
            {list.map((j, i) => (
              <motion.div key={j.id} className="job-wrap" layout initial={{ opacity: 0, y: 14 }} animate={{ opacity: 1, y: 0, transition: { delay: Math.min(i, 10) * 0.06, duration: 0.5, ease: [0.16, 1, 0.3, 1] } }} exit={{ opacity: 0, height: 0 }}>
                <JobRow job={j} index={i + 1} />
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: React.ReactNode; color?: string }) {
  return (
    <div className="card stat">
      <div className="stat-value" style={{ color }}>{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

export function JobRow({ job: j, index }: { job: Job; index: number }) {
  const t = useT();
  const p = j.progress || {};
  const act = (a: string, value?: unknown) => api.post(`/api/jobs/${j.id}/${a}`, value !== undefined ? { value } : {});
  const open = (reveal: boolean) => api.post("/api/system/open", { path: j.result?.output_path || j.result?.file_path, reveal });
  const active = ["downloading", "processing", "analyzing"].includes(j.status);
  const pct = j.status === "completed" ? 100 : p.percent;
  const state = j.status === "completed" ? "done" : j.status === "paused" ? "paused" : j.status === "failed" ? "failed" : active ? "active" : undefined;
  const o = j.options || {};
  const openInPlayer = useApp((s) => s.openInPlayer);
  const detail =
    j.kind === "download"
      ? o.mode === "image"
        ? [t("mode.imageShort"), String(o.image_format || "jpg").toUpperCase()]
        : [o.mode === "audio" ? (o.audio_format || "").toUpperCase() : o.quality === "best" ? t("quality.best") : `${o.quality}p`, o.mode !== "audio" ? String(o.container || "").toUpperCase() : null, o.preset]
      : j.kind === "convert"
        ? [`→ ${String(o.target || "").toUpperCase()}`, o.target_size_mb ? `${o.target_size_mb} MB` : null, o.resolution ? `${o.resolution}p` : null]
        : j.kind === "trim"
          ? [t("queue.parts", { n: (o.segments || []).length }), o.mode === "precise" ? t("trim.precise") : t("trim.fast")]
          : j.kind === "subtitles"
            ? [o.op === "burn" ? t("subs.burnShort") : t("subs.generateShort"), o.target_lang && o.target_lang !== o.source_lang ? `→ ${o.target_lang}` : null, o.engine === "claude" ? "Claude" : null]
            : j.kind === "studio"
            ? o.op === "separate"
              ? [t("studio.separate"), t(`studio.stems.${o.stems || "both"}`)]
              : [t("studio.clean"), t(`studio.sound.${o.sound || "voice"}`), t(`studio.strength.${o.strength || "medium"}`)]
          : [
              Number(o.scale) === 1 && !o.width ? null : o.model,
              Number(o.scale) === 1 && !o.width ? null : o.width ? `${o.width}×${o.height}` : `×${o.scale}`,
              o.face ? t("tool.face") : null,
              o.remove_bg ? t("tool.background") : null,
              o.colorize ? t("tool.colorize") : null,
              o.interpolate ? `${t("tool.interpolate")} ${String(o.interpolate).length > 1 ? `${o.interpolate} fps` : `×${o.interpolate}`}` : null,
            ];
  const kindIcon = j.kind === "upscale" ? <Sparkles size={18} /> : j.kind === "convert" ? <RefreshCcw size={18} /> : j.kind === "trim" ? <Scissors size={18} /> : j.kind === "studio" ? <AudioLines size={18} /> : j.kind === "subtitles" ? <Captions size={18} /> : <Download size={18} />;

  return (
    <div className={`job ${active ? "is-active" : ""}`}>
      <div className="job-index">{String(index).padStart(2, "0")}</div>
      <div className="job-thumb">
        <Thumb src={thumbUrl(j.thumbnail)} icon={kindIcon} />
      </div>
      <div style={{ minWidth: 0 }}>
        <div className="row">
          <div className="job-title ellipsis selectable" style={{ flex: 1 }}>{j.title || j.source}</div>
          <StatusBadge status={j.status} />
        </div>
        <div className="job-meta">
          <PlatformChip platform={j.platform} name={j.kind !== "download" ? t(`queue.kindLabel.${j.kind}`) : o.platform_name || j.platform} />
          {detail.filter(Boolean).map((x, k) => (
            <Badge key={k} mono>{String(x)}</Badge>
          ))}
          {j.priority > 0 && <Badge tone="accent" icon={<Star size={11} />}>{t("queue.priority")}</Badge>}
          {j.attempts > 0 && j.status !== "completed" && <Badge>{t("queue.attempt", { n: j.attempts + 1 })}</Badge>}
        </div>
        {j.status !== "failed" && j.status !== "canceled" && (
          <div style={{ marginTop: 10 }}>
            <Progress value={pct} state={(j.kind !== "download" && state === "active" && pct != null ? "neutral" : state) as any} showValue={active || j.status === "paused"} />
          </div>
        )}
        {(active || j.status === "paused" || j.status === "waiting") && (
          <div className="job-stats">
            {p.label && <span style={{ color: "var(--text)" }}>{p.label}</span>}
            {j.kind === "download" && p.stage === "downloading" && (
              <>
                <span>{t("queue.speed")} <b>{speed(p.speed)}</b></span>
                <span>{t("queue.downloaded")} <b>{p.unit === "files" ? `${p.downloaded ?? 0}` : bytes(p.downloaded)}</b>{p.total ? ` / ${p.unit === "files" ? p.total : bytes(p.total)}` : ""}</span>
                {p.total && p.downloaded != null && p.unit !== "files" && <span>{t("queue.remaining")} <b>{bytes(Math.max(0, p.total - p.downloaded))}</b></span>}
                <span>{t("queue.eta")} <b>{eta(p.eta)}</b></span>
                {p.connections && <span>{t("queue.connections")} <b>{p.connections}</b></span>}
              </>
            )}
            {j.kind !== "download" && !p.frames_total && p.eta ? <span>{t("queue.eta")} <b>{eta(p.eta)}</b></span> : null}
            {j.kind === "download" && p.stage === "processing" && p.eta ? <span>{t("queue.eta")} <b>{eta(p.eta)}</b></span> : null}
            {j.kind === "upscale" && p.frames_total && (
              <>
                <span>{t("queue.frames")} <b>{p.frames_done ?? 0} / {p.frames_total}</b></span>
                {p.segment && <span>{t("queue.segment")} <b>{p.segment} / {p.segments_total}</b></span>}
                {p.fps_processing && <span><b>{p.fps_processing.toFixed(2)}</b> fps</span>}
                <span>{t("queue.eta")} <b>{eta(p.eta)}</b></span>
                {p.tile ? <span>tile <b>{p.tile}</b></span> : null}
              </>
            )}
          </div>
        )}
        {j.status === "completed" && j.result && (
          <div className="job-stats">
            <span className="row" style={{ gap: 5, color: "var(--success)" }}><CheckCircle2 size={13} /> {t("queue.done")}</span>
            {j.result.resolution && <span><b>{j.result.resolution}</b></span>}
            {j.result.format && <span>{j.result.format}</span>}
            {j.result.size && <span><b>{bytes(j.result.size)}</b></span>}
            {j.result.meta?.warnings?.length ? <span style={{ color: "var(--warning)" }}>{j.result.meta.warnings[0]}</span> : null}
          </div>
        )}
        {j.status === "failed" && j.error && (
          <div style={{ marginTop: 10 }}>
            <ErrorAlert error={j.error} compact actions={<Button size="sm" icon={<RotateCcw size={13} />} onClick={() => act("retry")}>{t("common.retry")}</Button>} />
          </div>
        )}
        {p.stage === "retrying" && j.error && (
          <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>
            {t("queue.retryReason")}: {j.error.message}
          </div>
        )}
      </div>
      <div className="job-actions">
        {(active || j.status === "waiting") && <Button size="sm" variant="ghost" iconOnly icon={<Pause size={15} />} tip={t("queue.pause")} onClick={() => act("pause")} />}
        {(j.status === "paused" || j.status === "canceled") && <Button size="sm" variant="ghost" iconOnly icon={<Play size={15} />} tip={t("queue.resume")} onClick={() => act("resume")} />}
        {j.status === "failed" && <Button size="sm" variant="ghost" iconOnly icon={<RotateCcw size={15} />} tip={t("common.retry")} onClick={() => act("retry")} />}
        {(j.status === "waiting" || j.status === "paused") && (
          <>
            <Button size="sm" variant="ghost" iconOnly icon={<Star size={15} fill={j.priority > 0 ? "currentColor" : "none"} />} tip={t("queue.togglePriority")} onClick={() => act("priority", j.priority > 0 ? 0 : 5)} />
            <Button size="sm" variant="ghost" iconOnly icon={<ArrowUp size={15} />} tip={t("queue.moveUp")} onClick={() => act("move", "up")} />
            <Button size="sm" variant="ghost" iconOnly icon={<ArrowDown size={15} />} tip={t("queue.moveDown")} onClick={() => act("move", "down")} />
          </>
        )}
        {j.status === "completed" && (
          <>
            <Button size="sm" variant="ghost" iconOnly icon={<PlayCircle size={15} />} tip={t("player.play")} onClick={() => openInPlayer([j.result?.output_path || j.result?.file_path, ...(j.result?.extra_files ?? [])].filter(Boolean))} />
            <Button size="sm" variant="ghost" iconOnly icon={<File size={15} />} tip={t("common.openFile")} onClick={() => open(false)} />
            <Button size="sm" variant="ghost" iconOnly icon={<FolderOpen size={15} />} tip={t("common.showInFolder")} onClick={() => open(true)} />
          </>
        )}
        {!["completed", "canceled", "failed"].includes(j.status) && <Button size="sm" variant="ghost" iconOnly icon={<Ban size={15} />} tip={t("queue.cancel")} onClick={() => act("cancel")} />}
        <Button size="sm" variant="ghost" iconOnly className="btn-danger" icon={<Trash2 size={15} />} tip={t("queue.remove")} onClick={() => act("remove")} />
      </div>
    </div>
  );
}
