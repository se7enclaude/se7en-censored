import type { SubCue, SubStyle } from "../api/types";

/** 00:01.240 (or 1:02:03.456 past an hour) */
export function fmtTime(t: number, withMs = true): string {
  const ms = Math.max(0, Math.round(t * 1000));
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor(ms / 60_000) % 60;
  const s = Math.floor(ms / 1000) % 60;
  const f = ms % 1000;
  const core = `${h ? `${h}:${String(m).padStart(2, "0")}` : String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return withMs ? `${core}.${String(f).padStart(3, "0")}` : core;
}

/** "1:02.5", "62.5", "00:01:02,500" -> seconds (null when invalid) */
export function parseTime(v: string): number | null {
  const s = v.trim().replace(",", ".");
  if (!s) return null;
  const parts = s.split(":").map((x) => x.trim());
  if (parts.some((p) => p === "" || isNaN(Number(p)))) return null;
  let t = 0;
  for (const p of parts) t = t * 60 + Number(p);
  return t >= 0 ? t : null;
}

export const flat = (text: string) => text.replace(/\s*\n\s*/g, " ").trim();

export function visibleLength(text: string): number {
  return [...flat(text).normalize("NFC")].filter((c) => !/\p{M}/u.test(c)).length;
}

export function cps(c: SubCue): number {
  const d = c.end - c.start;
  return d > 0 ? visibleLength(c.text) / d : 99;
}

const NO_SPACES = new Set(["ja", "zh", "yue", "th", "lo", "km", "my", "bo"]);

export function units(text: string, lang: string): string[] {
  const f = flat(text);
  return NO_SPACES.has(lang) ? [...f.replace(/ /g, "")] : f.split(" ").filter(Boolean);
}

const norm = (w: string) => w.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");

/** Displayed words with times: same algorithm as the burn-in (formats.word_times) so preview = export. */
export function wordTimes(c: SubCue, lang: string, text?: string): { w: string; a: number; b: number }[] {
  const us = units(text ?? c.text, lang);
  if (!us.length) return [];
  const src = (c.words ?? []).filter((w) => String(w.text || "").trim());
  const start = c.start;
  const end = c.end;
  const voiceEnd = src.length ? Math.min(end, Math.max(...src.map((w) => w.end))) : end;
  const times: ([number, number] | null)[] = us.map(() => null);
  if (src.length && !NO_SPACES.has(lang)) {
    // longest common subsequence on normalized words (difflib-like result for subtitle-sized inputs)
    const a = us.map(norm);
    const b = src.map((w) => norm(w.text));
    const n = a.length;
    const m = b.length;
    const L: number[][] = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--) L[i][j] = a[i] === b[j] && a[i] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (a[i] === b[j] && a[i]) {
        times[i] = [Math.max(start, src[j].start), Math.min(end, src[j].end)];
        i++;
        j++;
      } else if (L[i + 1][j] >= L[i][j + 1]) i++;
      else j++;
    }
  }
  let i = 0;
  while (i < us.length) {
    if (times[i]) {
      i++;
      continue;
    }
    let j = i;
    while (j < us.length && !times[j]) j++;
    const lo = i > 0 ? times[i - 1]![1] : start;
    let hi = j < us.length ? times[j]![0] : voiceEnd;
    if (hi <= lo) hi = lo + 0.12 * (j - i);
    const weights = us.slice(i, j).map((u) => Math.max(1, u.length));
    const total = weights.reduce((x, y) => x + y, 0);
    let t = lo;
    for (let k = i; k < j; k++) {
      const d = ((hi - lo) * weights[k - i]) / total;
      times[k] = [t, t + d];
      t += d;
    }
    i = j;
  }
  return us.map((w, k) => ({ w, a: times[k]![0], b: times[k]![1] }));
}

export function newId(): string {
  return Math.random().toString(16).slice(2, 12).padEnd(10, "0");
}

/** Split a cue at a character position of its text (or in the middle), time shared by text length. */
export function splitCue(c: SubCue, at?: number, time?: number): [SubCue, SubCue] {
  const t = flat(c.text);
  let pos = at ?? Math.floor(t.length / 2);
  if (at === undefined || at <= 0 || at >= t.length) {
    // nearest space to the middle
    const mid = Math.floor(t.length / 2);
    let best = -1;
    for (let k = 0; k < t.length; k++) if (t[k] === " " && (best < 0 || Math.abs(k - mid) < Math.abs(best - mid))) best = k;
    pos = best > 0 ? best : mid;
  }
  const first = t.slice(0, pos).trim();
  const second = t.slice(pos).trim();
  const cut = time && time > c.start + 0.2 && time < c.end - 0.2 ? time : c.start + ((c.end - c.start) * first.length) / Math.max(1, first.length + second.length);
  const wa = (c.words ?? []).filter((w) => w.start < cut);
  const wb = (c.words ?? []).filter((w) => w.start >= cut);
  return [
    { ...c, text: first, end: +(cut - 0.04).toFixed(3), words: wa },
    { ...c, id: newId(), text: second, start: +(cut + 0.04).toFixed(3), words: wb },
  ];
}

export function mergeCues(a: SubCue, b: SubCue): SubCue {
  return { ...a, end: Math.max(a.end, b.end), text: `${flat(a.text)} ${flat(b.text)}`.trim(), words: [...(a.words ?? []), ...(b.words ?? [])] };
}

export function hexToRgba(hex: string, alpha: number): string {
  let h = hex.replace("#", "");
  if (h.length === 3) h = h.split("").map((c) => c + c).join("");
  const n = parseInt(h || "ffffff", 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${Math.max(0, Math.min(1, alpha))})`;
}

/** CSS for the text layer of a style, at a given displayed video height (px). */
export function styleMetrics(s: SubStyle, videoHeight: number) {
  const em = (s.size / 100) * videoHeight;
  return {
    em,
    outline: s.outline * em,
    shadow: s.shadow * em,
    pad: s.bg_padding * em,
    spacing: s.letter_spacing * em,
    bottom: (s.position / 100) * videoHeight,
  };
}
