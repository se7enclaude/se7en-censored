import { motion } from "motion/react";
import { Activity, BarChart3, Clock, Cpu, Download, Flame, HardDrive, RefreshCcw, Scissors, Sparkles } from "lucide-react";
import { ReactNode, useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import type { Stats } from "../api/types";
import { Empty, PageHead, Segmented, Ticks } from "../components/ui";
import { useT } from "../i18n";
import { bytes, eta, speed } from "../lib/format";
import { useApp } from "../store/app";

const KIND_COLORS: Record<string, string> = {
  download: "var(--accent)",
  upscale: "var(--fog-1)",
  convert: "var(--fog-3)",
  trim: "var(--fog-4)",
};
const KINDS = ["download", "upscale", "convert", "trim", "studio", "subtitles"] as const;

function hours(sec: number): string {
  if (!sec) return "0 h";
  if (sec < 60) return `${Math.round(sec)} s`;
  if (sec < 3600) return `${Math.round(sec / 60)} min`;
  return `${(sec / 3600).toFixed(sec < 36000 ? 1 : 0)} h`;
}

export function StatsPage() {
  const t = useT();
  const version = useApp((s) => s.historyVersion);
  const [days, setDays] = useState("30");
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    api.get<Stats>(`/api/stats?days=${days}`).then(setStats).catch(() => undefined);
  }, [days, version]);

  const maxDay = useMemo(() => Math.max(1, ...(stats?.timeline ?? []).map((d) => KINDS.reduce((a, k) => a + Number(d[k] ?? 0), 0))), [stats]);
  const maxPlat = Math.max(1, ...(stats?.platforms ?? []).map((p) => p.count));
  const maxHour = Math.max(1, ...(stats?.hours ?? []));

  if (!stats) return <div className="page-inner" />;
  const k = stats.by_kind;
  const dl = k.download ?? { count: 0, bytes: 0, seconds: 0 };
  const up = k.upscale ?? { count: 0, bytes: 0, seconds: 0 };

  return (
    <div className="page-inner">
      <PageHead
        eyebrow={t("stats.eyebrow")}
        title={t("stats.title")}
        right={<Segmented value={days} onChange={setDays} options={["7", "30", "90", "365"].map((d) => ({ value: d, label: t("stats.days", { n: d }) }))} />}
      />
      {stats.total === 0 ? (
        <Empty icon={<BarChart3 size={26} />} title={t("stats.emptyTitle")} body={t("stats.emptyBody")} />
      ) : (
        <>
          <div className="kpi-grid rise">
            <Kpi icon={<Download size={15} />} label={t("stats.downloads")} value={dl.count} sub={bytes(dl.bytes)} accent />
            <Kpi icon={<Clock size={15} />} label={t("stats.mediaTime")} value={hours(stats.media_seconds)} sub={t("stats.mediaTimeSub")} />
            <Kpi icon={<Sparkles size={15} />} label={t("stats.ai")} value={up.count} sub={t("stats.gpuTime", { t: hours(up.seconds) })} />
            <Kpi icon={<RefreshCcw size={15} />} label={t("stats.conversions")} value={(k.convert?.count ?? 0) + (k.trim?.count ?? 0)} sub={t("stats.conversionsSub", { c: k.convert?.count ?? 0, t: k.trim?.count ?? 0 })} />
            <Kpi icon={<HardDrive size={15} />} label={t("stats.onDisk")} value={bytes(stats.on_disk)} sub={stats.missing ? t("stats.missing", { n: stats.missing }) : t("stats.allThere")} />
            <Kpi icon={<Flame size={15} />} label={t("stats.streak")} value={stats.streak} sub={t("stats.activeDays", { n: stats.active_days })} />
          </div>

          <div className="card card-pad rise" style={{ marginTop: 16, animationDelay: "60ms" }}>
            <div className="row" style={{ marginBottom: 14 }}>
              <span className="label">{t("stats.activity")}</span>
              <span className="spacer" />
              {KINDS.map((kk) => (
                <span key={kk} className="row label" style={{ gap: 6 }}>
                  <span style={{ width: 8, height: 8, background: KIND_COLORS[kk] }} /> {t(`stats.kind.${kk}`)}
                </span>
              ))}
            </div>
            <div className="chart-bars">
              {stats.timeline.map((d, i) => {
                const total = KINDS.reduce((a, kk) => a + Number(d[kk] ?? 0), 0);
                return (
                  <div key={d.day} className="chart-col" data-tip={`${d.day} · ${total} · ${bytes(d.bytes)}`}>
                    <motion.div
                      className="chart-stack"
                      initial={{ scaleY: 0 }}
                      animate={{ scaleY: 1 }}
                      transition={{ delay: Math.min(i, 60) * 0.01, duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
                      style={{ height: `${(total / maxDay) * 100}%` }}
                    >
                      {KINDS.map((kk) => Number(d[kk] ?? 0) > 0 && <div key={kk} style={{ flex: Number(d[kk]), background: KIND_COLORS[kk] }} />)}
                    </motion.div>
                  </div>
                );
              })}
            </div>
            <div className="row mono faint" style={{ fontSize: 10, justifyContent: "space-between", marginTop: 8 }}>
              <span>{stats.timeline[0]?.day}</span>
              <span>{stats.timeline[stats.timeline.length - 1]?.day}</span>
            </div>
          </div>

          <div className="stats-grid" style={{ marginTop: 16 }}>
            <div className="card card-pad rise" style={{ animationDelay: "90ms" }}>
              <span className="label">{t("stats.platforms")}</span>
              <div className="col" style={{ gap: 10, marginTop: 14 }}>
                {stats.platforms.length === 0 && <span className="faint">—</span>}
                {stats.platforms.map((p) => (
                  <div key={p.id} className="hbar">
                    <span className="hbar-name mono">{p.name.toLowerCase()}</span>
                    <div className="hbar-track"><motion.div className="hbar-fill" initial={{ width: 0 }} animate={{ width: `${(p.count / maxPlat) * 100}%` }} transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }} /></div>
                    <span className="hbar-val mono">{p.count}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="card card-pad rise" style={{ animationDelay: "110ms" }}>
              <span className="label">{t("stats.hours")}</span>
              <div className="hours-grid">
                {stats.hours.map((n, h) => (
                  <div key={h} className="hour-cell" data-tip={`${String(h).padStart(2, "0")}h · ${n}`} style={{ background: n ? `rgba(var(--accent-rgb), ${0.12 + (n / maxHour) * 0.88})` : undefined }} />
                ))}
              </div>
              <div className="row mono faint" style={{ fontSize: 10, justifyContent: "space-between", marginTop: 6 }}>
                <span>00h</span><span>06h</span><span>12h</span><span>18h</span><span>23h</span>
              </div>
              <div className="divider" />
              <span className="label">{t("stats.formats")}</span>
              <div className="row" style={{ flexWrap: "wrap", gap: 6, marginTop: 10 }}>
                {stats.formats.map((f) => <span key={f.name} className="chip mono-b">{f.name} · {f.count}</span>)}
              </div>
              <div className="label" style={{ marginTop: 16 }}>{t("stats.resolutions")}</div>
              <div className="row" style={{ flexWrap: "wrap", gap: 6, marginTop: 10 }}>
                {stats.resolutions.map((f) => <span key={f.name} className="chip mono-b">{f.name} · {f.count}</span>)}
              </div>
            </div>
            <div className="card card-pad rise" style={{ animationDelay: "130ms" }}>
              <span className="label">{t("stats.records")}</span>
              <div className="col" style={{ gap: 14, marginTop: 14 }}>
                <Record icon={<HardDrive size={14} />} label={t("stats.biggest")} value={stats.biggest ? bytes(stats.biggest.size) : "—"} sub={stats.biggest?.title} />
                <Record icon={<Clock size={14} />} label={t("stats.longest")} value={stats.longest ? eta(stats.longest.duration) : "—"} sub={stats.longest?.title} />
                <Record icon={<Activity size={14} />} label={t("stats.speed")} value={speed(stats.median_speed)} sub={t("stats.speedSub")} />
                <Record icon={<Cpu size={14} />} label={t("stats.gpu")} value={hours(up.seconds)} sub={t("stats.gpuSub")} />
                <Record icon={<Scissors size={14} />} label={t("stats.since")} value={stats.first ? new Date(stats.first * 1000).toLocaleDateString(t.lang) : "—"} sub={t("stats.total", { n: stats.total })} />
              </div>
            </div>
            <div className="card card-pad rise" style={{ animationDelay: "150ms" }}>
              <span className="label">{t("stats.creators")}</span>
              <div className="col" style={{ gap: 8, marginTop: 14 }}>
                {stats.uploaders.length === 0 && <span className="faint">—</span>}
                {stats.uploaders.map((u, i) => (
                  <div key={u.name} className="row" style={{ gap: 10 }}>
                    <span className="mono faint" style={{ width: 20 }}>{String(i + 1).padStart(2, "0")}</span>
                    <span className="ellipsis" style={{ flex: 1, fontWeight: 600 }}>{u.name}</span>
                    <span className="mono faint">{u.count}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function Kpi({ icon, label, value, sub, accent }: { icon: ReactNode; label: string; value: ReactNode; sub?: ReactNode; accent?: boolean }) {
  return (
    <div className="card kpi">
      <Ticks inset={8} />
      <div className="row label" style={{ gap: 7 }}>{icon}{label}</div>
      <div className="kpi-value" style={accent ? { color: "var(--accent-text)" } : undefined}>{value}</div>
      {sub && <div className="faint mono ellipsis" style={{ fontSize: 11 }}>{sub}</div>}
    </div>
  );
}

function Record({ icon, label, value, sub }: { icon: ReactNode; label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="row" style={{ gap: 12, alignItems: "flex-start" }}>
      <span className="faint" style={{ marginTop: 3 }}>{icon}</span>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div className="label">{label}</div>
        <div style={{ font: "700 18px/1.3 var(--font-display)" }}>{value}</div>
        {sub && <div className="faint ellipsis" style={{ fontSize: 12 }}>{sub}</div>}
      </div>
    </div>
  );
}
