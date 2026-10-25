import { AnimatePresence, motion } from "motion/react";
import { ArrowUpCircle, RotateCw } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { UpdateState } from "../api/types";
import { useT } from "../i18n";
import { useApp } from "../store/app";
import { Button, Modal } from "./ui";

/**
 * Small notice pinned at the bottom-left while an update is waiting to be installed. It cannot be
 * dismissed: it stays until the update is installed (on "Restart now", or silently when se7en closes).
 */
export function UpdateBanner() {
  const t = useT();
  const u = useApp((s) => s.update);
  const auto = useApp((s) => s.settings?.general.auto_update !== false);
  const jobs = useApp((s) => s.jobs);
  const [notes, setNotes] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [installing, setInstalling] = useState(false);
  const show = !!u && ["available", "downloading", "ready", "installing"].includes(u.state);
  const running = Object.values(jobs).filter((j) => ["downloading", "processing", "analyzing"].includes(j.status)).length;
  const pct = u?.total ? Math.min(100, Math.round(((u.downloaded ?? 0) / u.total) * 100)) : null;

  const install = async () => {
    setInstalling(true);
    try {
      await api.post<UpdateState>("/api/update/install");
    } catch {
      setInstalling(false);
    }
  };

  const text = !u
    ? ""
    : u.state === "installing"
      ? t("update.installing")
      : u.state === "available" && !auto
        ? t("update.available", { v: u.latest ?? "" })
        : t("update.pending", { v: u.latest ?? "" });

  return (
    <>
      <AnimatePresence>
        {show && u && (
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 12 }}
            className="update-notice"
            role="status"
          >
            <ArrowUpCircle size={15} style={{ color: "var(--accent)", flexShrink: 0 }} />
            <span className="update-notice-text">
              {text}
              {u.state === "downloading" && pct !== null && <span className="faint tabular"> · {pct}%</span>}
              {u.error && u.state !== "installing" && <span className="faint"> · {u.error.message}</span>}
            </span>
            {u.notes && u.state !== "installing" && (
              <Button size="sm" variant="ghost" onClick={() => setNotes(true)}>
                {t("update.notes")}
              </Button>
            )}
            {u.state === "available" && (!auto || u.error) && (
              <Button size="sm" onClick={() => api.post("/api/update/download")}>
                {t("update.download")}
              </Button>
            )}
            {u.state === "ready" && (
              <Button size="sm" variant="primary" icon={<RotateCw size={13} />} loading={installing} onClick={() => (running ? setConfirm(true) : install())}>
                {t("update.restartNow")}
              </Button>
            )}
            {u.state === "downloading" && (
              <span className="update-notice-bar" style={{ width: `${pct ?? 0}%` }} />
            )}
          </motion.div>
        )}
      </AnimatePresence>
      <Modal open={notes} onClose={() => setNotes(false)} eyebrow={`se7en ${u?.latest ?? ""}`} title={t("update.notesTitle")} width={620}>
        <pre className="notes-pre selectable">{u?.notes}</pre>
      </Modal>
      <Modal
        open={confirm}
        onClose={() => setConfirm(false)}
        title={t("update.confirmTitle")}
        actions={
          <>
            <Button onClick={() => setConfirm(false)}>{t("update.later")}</Button>
            <Button variant="primary" loading={installing} onClick={install}>
              {t("update.restart")}
            </Button>
          </>
        }
      >
        <p>{t("update.confirmBody", { n: running })}</p>
      </Modal>
    </>
  );
}
