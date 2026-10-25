import { AnimatePresence, motion } from "motion/react";
import { Link2, UploadCloud } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useT } from "../i18n";
import { play } from "../lib/sounds";
import { useApp } from "../store/app";

const URL_RE = /https?:\/\/[^\s<>"'`]+/gi;

/**
 * Global drag & drop with context detection:
 *  - links (from a browser)  → Download page, analyzed automatically
 *  - images / videos         → Upscale page
 * In the desktop shell, real file paths arrive through the backend
 * (`files_dropped` event); in browser mode files are uploaded.
 */
export function DropOverlay() {
  const t = useT();
  const [kind, setKind] = useState<null | "files" | "url">(null);
  const depth = useRef(0);
  const shell = useApp((s) => s.info?.shell);
  const sendUrls = useApp((s) => s.sendUrls);
  const sendFiles = useApp((s) => s.sendFiles);
  const toast = useApp((s) => s.toast);

  useEffect(() => {
    const types = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []);
    const onEnter = (e: DragEvent) => {
      const ts = types(e);
      if (!ts.includes("Files") && !ts.includes("text/uri-list") && !ts.includes("text/plain")) return;
      e.preventDefault();
      depth.current++;
      setKind(ts.includes("Files") ? "files" : "url");
    };
    const onOver = (e: DragEvent) => {
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
    };
    const onLeave = () => {
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setKind(null);
    };
    const onDrop = async (e: DragEvent) => {
      e.preventDefault();
      depth.current = 0;
      setKind(null);
      const dt = e.dataTransfer;
      if (!dt) return;
      if (dt.files.length > 0) {
        if (shell) return; // the desktop shell forwards real paths via the backend
        const form = new FormData();
        Array.from(dt.files).forEach((f) => form.append("files", f));
        try {
          const r = await api.upload<{ paths: string[] }>("/api/upload", form);
          sendFiles(r.paths);
        } catch (err: any) {
          toast({ kind: "error", title: t("drop.uploadFailed"), body: err.message });
        }
        return;
      }
      const text = dt.getData("text/uri-list") || dt.getData("text/plain");
      const urls = Array.from(new Set(text.match(URL_RE) ?? []));
      if (urls.length) {
        play("drop");
        sendUrls(urls);
      }
    };
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragover", onOver);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("drop", onDrop);
    };
  }, [shell, sendFiles, sendUrls, toast, t]);

  return (
    <AnimatePresence>
      {kind && (
        <motion.div className="drop-overlay" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
          <motion.div
            className="drop-overlay-card"
            initial={{ scale: 0.92, y: 10 }}
            animate={{ scale: 1, y: 0 }}
            transition={{ type: "spring", stiffness: 400, damping: 28 }}
          >
            <div className="drop-overlay-icon">{kind === "url" ? <Link2 size={32} /> : <UploadCloud size={32} />}</div>
            <h2 style={{ fontSize: 20 }}>{kind === "url" ? t("drop.urlTitle") : t("drop.filesTitle")}</h2>
            <p className="muted" style={{ marginTop: 6 }}>
              {kind === "url" ? t("drop.urlBody") : t("drop.filesBody")}
            </p>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
