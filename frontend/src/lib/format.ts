export function bytes(n?: number | null, digits = 1): string {
  if (n === undefined || n === null || !isFinite(n)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (Math.abs(v) >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i <= 1 ? 0 : digits)} ${units[i]}`;
}

export function speed(bps?: number | null): string {
  if (!bps) return "—";
  return `${bytes(bps)}/s`;
}

export function duration(sec?: number | null): string {
  if (sec === undefined || sec === null || !isFinite(sec)) return "—";
  const s = Math.max(0, Math.round(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  const pad = (x: number) => String(x).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(r)}` : `${pad(m)}:${pad(r)}`;
}

export function eta(sec?: number | null): string {
  if (sec === undefined || sec === null || !isFinite(sec) || sec < 0) return "—";
  const s = Math.round(sec);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, "0")}m`;
}

export function num(n?: number | null): string {
  if (n === undefined || n === null) return "—";
  return new Intl.NumberFormat(undefined, { notation: n > 99999 ? "compact" : "standard" }).format(n);
}

export function dateTime(ts: number, locale?: string): string {
  return new Date(ts * 1000).toLocaleString(locale, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function relative(ts: number, locale?: string): string {
  const diff = ts * 1000 - Date.now();
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  const abs = Math.abs(diff);
  if (abs < 60_000) return rtf.format(Math.round(diff / 1000), "second");
  if (abs < 3_600_000) return rtf.format(Math.round(diff / 60_000), "minute");
  if (abs < 86_400_000) return rtf.format(Math.round(diff / 3_600_000), "hour");
  if (abs < 7 * 86_400_000) return rtf.format(Math.round(diff / 86_400_000), "day");
  return new Date(ts * 1000).toLocaleDateString(locale, { day: "2-digit", month: "short", year: "numeric" });
}

export function basename(p?: string | null): string {
  if (!p) return "";
  return p.split(/[\\/]/).pop() || p;
}

export function dirname(p?: string | null): string {
  if (!p) return "";
  const parts = p.split(/[\\/]/);
  parts.pop();
  return parts.join("\\");
}

const PLATFORM_COLORS: Record<string, string> = {
  youtube: "#ff4d4f",
  tiktok: "#25f4ee",
  twitter: "#e7e9ea",
  instagram: "#e1306c",
  facebook: "#1877f2",
  reddit: "#ff5700",
  vimeo: "#1ab7ea",
  twitch: "#a970ff",
  dailymotion: "#0a9cff",
  soundcloud: "#ff7700",
  bilibili: "#00a1d6",
  direct: "#94a3b8",
  upscale: "#8b6cff",
};

export function platformColor(key?: string | null): string {
  if (!key) return "#94a3b8";
  if (PLATFORM_COLORS[key]) return PLATFORM_COLORS[key];
  let h = 0;
  for (const c of key) h = (h * 31 + c.charCodeAt(0)) % 360;
  return `hsl(${h} 70% 65%)`;
}

export function resolutionLabel(w?: number | null, h?: number | null): string {
  if (!w || !h) return "—";
  return `${w}×${h}`;
}

export const codecLabel: Record<string, string> = {
  h264: "H.264",
  h265: "H.265 / HEVC",
  av1: "AV1",
  vp9: "VP9",
  vp8: "VP8",
  aac: "AAC",
  opus: "Opus",
  vorbis: "Vorbis",
  mp3: "MP3",
  flac: "FLAC",
  other: "Other",
  embedded: "Embedded",
};
