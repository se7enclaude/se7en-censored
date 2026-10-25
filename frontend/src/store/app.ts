import { create } from "zustand";
import { api } from "../api/client";
import { events } from "../api/ws";
import type { AppEvent, Capabilities, Component, Hardware, Job, Settings, TaskState, UpdateState } from "../api/types";
import { configureSounds, play } from "../lib/sounds";

export type Page =
  | "censor"
  | "download"
  | "queue"
  | "upscale"
  | "convert"
  | "trim"
  | "studio"
  | "subtitles"
  | "player"
  | "history"
  | "duplicates"
  | "stats"
  | "settings"
  | "logs";

/** Pages that accept local files dropped on the window. */
const FILE_PAGES: Page[] = ["censor", "upscale", "convert", "trim", "studio", "subtitles", "player"];

export interface Toast {
  id: number;
  kind: "success" | "error" | "info" | "warning";
  title: string;
  body?: string;
  action?: { label: string; run: () => void };
  timeout?: number;
}

export interface ComponentProgress {
  state: string;
  downloaded?: number;
  total?: number;
  error?: { message: string; hint?: string };
}

interface SystemInfo {
  app: string;
  version: string;
  portable: boolean;
  frozen: boolean;
  shell: boolean;
  data_dir: string;
  logs_dir: string;
  plugins: { name: string; ok: boolean; error?: string }[];
  providers: { id: string; name: string }[];
}

interface AppState {
  ready: boolean;
  bootError: string | null;
  connected: boolean;
  page: Page;
  sidebarCollapsed: boolean;
  info: SystemInfo | null;
  settings: Settings | null;
  jobs: Record<string, Job>;
  hardware: Hardware | null;
  capabilities: Capabilities | null;
  components: Component[];
  componentProgress: Record<string, ComponentProgress>;
  toasts: Toast[];
  pendingUrls: string[] | null;
  pendingFiles: string[] | null;
  pendingFilesPage: Page | null;
  playerQueue: { paths: string[]; index: number; nonce: number } | null;
  update: UpdateState | null;
  tasks: Record<string, TaskState>;
  historyVersion: number;
  settingsSection: string | null;

  setPage: (p: Page) => void;
  goSettings: (section: string) => void;
  toggleSidebar: () => void;
  saveSettings: (patch: Partial<{ [K in keyof Settings]: Partial<Settings[K]> }>) => Promise<void>;
  loadCapabilities: () => Promise<void>;
  loadComponents: () => Promise<void>;
  toast: (t: Omit<Toast, "id">) => void;
  dismiss: (id: number) => void;
  sendUrls: (urls: string[]) => void;
  sendFiles: (paths: string[], page?: Page) => void;
  openInPlayer: (paths: string[], index?: number) => void;
  consumeUrls: () => string[] | null;
  consumeFiles: (page: Page) => string[] | null;
  upsertJob: (j: Job) => void;
}

let toastSeq = 1;
const LS_PAGE = "se7encensored.page";
const LS_SIDEBAR = "medialab.sidebar";

