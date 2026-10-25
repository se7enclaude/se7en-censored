import { CSSProperties, ReactNode, useEffect, useRef, useState } from "react";

/* ---------------------------------------------------------------------------
   Signature motif — pixel-resolve: media starts as coarse pixel blocks with
   dark LED gaps and resolves to crisp. Used on analysis previews and upscales.
   --------------------------------------------------------------------------- */

function rng(seed: number) {
  let s = seed * 9301 + 49297;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

function paintProcedural(c: HTMLCanvasElement, w: number, h: number, seed: number) {
  const x = c.getContext("2d")!;
  const r = rng(seed);
  x.fillStyle = "#0B0B0C";
  x.fillRect(0, 0, w, h);
  const blob = (cx: number, cy: number, rad: number, col: string) => {
    const g = x.createRadialGradient(cx, cy, 0, cx, cy, rad);
    g.addColorStop(0, col);
    g.addColorStop(1, "rgba(0,0,0,0)");
    x.fillStyle = g;
    x.fillRect(0, 0, w, h);
  };
  blob(w * (0.55 + r() * 0.3), h * (0.3 + r() * 0.4), Math.max(w, h) * (0.35 + r() * 0.2), "rgba(242,43,43,.95)");
  blob(w * (0.1 + r() * 0.4), h * (0.5 + r() * 0.4), Math.max(w, h) * (0.3 + r() * 0.2), "rgba(120,70,60,.55)");
  blob(w * (0.3 + r() * 0.5), h * (0.2 + r() * 0.3), Math.max(w, h) * 0.12, "rgba(255,240,230,.85)");
  const hz = h * (0.62 + r() * 0.15);
  const g = x.createLinearGradient(0, hz - h * 0.2, 0, h);
  g.addColorStop(0, "rgba(5,5,5,0)");
  g.addColorStop(0.35, "rgba(5,5,5,.85)");
  g.addColorStop(1, "#050505");
  x.fillStyle = g;
  x.fillRect(0, 0, w, h);
}

export function PixelImage({
  src,
  block = 1,
  animateFrom,
  duration = 1400,
  seed = 7,
  ratio = "16/9",
  grid = true,
  fit = "cover",
  backdrop = false,
  className,
  children,
  style,
}: {
  src?: string | null;
  block?: number;
  animateFrom?: number;
  duration?: number;
  seed?: number;
  ratio?: string;
  grid?: boolean;
  fit?: "cover" | "contain";
  /** with fit="contain": fill the empty sides with a blurred, dimmed copy (vertical videos in a 16:9 frame) */
  backdrop?: boolean;
  className?: string;
  children?: ReactNode;
  style?: CSSProperties;
}) {
  const wrap = useRef<HTMLDivElement>(null);
  const cv = useRef<HTMLCanvasElement>(null);
  const srcCv = useRef<HTMLCanvasElement | null>(null);
  const [size, setSize] = useState<[number, number]>([0, 0]);
  const [img, setImg] = useState<HTMLImageElement | null>(null);
  const [b, setB] = useState(animateFrom || block);

  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setSize([Math.round(e.contentRect.width), Math.round(e.contentRect.height)]));
    ro.observe(wrap.current!);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (!src) {
      setImg(null);
      return;
    }
    const i = new Image();
    i.onload = () => setImg(i);
    i.onerror = () => setImg(null);
    i.src = src;
  }, [src]);

  useEffect(() => {
    if (!animateFrom || animateFrom <= block) {
      setB(block);
      return;
    }
    let raf = 0;
    const st = performance.now();
    const tick = (n: number) => {
      const p = Math.min(1, (n - st) / duration);
      const e = 1 - Math.pow(1 - p, 2.2);
      setB(Math.max(block, Math.round(animateFrom * Math.pow(block / animateFrom, e))));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [animateFrom, block, duration, src]);

  const draw = () => {
    const c = cv.current;
    const s = srcCv.current;
    if (!c || !s) return;
    const [w, h] = size;
    const d = window.devicePixelRatio || 1;
    c.width = w * d;
    c.height = h * d;
    const x = c.getContext("2d")!;
    const bb = Math.max(1, b);
    if (bb <= 1) {
      x.imageSmoothingEnabled = true;
      x.drawImage(s, 0, 0, w * d, h * d);
      return;
    }
    const ow = Math.max(1, Math.ceil(w / bb));
    const oh = Math.max(1, Math.ceil(h / bb));
    const o = document.createElement("canvas");
    o.width = ow;
    o.height = oh;
    const ox = o.getContext("2d")!;
    ox.imageSmoothingEnabled = true;
    ox.drawImage(s, 0, 0, ow, oh);
    x.imageSmoothingEnabled = false;
    x.drawImage(o, 0, 0, ow * bb * d, oh * bb * d);
    if (grid && bb >= 6) {
      x.fillStyle = "rgba(5,5,5,.6)";
      for (let i = 0; i < ow; i++) x.fillRect(i * bb * d, 0, Math.max(1, d), h * d);
      for (let j = 0; j < oh; j++) x.fillRect(0, j * bb * d, w * d, Math.max(1, d));
    }
  };

  useEffect(() => {
    const [w, h] = size;
    if (!w || !h) return;
    const s = document.createElement("canvas");
    s.width = w;
    s.height = h;
    if (img) {
      const x = s.getContext("2d")!;
      x.fillStyle = "#0B0B0C";
      x.fillRect(0, 0, w, h);
      const k = fit === "cover" ? Math.max(w / img.width, h / img.height) : Math.min(w / img.width, h / img.height);
      if (fit === "contain" && backdrop) {
        const kc = Math.max(w / img.width, h / img.height) * 1.15; // oversized so the blur has no dark edges
        x.filter = "blur(26px) brightness(0.42) saturate(1.15)";
        x.drawImage(img, (w - img.width * kc) / 2, (h - img.height * kc) / 2, img.width * kc, img.height * kc);
        x.filter = "none";
      }
      x.drawImage(img, (w - img.width * k) / 2, (h - img.height * k) / 2, img.width * k, img.height * k);
    } else paintProcedural(s, w, h, seed);
    srcCv.current = s;
    draw();
  }, [size, img, seed, fit, backdrop]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(draw, [b]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div ref={wrap} className={`pixel-image ${className ?? ""}`} style={{ aspectRatio: ratio, ...style }}>
      <canvas ref={cv} />
      {children}
    </div>
  );
}

/** Cursor-reactive 16px dot grid that swells and turns red near the pointer (home screen). */
export function PixelField() {
  const cv = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = cv.current!;
    const x = c.getContext("2d")!;
    const d = window.devicePixelRatio || 1;
    let W = 0;
    let H = 0;
    const rs = () => {
      W = c.clientWidth;
      H = c.clientHeight;
      c.width = W * d;
      c.height = H * d;
    };
    rs();
    const ro = new ResizeObserver(rs);
    ro.observe(c);
    let mx = -999;
    let my = -999;
    let tx = -999;
    let ty = -999;
    const mv = (e: MouseEvent) => {
      const r = c.getBoundingClientRect();
      tx = e.clientX - r.left;
      ty = e.clientY - r.top;
    };
    window.addEventListener("mousemove", mv);
    const light = document.documentElement.dataset.theme === "light";
    const base = light ? "11,11,12" : "244,243,241";
    const reduced = document.documentElement.dataset.motion === "reduced";
    let raf = 0;
    const G = 16;
    const tick = (n: number) => {
      mx += (tx - mx) * 0.1;
      my += (ty - my) * 0.1;
      x.setTransform(d, 0, 0, d, 0, 0);
      x.clearRect(0, 0, W, H);
      for (let yy = G / 2; yy < H; yy += G)
        for (let xx = G / 2; xx < W; xx += G) {
          const k = Math.max(0, 1 - Math.hypot(xx - mx, yy - my) / 230);
          const w = reduced ? 0.5 : 0.5 + 0.5 * Math.sin(n / 1100 + xx * 0.011 + yy * 0.017);
          const a = 0.035 + w * 0.045 + k * k * 0.8;
          const s = 2 + k * k * 6;
          x.fillStyle = k > 0.55 ? `rgba(242,43,43,${a})` : `rgba(${base},${a})`;
          x.fillRect(xx - s / 2, yy - s / 2, s, s);
        }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      window.removeEventListener("mousemove", mv);
    };
  }, []);
  return <canvas ref={cv} className="home-field" />;
}
