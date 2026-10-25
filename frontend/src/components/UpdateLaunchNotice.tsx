import { ArrowUpCircle, PartyPopper, RotateCw } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { UpdateState } from "../api/types";
import { useT } from "../i18n";
import { play } from "../lib/sounds";
import { useApp } from "../store/app";
import { Button, Modal } from "./ui";

// versions already announced in this session (the window is not shown again when the user chose "Later"),
// and whether "se7en has been updated" was closed (the server may still report it for a moment)
const announced = new Set<string>();
let updatedSeen = false;

/**
 * Windows shown once at launch (after the intro):
 *  - "se7en has been updated to X" with what changed, the first time a new version runs;
 *  - "A new version is available" as soon as the launch check finds one. The small notice at the bottom
 *    stays afterwards (UpdateBanner) until the update is installed.
 */
export function UpdateLaunchNotice({ blocked }: { blocked: boolean }) {
  const t = useT();
  const u = useApp((s) => s.update);
  const auto = useApp((s) => s.settings?.general.auto_update !== false);
  const running = useApp((s) => Object.values(s.jobs).filter((j) => ["downloading", "processing", "analyzing"].includes(j.status)).length);
  const [open, setOpen] = useState<"updated" | "available" | null>(null);
  const [installing, setInstalling] = useState(false);

  useEffect(() => {
    if (blocked || open || !u) return;
    if (u.updated_from && !updatedSeen) {
      setOpen("updated");
      play("success");
    } else if (u.latest && ["available", "downloading", "ready"].includes(u.state) && !announced.has(u.latest)) {
      announced.add(u.latest);
      setOpen("available");
      play("update");
    }
  }, [u, blocked, open]);

  const closeUpdated = () => {
    updatedSeen = true;
    setOpen(null);
    api.post<UpdateState>("/api/update/seen").then((s) => useApp.setState({ update: s })).catch(() => undefined);
  };
  const install = async () => {
    setInstalling(true);
    try {
      await api.post<UpdateState>("/api/update/install");
    } catch {
      setInstalling(false);
    }
  };

  if (open === "updated" && u) {
    const changes = u.changes?.[t.lang] ?? u.changes?.en ?? [];
    return (
      <Modal
        open
        onClose={closeUpdated}
        eyebrow={`se7en ${u.current}`}
        title={t("update.doneTitle", { v: u.current })}
        width={600}
        actions={<Button variant="primary" onClick={closeUpdated}>{t("update.doneOk")}</Button>}
      >
        <div className="row" style={{ gap: 10, alignItems: "flex-start" }}>
          <PartyPopper size={18} style={{ color: "var(--accent)", flexShrink: 0, marginTop: 2 }} />
          <p className="muted" style={{ margin: 0 }}>{t(changes.length ? "update.doneBody" : "update.doneBodyShort")}</p>
        </div>
        {changes.length > 0 && (
          <ul className="update-changes">
            {changes.map((c) => <li key={c}>{c}</li>)}
          </ul>
        )}
      </Modal>
    );
  }

  if (open === "available" && u) {
    const pct = u.total ? Math.min(100, Math.round(((u.downloaded ?? 0) / u.total) * 100)) : null;
    const ready = u.state === "ready";
    return (
      <Modal
        open
        onClose={() => setOpen(null)}
        eyebrow={t("update.launchEyebrow")}
        title={t("update.launchTitle", { v: u.latest ?? "" })}
        width={600}
        actions={
          <>
            <Button onClick={() => setOpen(null)}>{t("update.later")}</Button>
            {ready ? (
              <Button variant="primary" icon={<RotateCw size={13} />} loading={installing} onClick={install}>
                {t("update.restartNow")}
              </Button>
            ) : u.state === "downloading" ? (
              <Button variant="primary" disabled loading>
                {t("update.downloading", { p: pct ?? 0 })}
              </Button>
            ) : (
              <Button variant="primary" onClick={() => api.post("/api/update/download")}>
                {t("update.download")}
              </Button>
            )}
          </>
        }
      >
        <div className="row" style={{ gap: 10, alignItems: "flex-start" }}>
          <ArrowUpCircle size={18} style={{ color: "var(--accent)", flexShrink: 0, marginTop: 2 }} />
          <p className="muted" style={{ margin: 0 }}>
            {t("update.launchCurrent", { v: u.current })} {auto ? t("update.launchAuto") : t("update.launchManual")}
            {ready && running > 0 && <> {t("update.confirmBody", { n: running })}</>}
          </p>
        </div>
        {u.notes && <pre className="notes-pre selectable update-launch-notes">{u.notes}</pre>}
      </Modal>
    );
  }
  return null;
}
