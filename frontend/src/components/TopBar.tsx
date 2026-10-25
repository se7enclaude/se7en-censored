import clsx from "clsx";
import { ShieldCheck, History, ListOrdered, ScrollText, Settings as SettingsIcon } from "lucide-react";
import { ReactNode, useMemo } from "react";
import { useT } from "../i18n";
import { Page, useApp } from "../store/app";
import { Button } from "./ui";

export function Wordmark({ size = 26, onClick }: { size?: number; onClick?: () => void }) {
  return (
    <span className="wordmark" style={{ fontSize: size }} onClick={onClick} role={onClick ? "button" : undefined} title={onClick ? "se7en" : undefined}>
      se<span className="seven">7</span>en<span className="wordmark-sub"> censored</span>
    </span>
  );
}

export const TABS: { id: Page; n: string; key: string; icon: ReactNode }[] = [
  { id: "censor", n: "01", key: "nav.censor", icon: <ShieldCheck size={14} /> },
  { id: "queue", n: "02", key: "nav.queue", icon: <ListOrdered size={14} /> },
  { id: "history", n: "03", key: "nav.history", icon: <History size={14} /> },
];

/** 64px top bar: wordmark · numbered tabs · GPU status · logs · settings (design system "Chrome"). */
export function TopBar({ onLogo }: { onLogo: () => void }) {
  const t = useT();
  const page = useApp((s) => s.page);
  const setPage = useApp((s) => s.setPage);
  const jobs = useApp((s) => s.jobs);
  const hw = useApp((s) => s.hardware);
  const caps = useApp((s) => s.capabilities);
  const connected = useApp((s) => s.connected);

  const active = useMemo(() => Object.values(jobs).filter((j) => ["waiting", "analyzing", "downloading", "processing"].includes(j.status)).length, [jobs]);
  const gpu = hw?.gpus?.[0];
  const aiReady = caps?.backends.some((b) => b.id !== "classic" && b.available);
  const status = !connected
    ? { cls: "off", text: t("app.reconnecting") }
    : gpu && aiReady
      ? { cls: "ok", text: t("top.gpuReady") }
      : gpu
        ? { cls: "warn", text: t("top.gpuNoAi") }
        : { cls: "warn", text: t("top.cpuOnly") };
  const tip = gpu
    ? `${gpu.name}\n${gpu.utilization ?? "—"}% · ${gpu.temperature ?? "—"}°C · VRAM ${gpu.vram_used_mb != null ? (gpu.vram_used_mb / 1024).toFixed(1) : "—"} / ${((gpu.vram_total_mb ?? 0) / 1024).toFixed(0)} GB`
    : undefined;

  // Glass pills (Studio Glass): brand pill + navigation pill on the left, status + round buttons on the right.
  // On the home screen these two clusters sit in the notches of the red hero, which measures them.
  return (
    <header className="topbar">
      <div className="topbar-l">
        <button className="brand-pill glass" onClick={onLogo} title="se7en">
          <Wordmark size={22} />
        </button>
        <nav className="navpills glass">
          {TABS.map((tab) => {
            const on = page === tab.id;
            return (
              <button key={tab.id} className={clsx("navpill", on && "on")} onClick={() => setPage(tab.id)} data-tip={on ? undefined : t(tab.key)} aria-current={on ? "page" : undefined}>
                {tab.icon}
                {on && <span className="navpill-label">{t(tab.key)}</span>}
                {tab.id === "queue" && active > 0 && <span className="tab-badge">{active}</span>}
              </button>
            );
          })}
        </nav>
      </div>
      <div className="spacer" />
      <div className="topbar-r">
        <span className="status-pill glass" data-tip={tip}>
          <span className={`status-dot ${status.cls}`} />
          {status.text}
          {gpu && connected && <span className="topbar-extra">· {gpu.temperature ?? "—"}°C · {gpu.utilization ?? 0}%</span>}
        </span>
        <Button iconOnly size="sm" className="disc" icon={<ScrollText size={16} />} tip={t("nav.logs")} active={page === "logs"} onClick={() => setPage("logs")} />
        <Button iconOnly size="sm" className="disc" icon={<SettingsIcon size={16} />} tip={t("nav.settings")} active={page === "settings"} onClick={() => setPage("settings")} />
      </div>
    </header>
  );
}
