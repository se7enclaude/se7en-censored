import clsx from "clsx";
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { SubCue } from "../../api/types";
import { cps, flat, fmtTime } from "../../lib/subtitles";

const RULER = 22;
const HEIGHT = 112;
const EDGE = 7;
const MIN_DUR = 0.2;

type Drag = { id: string; mode: "move" | "start" | "end"; x0: number; start: number; end: number; moved: boolean };

/**
 * Subtitle timeline: waveform + cue blocks. Drag a block to move it, its edges to change its duration;
 * edges snap to the playhead and to neighbouring cues; cues never overlap. Ctrl + wheel zooms around the
 * pointer, wheel scrolls. Only the visible window is drawn, so hour-long videos stay smooth.
 */
export function SubtitleTimeline({
  duration,
  time,
  playing,
  cues,
  selected,
  peaks,
  pps,
  maxCps,
  onZoom,
  onSeek,
  onSelect,
  onChange,
}: {
  duration: number;
  time: number;
  playing: boolean;
  cues: SubCue[];
  selected: Set<string>;
  peaks: number[];
  pps: number;
  maxCps: number;
  onZoom: (pps: number, anchorTime?: number) => void;
  onSeek: (t: number) => void;
  onSelect: (id: string, mode: "one" | "toggle" | "range") => void;
  onChange: (id: string, patch: Partial<SubCue>, commit: boolean) => void;
}) {
  const scroll = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [view, setView] = useState({ left: 0, width: 800 });
  const drag = useRef<Drag | null>(null);
  const total = Math.max(1, duration) * pps;

  // keep the view in sync with scrolling / resizing
  useLayoutEffect(() => {
    const el = scroll.current;
    if (!el) return;
    const update = () => setView({ left: el.scrollLeft, width: el.clientWidth });
    update();
    el.addEventListener("scroll", update, { passive: true });
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => {
      el.removeEventListener("scroll", update);
      ro.disconnect();
    };
  }, []);

  // follow the playhead while playing
  useEffect(() => {
    const el = scroll.current;
    if (!el || !playing || drag.current) return;
    const x = time * pps;
    if (x < el.scrollLeft + 20 || x > el.scrollLeft + el.clientWidth - 40) el.scrollLeft = Math.max(0, x - el.clientWidth * 0.3);
  }, [time, playing, pps]);

  // ruler + waveform for the visible window
  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const d = window.devicePixelRatio || 1;
    c.width = view.width * d;
    c.height = HEIGHT * d;
    const x = c.getContext("2d")!;
    x.setTransform(d, 0, 0, d, 0, 0);
    x.clearRect(0, 0, view.width, HEIGHT);
    const css = getComputedStyle(c);
    const faint = css.getPropertyValue("--text-tertiary").trim() || "#777";
    const line = css.getPropertyValue("--border-subtle").trim() || "#333";
    const wave = css.getPropertyValue("--text-secondary").trim() || "#aaa";
    const t0 = view.left / pps;
    const t1 = (view.left + view.width) / pps;
    // ruler: pick a tick step that leaves ~90 px between labels
    const steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
    const step = steps.find((s) => s * pps >= 90) ?? 600;
    x.fillStyle = faint;
    x.font = "10px 'Geist Mono', monospace";
    x.strokeStyle = line;
    for (let t = Math.floor(t0 / step) * step; t <= t1; t += step) {
      const px = t * pps - view.left;
      x.beginPath();
      x.moveTo(px + 0.5, RULER - 7);
      x.lineTo(px + 0.5, HEIGHT);
      x.globalAlpha = 0.5;
      x.stroke();
      x.globalAlpha = 1;
      x.fillText(fmtTime(t, step < 1).replace(/^00:/, ""), px + 4, 12);
    }
    // waveform (mirrored envelope)
    if (peaks.length && duration > 0) {
      const mid = RULER + (HEIGHT - RULER) / 2;
      const amp = (HEIGHT - RULER) / 2 - 6;
      x.fillStyle = wave;
      x.globalAlpha = 0.35;
      for (let px = 0; px < view.width; px++) {
        const t = (view.left + px) / pps;
        const i = Math.floor((t / duration) * peaks.length);
        const v = peaks[Math.min(peaks.length - 1, Math.max(0, i))] || 0;
        const h = Math.max(0.5, v * amp);
        x.fillRect(px, mid - h, 1, h * 2);
      }
      x.globalAlpha = 1;
    }
  }, [view, pps, peaks, duration]);

  const neighbours = useCallback(
    (id: string) => {
      const i = cues.findIndex((c) => c.id === id);
      return { prev: i > 0 ? cues[i - 1] : null, next: i >= 0 && i < cues.length - 1 ? cues[i + 1] : null };
    },
    [cues],
  );

  const snap = (t: number, id: string) => {
    const tol = 6 / pps;
    const targets = [time];
    const { prev, next } = neighbours(id);
    if (prev) targets.push(prev.end + 0.083);
    if (next) targets.push(next.start - 0.083);
    for (const g of targets) if (Math.abs(g - t) < tol) return g;
    return t;
  };

  const onPointerDown = (e: React.PointerEvent, c: SubCue) => {
    e.stopPropagation();
    if (e.button !== 0) return;
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const off = e.clientX - r.left;
    const mode = off < EDGE ? "start" : off > r.width - EDGE ? "end" : "move";
    drag.current = { id: c.id, mode, x0: e.clientX, start: c.start, end: c.end, moved: false };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    onSelect(c.id, e.ctrlKey || e.metaKey ? "toggle" : e.shiftKey ? "range" : "one");
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dt = (e.clientX - d.x0) / pps;
    if (!d.moved && Math.abs(e.clientX - d.x0) < 3) return;
    d.moved = true;
    const { prev, next } = neighbours(d.id);
    const lo = prev ? prev.end + 0.083 : 0;
    const hi = next ? next.start - 0.083 : duration || Infinity;
    if (d.mode === "move") {
      const len = d.end - d.start;
      let s = snap(d.start + dt, d.id);
      const eSnap = snap(d.end + dt, d.id);
      if (eSnap !== d.end + dt) s = eSnap - len;
      s = Math.max(lo, Math.min(hi - len, s));
      onChange(d.id, { start: +s.toFixed(3), end: +(s + len).toFixed(3) }, false);
    } else if (d.mode === "start") {
      const s = Math.max(lo, Math.min(d.end - MIN_DUR, snap(d.start + dt, d.id)));
      onChange(d.id, { start: +s.toFixed(3) }, false);
    } else {
      const en = Math.min(hi, Math.max(d.start + MIN_DUR, snap(d.end + dt, d.id)));
      onChange(d.id, { end: +en.toFixed(3) }, false);
    }
  };

  const onPointerUp = () => {
    const d = drag.current;
    drag.current = null;
    if (d?.moved) onChange(d.id, {}, true);
  };

  const onWheel = (e: React.WheelEvent) => {
    const el = scroll.current;
    if (!el) return;
    if (e.ctrlKey || e.metaKey) {
      const r = el.getBoundingClientRect();
      const anchor = (el.scrollLeft + e.clientX - r.left) / pps;
      const next = Math.max(8, Math.min(800, pps * (e.deltaY < 0 ? 1.25 : 0.8)));
      onZoom(next, anchor);
      requestAnimationFrame(() => {
        if (scroll.current) scroll.current.scrollLeft = anchor * next - (e.clientX - r.left);
      });
    } else if (Math.abs(e.deltaY) > Math.abs(e.deltaX)) {
      el.scrollLeft += e.deltaY;
    }
  };

  const onBackgroundDown = (e: React.PointerEvent) => {
    const el = scroll.current;
    if (!el || e.button !== 0) return;
    const r = el.getBoundingClientRect();
    onSeek(Math.max(0, Math.min(duration, (el.scrollLeft + e.clientX - r.left) / pps)));
  };

  const t0 = view.left / pps - 1;
  const t1 = (view.left + view.width) / pps + 1;
  const visible = cues.filter((c) => c.end >= t0 && c.start <= t1);

  return (
    <div className="sub-tl" onWheel={onWheel}>
      <div className="sub-tl-scroll" ref={scroll} onPointerDown={onBackgroundDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp}>
        <div style={{ width: total, height: HEIGHT, position: "relative" }}>
          <canvas ref={canvas} style={{ position: "sticky", left: 0, width: view.width, height: HEIGHT, display: "block" }} />
          {visible.map((c) => {
            const fast = cps(c) > maxCps;
            return (
              <div
                key={c.id}
                className={clsx("sub-tl-cue", selected.has(c.id) && "on", fast && "fast", c.start <= time && time < c.end && "live")}
                style={{ left: c.start * pps, width: Math.max(3, (c.end - c.start) * pps), top: RULER + 4, height: HEIGHT - RULER - 8 }}
                onPointerDown={(e) => onPointerDown(e, c)}
                onDoubleClick={(e) => {
                  e.stopPropagation();
                  onSeek(c.start);
                }}
                title={`${fmtTime(c.start)} → ${fmtTime(c.end)}\n${flat(c.text)}`}
              >
                <span className="sub-tl-edge l" />
                <span className="sub-tl-text">{flat(c.text)}</span>
                <span className="sub-tl-edge r" />
              </div>
            );
          })}
          <div className="sub-tl-head" style={{ left: time * pps }} />
        </div>
      </div>
    </div>
  );
}
