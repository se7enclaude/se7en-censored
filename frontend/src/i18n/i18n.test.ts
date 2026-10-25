import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { en } from "./en";
import { fr } from "./fr";

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    if (statSync(p).isDirectory()) return sources(p);
    return /\.tsx?$/.test(f) && !f.endsWith(".test.ts") ? [p] : [];
  });
}

const root = join(__dirname, "..");
const used = new Set<string>();
for (const file of sources(root)) {
  const text = readFileSync(file, "utf8");
  for (const m of text.matchAll(/\b(?:t|translate)\("([a-zA-Z0-9_.]+)"/g)) used.add(m[1]);
  for (const m of text.matchAll(/key: "([a-z]+\.[a-zA-Z0-9_.]+)"/g)) used.add(m[1]);
}
// keys built dynamically in the code
const dynamic = [
  ...["waiting", "analyzing", "downloading", "processing", "paused", "completed", "failed", "canceled"].map((s) => `status.${s}`),
  ...["video", "audio", "image", "playlist", "gallery"].map((k) => `kind.${k}`),
  ...["general", "anime", "realistic", "face", "detail"].flatMap((c) => [`category.${c}`, `category.${c}.empty`]),
  ...["general", "download", "naming", "presets", "ai", "tools", "subtitles", "components", "updates", "privacy", "advanced", "about"].flatMap((s) => [`settings.${s}`, `settings.${s}.desc`]),
  ...["pause_all", "resume_all", "retry_failed", "clear_finished", "cancel_all"].map((a) => `queue.bulk.${a}`),
  ...["debug", "info", "warning", "error"].map((l) => `logs.level.${l}`),
  ...["app", "download", "process", "gpu"].map((c) => `logs.cat.${c}`),
  ...["av", "video", "audio", "image"].map((m) => `mode.${m}`),
  ...["tools", "updates"].flatMap((s) => [`settings.${s}`, `settings.${s}.desc`]),
  ...["upscale", "face", "background", "colorize", "interpolate"].flatMap((x) => [`tool.${x}`, `tool.${x}.title`, `tool.${x}.tip`, `tool.${x}.start`]),
  ...["transparent", "color", "blur", "mask"].map((m) => `tool.bg.${m}`),
  ...["video", "audio", "image", "subtitle"].map((c) => `convert.cat.${c}`),
  ...["plays_everywhere", "half_size", "smallest", "keeps_everything", "web", "apple", "editing", "old_devices", "animated", "lossless", "apple_lossless", "print", "icon", "document", "styled"].map((n) => `convert.note.${n}`),
  ...["auto", "high", "normal", "small"].map((q) => `convert.q.${q}`),
  ...["fast", "medium", "slow"].map((x) => `speed.${x}`),
  ...["upscale", "convert", "trim", "studio", "subtitles"].map((k) => `queue.kindLabel.${k}`),
  ...["download", "upscale", "convert", "trim", "studio", "subtitles"].map((k) => `stats.kind.${k}`),
  ...["import", "language", "generate", "review", "style", "export"].map((k) => `subs.step.${k}`),
  ...["netflix", "youtube", "cinematic", "tiktok", "anime", "documentary", "minimal", "clean", "bold"].map((k) => `subs.preset.${k}`),
  ...["none", "fade", "pop", "slide", "word", "char", "highlight", "typewriter", "karaoke"].flatMap((k) => [`subs.anim.${k}`, `subs.anim.${k}.hint`]),
  ...[300, 400, 500, 600, 700, 900].map((w) => `subs.w${w}`),
  ...["generate", "edit", "style", "export"].map((k) => `subs.tab.${k}`),
  ...["srt", "vtt", "ass", "txt"].map((k) => `subs.fmt.${k}`),
  ...["both", "vocals", "instrumental"].map((k) => `studio.stems.${k}`),
  ...["voice", "other"].map((k) => `studio.sound.${k}`),
  ...["light", "medium", "strong"].flatMap((k) => [`studio.strength.${k}`, `studio.voice.${k}`]),
  ...["listing", "hashing", "comparing", "thumbnails", "done"].map((k) => `dupes.stage.${k}`),
  ...["jpg", "png", "webp"].map((k) => `image.${k}`),
  ...["idle", "checking", "installing"].map((k) => `update.state.${k}`),
  "notify.convertDone",
  "notify.trimDone",
  "download.compatConvertGpu",
  "download.compatConvertCpu",
];
dynamic.forEach((k) => used.add(k));

describe("i18n", () => {
  it("every key used in the UI exists in English", () => {
    expect([...used].filter((k) => !(k in en))).toEqual([]);
  });
  it("every English key has a French translation", () => {
    expect(Object.keys(en).filter((k) => !(k in fr))).toEqual([]);
  });
  it("placeholders match between languages", () => {
    const vars = (s: string) => (s.match(/\{\w+\}/g) ?? []).filter((v) => !["{platform}", "{uploader}", "{year}", "{month}", "{title}"].includes(v)).sort().join(",");
    const bad = Object.keys(en).filter((k) => fr[k] && vars(en[k]) !== vars(fr[k]));
    expect(bad).toEqual([]);
  });
});
