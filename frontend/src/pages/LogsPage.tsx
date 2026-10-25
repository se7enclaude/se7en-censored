import { Bug, Download, Pause, Play, Search, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, mediaUrl } from "../api/client";
import { events } from "../api/ws";
import type { LogEntry } from "../api/types";
import { Button, PageHead, Segmented, Switch } from "../components/ui";
import { useT } from "../i18n";
import { useApp } from "../store/app";

type Level = "debug" | "info" | "warning" | "error";
const ORDER = ["debug", "info", "warning", "error", "critical"];

export function LogsPage() {
  const t = useT();
  const settings = useApp((s) => s.settings);
  const save = useApp((s) => s.saveSettings);
  const [entries, setEntries] = useState<LogEntry[]>([]);
  const [level, setLevel] = useState<Level>("info");
  const [category, setCategory] = useState("");
  const [q, setQ] = useState("");
  const [follow, setFollow] = useState(true);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.get<{ entries: LogEntry[] }>("/api/logs?level=debug").then((r) => setEntries(r.entries));
    return events.on((ev) => {
      if (ev.type === "log") setEntries((xs) => (xs.length > 4000 ? [...xs.slice(-3000), ev.data] : [...xs, ev.data]));
    });
  }, []);

  const shown = useMemo(() => {
    const min = ORDER.indexOf(level);
    const ql = q.toLowerCase();
    return entries.filter((e) => ORDER.indexOf(e.level) >= min && (!category || e.category === category) && (!ql || e.message.toLowerCase().includes(ql)));
  }, [entries, level, category, q]);

  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [shown, follow]);

  const counts = useMemo(() => ({ warning: entries.filter((e) => e.level === "warning").length, error: entries.filter((e) => e.level === "error" || e.level === "critical").length }), [entries]);

  return (
    <div className="page-inner wide">
      <PageHead
        eyebrow={t("logs.eyebrow", { w: counts.warning, e: counts.error })}
        title={t("logs.title")}
        right={
          <label className="row card" style={{ padding: "8px 14px", gap: 10, cursor: "pointer" }}>
            <Bug size={14} style={{ color: settings?.advanced.debug_mode ? "var(--accent)" : "var(--text-tertiary)" }} />
            <span className="label" style={{ color: "var(--text-primary)" }}>{t("adv.debug")}</span>
            <Switch checked={!!settings?.advanced.debug_mode} onChange={(v) => save({ advanced: { debug_mode: v } })} />
          </label>
        }
      />
      <div className="row" style={{ marginBottom: 12, flexWrap: "wrap" }}>
        <Segmented<Level> value={level} onChange={setLevel} options={(["debug", "info", "warning", "error"] as Level[]).map((l) => ({ value: l, label: t(`logs.level.${l}`) }))} />
        <Segmented value={category} onChange={setCategory} options={["", "app", "download", "process", "gpu"].map((c) => ({ value: c, label: c ? t(`logs.cat.${c}`) : t("queue.filter.all") }))} />
        <div className="search" style={{ width: 260 }}>
          <Search size={14} style={{ top: 10 }} />
          <input className="input input-sm" placeholder={t("logs.search")} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="spacer" />
        <Button size="sm" icon={follow ? <Pause size={14} /> : <Play size={14} />} onClick={() => setFollow(!follow)}>{follow ? t("logs.pause") : t("logs.follow")}</Button>
        <Button size="sm" icon={<Download size={14} />} onClick={() => window.open(mediaUrl("/api/logs/export"), "_blank")}>{t("logs.export")}</Button>
        <Button size="sm" variant="ghost" icon={<Trash2 size={14} />} onClick={async () => { await api.del("/api/logs"); setEntries([]); }}>{t("common.clear")}</Button>
      </div>
      <div className="logs" ref={box} onWheel={() => box.current && box.current.scrollTop + box.current.clientHeight < box.current.scrollHeight - 30 && setFollow(false)}>
        {shown.length === 0 && <div className="faint" style={{ padding: 20, textAlign: "center" }}>{t("logs.empty")}</div>}
        {shown.map((e) => (
          <div key={e.seq} className={`log-line log-${e.level}`}>
            <span className="log-ts">{new Date(e.ts * 1000).toLocaleTimeString(undefined, { hour12: false })}.{String(Math.floor((e.ts % 1) * 1000)).padStart(3, "0")}</span>
            <span className="log-level">{e.level}</span>
            <span className="log-cat">{e.category}{e.job_id ? `·${e.job_id.slice(0, 4)}` : ""}</span>
            <span className="log-msg">{e.message}{e.exception ? `\n${e.exception}` : ""}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
