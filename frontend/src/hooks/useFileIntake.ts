import { useCallback, useEffect, useRef } from "react";
import { api } from "../api/client";
import type { LocalFile } from "../api/types";
import { useT } from "../i18n";
import { Page, useApp } from "../store/app";
import { useKey } from "./useShortcuts";

const PATH_RE = /"?([A-Za-z]:\\[^"\r\n<>|*?]+\.\w{2,5})"?/g;

/**
 * Local files for a tool page: native file dialog (desktop) or upload (browser mode),
 * files dropped on the window, pasted paths ("Copy as path") and Ctrl+O. Files are
 * probed by the backend (kind, duration, resolution, codecs) before reaching the page.
 */
export function useFileIntake({
  page,
  kind,
  multiple = true,
  onFiles,
}: {
  page: Page;
  kind: "media" | "video" | "audio" | "image" | "av" | "any" | "playable";
  multiple?: boolean;
  onFiles: (files: LocalFile[]) => void;
}) {
  const t = useT();
  const shell = useApp((s) => s.info?.shell);
  const toast = useApp((s) => s.toast);
  const pendingFiles = useApp((s) => s.pendingFiles);
  const consumeFiles = useApp((s) => s.consumeFiles);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const cb = useRef(onFiles);
  cb.current = onFiles;

  const addPaths = useCallback(
    async (paths: string[]) => {
      if (!paths.length) return;
      const r = await api.post<{ files: LocalFile[] }>("/api/files/probe", { paths: multiple ? paths : paths.slice(0, 1) });
      const ok = r.files.filter((f) => f.ok);
      const bad = r.files.filter((f) => !f.ok);
      if (bad.length) toast({ kind: "warning", title: t("files.skipped", { n: bad.length }), body: bad.map((b) => `${b.name}: ${b.error?.message}`).join("\n") });
      if (ok.length) cb.current(ok);
    },
    [multiple, toast, t],
  );

  useEffect(() => {
    if (!pendingFiles) return;
    const p = consumeFiles(page);
    if (p) addPaths(p);
  }, [pendingFiles]); // eslint-disable-line react-hooks/exhaustive-deps

  const pick = useCallback(async () => {
    if (!shell) {
      inputRef.current?.click();
      return;
    }
    try {
      const r = await api.post<{ paths: string[] }>("/api/system/pick-files", { kind, multiple });
      addPaths(r.paths);
    } catch (e: any) {
      toast({ kind: "error", title: t("files.pickFailed"), body: e?.message });
    }
  }, [shell, kind, multiple, addPaths, toast, t]);

  const upload = useCallback(
    async (list: FileList | null) => {
      if (!list?.length) return;
      const form = new FormData();
      Array.from(list).forEach((f) => form.append("files", f));
      const r = await api.upload<{ paths: string[] }>("/api/upload", form);
      addPaths(r.paths);
    },
    [addPaths],
  );

  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const target = e.target as HTMLElement;
      if (target?.tagName === "INPUT" || target?.tagName === "TEXTAREA") return;
      const text = e.clipboardData?.getData("text") ?? "";
      const paths = Array.from(text.matchAll(PATH_RE), (m) => m[1].trim());
      if (paths.length) {
        e.preventDefault();
        addPaths(paths);
      }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [addPaths]);

  useKey("ctrl+o", (e) => {
    e.preventDefault();
    pick();
  });

  const accept = { media: "image/*,video/*", video: "video/*", audio: "audio/*", image: "image/*", av: "video/*,audio/*", any: "", playable: "image/*,video/*,audio/*" }[kind];
  return { pick, addPaths, inputProps: { ref: inputRef, type: "file" as const, multiple, accept, hidden: true, onChange: (e: React.ChangeEvent<HTMLInputElement>) => upload(e.target.files) } };
}
