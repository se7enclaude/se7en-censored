import { AnimatePresence, motion } from "motion/react";
import { Download, PackageCheck, X } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import { useT } from "../i18n";
import { bytes } from "../lib/format";
import { useApp } from "../store/app";
import { Button, Progress } from "./ui";

/** First-run helper: offers to install required components that are missing. */
export function SetupBanner() {
  const t = useT();
  const comps = useApp((s) => s.components);
  const progress = useApp((s) => s.componentProgress);
  const [hidden, setHidden] = useState(() => {
    try {
      return sessionStorage.getItem("medialab.setupHidden") === "1";
    } catch {
      return false;
    }
  });
  const missing = comps.filter((c) => !c.optional && !c.installed);
  const busy = missing.find((c) => ["starting", "downloading", "extracting"].includes(progress[c.id]?.state ?? ""));
  const installAll = async () => {
    // one at a time: the backend installs components sequentially
    for (const c of missing) {
      await api.post(`/api/components/${c.id}/install`);
      await new Promise<void>((resolve) => {
        const iv = window.setInterval(() => {
          const st = useApp.getState().componentProgress[c.id]?.state;
          if (st === "installed" || st === "failed") {
            window.clearInterval(iv);
            resolve();
          }
        }, 400);
      });
    }
  };
  const p = busy ? progress[busy.id] : undefined;
  return (
    <AnimatePresence>
      {!hidden && missing.length > 0 && (
        <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }} className="card card-pad row setup-banner" style={{ gap: 14 }}>
          <PackageCheck size={20} style={{ color: "var(--accent)", flexShrink: 0 }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 650 }}>{t("setup.title")}</div>
            <div className="muted" style={{ fontSize: 12.5 }}>
              {busy
                ? `${busy.name} — ${p?.state === "extracting" ? t("components.extracting") : `${bytes(p?.downloaded)} / ${bytes(p?.total)}`}`
                : t("setup.body", { list: missing.map((c) => c.name).join(", ") })}
            </div>
            {busy && (
              <div style={{ marginTop: 8, maxWidth: 420 }}>
                <Progress value={p?.total ? ((p.downloaded ?? 0) / p.total) * 100 : null} state="active" />
              </div>
            )}
          </div>
          <Button variant="primary" icon={<Download size={15} />} loading={!!busy} onClick={installAll}>
            {t("setup.installAll")}
          </Button>
          <Button
            variant="ghost"
            iconOnly
            icon={<X size={15} />}
            onClick={() => {
              setHidden(true);
              try {
                sessionStorage.setItem("medialab.setupHidden", "1");
              } catch {
                /* ignore */
              }
            }}
          />
        </motion.div>
      )}
    </AnimatePresence>
  );
}
