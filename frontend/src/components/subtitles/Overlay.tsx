import { CSSProperties, useEffect, useRef, useState } from "react";
import type { SubCue, SubStyle } from "../../api/types";
import { hexToRgba, styleMetrics, wordTimes } from "../../lib/subtitles";

/**
 * Subtitles drawn over the video exactly where and how the burned-in export draws them
 * (same sizes, outline outside the glyphs, one box per line, same animation timings).
 * `rect` is the displayed video picture inside the player (object-fit: contain).
 */
export function SubtitleOverlay({
  cues,
  style,
  lang,
  time,
  playing,
  getTime,
  rect,
}: {
  cues: SubCue[];
  style: SubStyle;
  lang: string;
  time: number;
  playing: boolean;
  getTime: () => number;
  rect: { x: number; y: number; w: number; h: number } | null;
}) {
  const [t, setT] = useState(time);
  const raf = useRef(0);
  useEffect(() => {
    setT(time);
  }, [time]);
  useEffect(() => {
    if (!playing) return;
    const tick = () => {
      setT(getTime());
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf.current);
  }, [playing, getTime]);

  if (!rect || rect.w < 10) return null;
  const active = cues.filter((c) => c.start <= t && t < c.end);
  if (!active.length) return null;
  const m = styleMetrics(style, rect.h);
  const box: CSSProperties = {
    position: "absolute",
    left: rect.x + (rect.w * (100 - style.max_width)) / 200,
    width: (rect.w * style.max_width) / 100,
    ...(style.vertical === "top" ? { top: rect.y + m.bottom } : { bottom: `calc(100% - ${rect.y + rect.h - m.bottom}px)` }),
    textAlign: style.align,
    pointerEvents: "none",
    display: "flex",
    flexDirection: "column",
    alignItems: style.align === "left" ? "flex-start" : style.align === "right" ? "flex-end" : "center",
    gap: 0,
  };
  return (
    <div className="sub-overlay" style={{ position: "absolute", inset: 0, pointerEvents: "none", overflow: "hidden" }}>
      {active.map((c) => (
        <div key={c.id} style={box}>
          <CueView cue={c} style={style} lang={lang} t={t} m={m} />
        </div>
      ))}
    </div>
  );
}

function CueView({ cue, style: s, lang, t, m }: { cue: SubCue; style: SubStyle; lang: string; t: number; m: ReturnType<typeof styleMetrics> }) {
  const speed = Math.max(0.25, s.anim_speed || 1);
  const since = (t - cue.start) * 1000;
  const until = (cue.end - t) * 1000;
  let opacity = 1;
  let transform = "";
  if (s.animation === "fade") opacity = Math.min(1, since / (160 / speed), until / (120 / speed));
  if (s.animation === "pop") {
    const d = 120 / speed;
    const k = since < d ? 0.72 + (1.06 - 0.72) * (since / d) : since < d * 1.7 ? 1.06 - 0.06 * ((since - d) / (d * 0.7)) : 1;
    transform = `scale(${k})`;
    opacity = Math.min(1, since / (70 / speed), until / (90 / speed));
  }
  if (s.animation === "slide") {
    const p = Math.min(1, since / (240 / speed));
    transform = `translateY(${(1 - p) * m.em * 0.45 * (s.vertical === "top" ? -1 : 1)}px)`;
    opacity = Math.min(1, since / (150 / speed), until / (100 / speed));
  }
  const text = s.uppercase ? cue.text.toUpperCase() : cue.text;
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  const timed = ["word", "char", "typewriter", "highlight", "karaoke"].includes(s.animation) ? wordTimes(cue, lang, text) : null;
  const sep = ["ja", "zh", "yue", "th"].includes(lang) ? "" : " ";
  const lineStyle: CSSProperties = {
    fontFamily: `"${s.font}", "Segoe UI", sans-serif`,
    fontSize: m.em,
    fontWeight: s.weight,
    fontStyle: s.italic ? "italic" : "normal",
    lineHeight: s.line_height,
    letterSpacing: m.spacing,
    color: hexToRgba(s.color, s.opacity),
    WebkitTextStroke: s.background === "box" || !m.outline ? undefined : `${m.outline * 2}px ${hexToRgba(s.outline_color, s.opacity)}`,
    paintOrder: "stroke fill",
    textShadow: m.shadow ? `${m.shadow}px ${m.shadow}px 0 ${hexToRgba(s.shadow_color, s.shadow_opacity * s.opacity)}` : undefined,
    whiteSpace: "pre",
    background: s.background === "box" ? hexToRgba(s.bg_color, s.bg_opacity) : undefined,
    padding: s.background === "box" ? `0 ${m.pad}px` : undefined,
    boxDecorationBreak: "clone",
    WebkitBoxDecorationBreak: "clone",
  };
  const countOf = (line: string) => (sep ? line.split(" ").filter(Boolean).length : [...line.replace(/ /g, "")].length);
  let k = 0;
  return (
    <div style={{ opacity: Math.max(0, opacity), transform, transformOrigin: "50% 100%", display: "flex", flexDirection: "column", alignItems: "inherit" }}>
      {lines.map((line, li) => {
        const n = countOf(line);
        const words = timed ? timed.slice(k, k + n) : null;
        k += n;
        return (
          <span key={li} style={lineStyle}>
            {words ? words.map((w, wi) => <Unit key={wi} w={w} t={t} s={s} speed={speed} last={wi === words.length - 1} sep={sep} />) : line}
          </span>
        );
      })}
    </div>
  );
}

function Unit({ w, t, s, speed, last, sep }: { w: { w: string; a: number; b: number }; t: number; s: SubStyle; speed: number; last: boolean; sep: string }) {
  const tail = last ? "" : sep;
  if (s.animation === "word") {
    const o = Math.max(0, Math.min(1, ((t - w.a) * 1000) / (90 / speed)));
    return <span style={{ opacity: o }}>{w.w + tail}</span>;
  }
  if (s.animation === "highlight") {
    const on = t >= w.a && t < w.b + 0.08;
    return <span style={{ color: on ? s.highlight_color : undefined, transition: "color 60ms" }}>{w.w + tail}</span>;
  }
  if (s.animation === "karaoke") {
    const p = Math.max(0, Math.min(1, (t - w.a) / Math.max(0.01, w.b - w.a))) * 100;
    return (
      <span
        style={{
          backgroundImage: `linear-gradient(90deg, ${s.highlight_color} ${p}%, ${hexToRgba(s.color, s.opacity)} ${p}%)`,
          WebkitBackgroundClip: "text",
          backgroundClip: "text",
          color: "transparent",
        }}
      >
        {w.w + tail}
      </span>
    );
  }
  // char / typewriter: letters appear one after another across the word's time
  const chars = [...w.w];
  const dur = (w.b - w.a) * 1000;
  const step = s.animation === "char" ? dur / Math.max(1, chars.length) : Math.min(60 / speed, dur / Math.max(1, chars.length));
  const fade = s.animation === "char" ? 70 / speed : 1;
  return (
    <span>
      {chars.map((ch, i) => {
        const ta = w.a * 1000 + i * step;
        const o = Math.max(0, Math.min(1, (t * 1000 - ta) / fade));
        return (
          <span key={i} style={{ opacity: o }}>
            {ch}
          </span>
        );
      })}
      {tail}
    </span>
  );
}
