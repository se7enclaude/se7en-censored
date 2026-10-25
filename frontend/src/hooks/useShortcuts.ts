import { useEffect } from "react";
import { Page, useApp } from "../store/app";

export const SHORTCUTS: { keys: string; key: string }[] = [
  { keys: "Ctrl + 1…9, 0", key: "shortcuts.nav" },
  { keys: "Ctrl + ,", key: "shortcuts.settings" },
  { keys: "Ctrl + L", key: "shortcuts.logs" },
  { keys: "Ctrl + V", key: "shortcuts.paste" },
  { keys: "Enter", key: "shortcuts.analyze" },
  { keys: "Ctrl + Enter", key: "shortcuts.download" },
  { keys: "Ctrl + O", key: "shortcuts.import" },
  { keys: "Ctrl + P", key: "shortcuts.preview" },
  { keys: "?", key: "shortcuts.help" },
  { keys: "Esc", key: "shortcuts.close" },
];

// Same order as the numbered tabs (01 Télécharger … 10 Doublons; Ctrl+0 is the tenth)
const PAGES: Page[] = ["download", "queue", "upscale", "convert", "trim", "studio", "subtitles", "player", "history", "duplicates", "stats"];

/** Global navigation shortcuts. Page-level shortcuts live in the pages. */
export function useGlobalShortcuts(openHelp: () => void) {
  const setPage = useApp((s) => s.setPage);
  const toggleSidebar = useApp((s) => s.toggleSidebar);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
      if (e.ctrlKey && !e.shiftKey && !e.altKey) {
        const n = Number(e.key);
        if (e.key >= "0" && e.key <= "9") {
          e.preventDefault();
          setPage(PAGES[n === 0 ? 9 : n - 1]);
        } else if (e.key === ",") {
          e.preventDefault();
          setPage("settings");
        } else if (e.key.toLowerCase() === "l") {
          e.preventDefault();
          setPage("logs");
        } else if (e.key.toLowerCase() === "b") {
          e.preventDefault();
          toggleSidebar();
        }
      } else if (!typing && e.key === "?") {
        e.preventDefault();
        openHelp();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setPage, toggleSidebar, openHelp]);
}

/** Page-scoped shortcut helper. */
export function useKey(combo: string, handler: (e: KeyboardEvent) => void, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    const [mod, key] = combo.includes("+") ? combo.split("+") : ["", combo];
    const onKey = (e: KeyboardEvent) => {
      if (mod === "ctrl" && !e.ctrlKey) return;
      if (mod === "" && (e.ctrlKey || e.altKey || e.metaKey)) return;
      if (e.key.toLowerCase() !== key.toLowerCase()) return;
      handler(e);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [combo, handler, enabled]);
}