function ls(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function lsSet(key: string, v: string): void {
  try {
    localStorage.setItem(key, v);
  } catch {
    /* ignore */
  }
}

export const useApp = create<AppState>((set, get) => ({
  ready: false,
  bootError: null,
  connected: false,
  page: "censor",
  sidebarCollapsed: ls(LS_SIDEBAR) === "1",
  info: null,
  settings: null,
  jobs: {},
  hardware: null,
  capabilities: null,
  components: [],
  componentProgress: {},
  toasts: [],
  pendingUrls: null,
  pendingFiles: null,
  pendingFilesPage: null,
  playerQueue: null,
  update: null,
  tasks: {},
  historyVersion: 0,
  settingsSection: null,

  setPage: (p) => {
    lsSet(LS_PAGE, p);
    if (p !== get().page) play("tick");
    set({ page: p });
  },
  goSettings: (section) => {
    lsSet(LS_PAGE, "settings");
    set({ page: "settings", settingsSection: section });
  },
  toggleSidebar: () => {
    const v = !get().sidebarCollapsed;
    lsSet(LS_SIDEBAR, v ? "1" : "0");
    set({ sidebarCollapsed: v });
  },
  saveSettings: async (patch) => {
    const s = await api.patch<Settings>("/api/settings", patch);
    set({ settings: s });
  },
  loadCapabilities: async () => {
    const c = await api.get<Capabilities>("/api/upscale/capabilities");
    set({ capabilities: c });
  },
  loadComponents: async () => {
    const c = await api.get<Component[]>("/api/components");
    set({ components: c });
  },
  toast: (t) => {
    const id = toastSeq++;
    set({ toasts: [...get().toasts, { ...t, id }].slice(-5) });
    const timeout = t.timeout ?? (t.kind === "error" ? 9000 : 5000);
    if (timeout > 0) window.setTimeout(() => get().dismiss(id), timeout);
  },
  dismiss: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
  sendUrls: (urls) => {
    set({ pendingUrls: urls });
    get().setPage("censor");
  },
  sendFiles: (paths, page) => {
    play("drop");
    // Dropped files go to the tool on screen; otherwise audio -> player, images/videos -> AI.
    const cur = get().page;
    const target: Page = page ?? (FILE_PAGES.includes(cur) ? cur : "censor");
    if (target === "player") {
      get().openInPlayer(paths, 0);
      return;
    }
    set({ pendingFiles: paths, pendingFilesPage: target });
    get().setPage(target);
  },
  openInPlayer: (paths, index = 0) => {
    set({ playerQueue: { paths, index, nonce: Date.now() } });
    get().setPage("player");
  },
  consumeUrls: () => {
    const u = get().pendingUrls;
    if (u) set({ pendingUrls: null });
    return u;
  },
  consumeFiles: (page) => {
    const f = get().pendingFiles;
    if (!f || get().pendingFilesPage !== page) return null;
    set({ pendingFiles: null, pendingFilesPage: null });
    return f;
  },
  upsertJob: (j) => set({ jobs: { ...get().jobs, [j.id]: j } }),
}));

// ---------------------------------------------------------------- event wiring
type Translate = (key: string, vars?: Record<string, string | number>) => string;
let translate: Translate = (k) => k;
export function setTranslator(t: Translate): void {
  translate = t;
}

function onEvent(ev: AppEvent): void {
  const s = useApp.getState();
  switch (ev.type) {
    case "job":
      s.upsertJob(ev.data as Job);
      break;
    case "job_progress": {
      const cur = s.jobs[ev.data.id];
      if (cur) {
        useApp.setState({
          jobs: {
            ...s.jobs,
            [cur.id]: { ...cur, status: ev.data.status || cur.status, progress: ev.data.progress },
          },
        });
      }
      break;
    }
    case "job_removed": {
      const next = { ...s.jobs };
      delete next[ev.data.id];
      useApp.setState({ jobs: next });
      break;
    }
    case "queue_reordered":
      api.get<{ jobs: Job[] }>("/api/jobs").then((r) => {
        useApp.setState({ jobs: Object.fromEntries(r.jobs.map((j) => [j.id, j])) });
      });
      break;
    case "hardware":
      useApp.setState({ hardware: ev.data });
      break;
    case "settings":
      useApp.setState({ settings: ev.data });
      applySoundSettings(ev.data);
      break;
    case "update": {
      const prev = s.update?.state;
      useApp.setState({ update: ev.data });
      if (ev.data.state === "ready" && prev !== "ready") play("update");
      break;
    }
    case "task":
      useApp.setState({ tasks: { ...s.tasks, [ev.data.id]: { ...s.tasks[ev.data.id], ...ev.data } } });
      break;
    case "history_changed":
      useApp.setState({ historyVersion: s.historyVersion + 1 });
      break;
    case "component": {
      const d = ev.data as { id: string } & ComponentProgress;
      useApp.setState({ componentProgress: { ...s.componentProgress, [d.id]: d } });
      if (d.state === "installed" || d.state === "restart_required") {
        s.loadComponents();
        s.loadCapabilities().catch(() => undefined);
        s.toast({ kind: "success", title: translate("components.installed", { name: d.id }) });
      }
      if (d.state === "failed") {
        s.toast({ kind: "error", title: translate("components.failed"), body: d.error?.message });
      }
      break;
    }
    case "files_dropped":
      // "Open with se7en" -> play media; a drop on the window -> the tool on screen
      if (ev.data?.paths?.length) s.sendFiles(ev.data.paths, ev.data.open ? "player" : undefined);
      break;
    case "notify": {
      const st = s.settings;
      if (st && !st.general.notifications) break;
      if (ev.data.kind === "success") {
        const doneKey: Record<string, string> = { censor: ev.data.file_path ? "notify.censorDone" : "notify.censorAnalyzed", upscale: "notify.upscaleDone", convert: "notify.convertDone", trim: "notify.trimDone" };
        s.toast({
          kind: "success",
          title: translate(doneKey[ev.data.job_kind] ?? "notify.downloadDone"),
          body: ev.data.title,
          action: ev.data.file_path
            ? {
                label: translate("common.showInFolder"),
                run: () => api.post("/api/system/open", { path: ev.data.file_path, reveal: true }),
              }
            : undefined,
        });
      } else {
        s.toast({
          kind: "error",
          title: `${translate("notify.failed")} — ${ev.data.title ?? ""}`,
          body: ev.data.error?.message,
          action: { label: translate("nav.queue"), run: () => s.setPage("queue") },
        });
      }
      play(ev.data.kind === "success" ? "success" : "error");
      break;
    }
  }
}

function applySoundSettings(st: Settings): void {
  const g = st.general;
  configureSounds({ volume: (g.sound_volume ?? 60) / 100, events: g.notification_sound !== false, ui: g.ui_sounds !== false, intro: g.startup_sound !== false });
}

export async function bootstrap(): Promise<void> {
  events.on(onEvent);
  events.onStatus((c) => {
    const was = useApp.getState().connected;
    useApp.setState({ connected: c });
    if (c && !was && useApp.getState().ready) {
      // resync after a reconnect
      api.get<{ jobs: Job[] }>("/api/jobs").then((r) =>
        useApp.setState({ jobs: Object.fromEntries(r.jobs.map((j) => [j.id, j])) }),
      );
    }
  });
  events.connect();
  try {
    const [info, settings, jobs, hardware] = await Promise.all([
      api.get<SystemInfo>("/api/system/info"),
      api.get<Settings>("/api/settings"),
      api.get<{ jobs: Job[] }>("/api/jobs"),
      api.get<Hardware>("/api/system/hardware"),
    ]);
    const start = settings.general.startup_page;
    const last = ls(LS_PAGE) as Page | null;
    const [hashPage, hashQuery] = window.location.hash.replace(/^#\/?/, "").split("?");
    const fromHash = hashPage as Page;
    const toOpen = new URLSearchParams(hashQuery ?? "").getAll("open");
    const known: Page[] = ["censor", "queue", "history", "settings", "logs"];
    const page: Page = known.includes(fromHash) ? fromHash : start === "last" && last && known.includes(last) ? last : "censor";
    applySoundSettings(settings);
    useApp.setState({
      info,
      settings,
      hardware,
      jobs: Object.fromEntries(jobs.jobs.map((j) => [j.id, j])),
      page,
      ready: true,
    });
    api.get<UpdateState>("/api/update").then((u) => useApp.setState({ update: u })).catch(() => undefined);
    if (toOpen.length) window.setTimeout(() => useApp.getState().sendFiles(toOpen, known.includes(fromHash) ? fromHash : undefined), 300);
    useApp.getState().loadComponents().catch(() => undefined);
    useApp.getState().loadCapabilities().catch(() => undefined);
  } catch (e: any) {
    useApp.setState({ bootError: e?.message || String(e) });
  }
}
