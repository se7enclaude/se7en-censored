import { CSSProperties, ReactNode, useLayoutEffect, useRef, useState } from "react";

export type Notch = { w: number; h: number };
export type Notches = { tl?: Notch; tr?: Notch; bl?: Notch; br?: Notch };

/** Outline of a rectangle with rectangular bites taken out of its corners, every corner (convex and concave)
 *  rounded with the same radius — the "notched hero" of the Studio Glass design system. */
export function cutoutPath(W: number, H: number, n: Notches, r: number): string {
  const v: [number, number][] = [];
  if (n.tl) v.push([0, n.tl.h], [n.tl.w, n.tl.h], [n.tl.w, 0]);
  else v.push([0, 0]);
  if (n.tr) v.push([W - n.tr.w, 0], [W - n.tr.w, n.tr.h], [W, n.tr.h]);
  else v.push([W, 0]);
  if (n.br) v.push([W, H - n.br.h], [W - n.br.w, H - n.br.h], [W - n.br.w, H]);
  else v.push([W, H]);
  if (n.bl) v.push([n.bl.w, H], [n.bl.w, H - n.bl.h], [0, H - n.bl.h]);
  else v.push([0, H]);
  const k = 0.5523;
  const f = (x: number) => Math.round(x * 100) / 100;
  let d = "";
  v.forEach((p, i) => {
    const pr = v[(i - 1 + v.length) % v.length];
    const nx = v[(i + 1) % v.length];
    const lp = Math.hypot(p[0] - pr[0], p[1] - pr[1]) || 1;
    const ln = Math.hypot(nx[0] - p[0], nx[1] - p[1]) || 1;
    const rr = Math.min(r, lp / 2, ln / 2);
    const ux = (pr[0] - p[0]) / lp, uy = (pr[1] - p[1]) / lp;
    const wx = (nx[0] - p[0]) / ln, wy = (nx[1] - p[1]) / ln;
    const a = [p[0] + ux * rr, p[1] + uy * rr];
    const b = [p[0] + wx * rr, p[1] + wy * rr];
    const c1 = [a[0] - ux * rr * k, a[1] - uy * rr * k];
    const c2 = [b[0] - wx * rr * k, b[1] - wy * rr * k];
    d += `${i === 0 ? "M" : "L"}${f(a[0])} ${f(a[1])} C${f(c1[0])} ${f(c1[1])} ${f(c2[0])} ${f(c2[1])} ${f(b[0])} ${f(b[1])} `;
  });
  return d + "Z";
}

/** Panel clipped to the notched shape, with a glass hairline drawn along the cut. Whatever sits in the notches
 *  is positioned by the parent. */
export function CutoutPanel({ notches, radius = 30, className = "", style, children }: { notches: Notches; radius?: number; className?: string; style?: CSSProperties; children?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setSize({ w: el.offsetWidth, h: el.offsetHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const d = size.w ? cutoutPath(size.w, size.h, notches, radius) : null;
  return (
    <div ref={ref} className={`cutout ${className}`} style={{ borderRadius: radius, clipPath: d ? `path('${d}')` : undefined, ...style }}>
      {children}
      {d && (
        <svg className="cutout-edge" width={size.w} height={size.h} aria-hidden="true">
          <path d={d} fill="none" stroke="rgba(255,255,255,.16)" strokeWidth="14" />
          <path d={d} fill="none" stroke="rgba(255,255,255,.62)" strokeWidth="2.5" />
        </svg>
      )}
    </div>
  );
}
