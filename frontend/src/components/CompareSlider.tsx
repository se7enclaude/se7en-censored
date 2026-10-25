import clsx from "clsx";
import { ChevronsLeftRight, Grid3x3, Maximize, Scan, ZoomIn, ZoomOut } from "lucide-react";
import { ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useT } from "../i18n";
import { Button, Ticks } from "./ui";

interface Props {
  original: string;
  result: string;
  width: number; // natural size of both images (original is resized to match)
  height: number;
  busy?: ReactNode;
  afterLabel?: string;
}

/** Split view ORIGINAL | ENHANCED with synchronized zoom (wheel) and pan (drag). */
export function CompareSlider({ original, result, width, height, busy, afterLabel }: Props) {
  const t = useT();
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 800, h: 500 });
  const [split, setSplit] = useState(0.5);
  const [scale, setScale] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [pixel, setPixel] = useState(false);
  const [drag, setDrag] = useState<null | "split" | "pan">(null);
  const last = useRef({ x: 0, y: 0 });

  const fit = Math.min(size.w / width, size.h / height) || 1;

  const center = useCallback(
    (s: number) => {
      setScale(s);
      setPan({ x: (size.w - width * s) / 2, y: (size.h - height * s) / 2 });
    },
    [size, width, height],
  );

  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    center(fit);
  }, [fit, result]); // eslint-disable-line react-hooks/exhaustive-deps

  const clampPan = (p: { x: number; y: number }, s: number) => {
    const iw = width * s;
    const ih = height * s;
    const x = iw <= size.w ? (size.w - iw) / 2 : Math.min(0, Math.max(size.w - iw, p.x));
    const y = ih <= size.h ? (size.h - ih) / 2 : Math.min(0, Math.max(size.h - ih, p.y));
    return { x, y };
  };

  const zoomAt = (factor: number, cx = size.w / 2, cy = size.h / 2) => {
    const next = Math.max(fit, Math.min(16, scale * factor));
    const k = next / scale;
    setPan(clampPan({ x: cx - (cx - pan.x) * k, y: cy - (cy - pan.y) * k }, next));
    setScale(next);
  };

  const onWheel = (e: React.WheelEvent) => {
    const r = box.current!.getBoundingClientRect();
    zoomAt(e.deltaY < 0 ? 1.18 : 1 / 1.18, e.clientX - r.left, e.clientY - r.top);
  };

  const onDown = (e: React.PointerEvent, mode: "split" | "pan") => {
    e.stopPropagation();
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    last.current = { x: e.clientX, y: e.clientY };
    setDrag(mode);
  };

  const onMove = (e: React.PointerEvent) => {
    if (!drag) return;
    const r = box.current!.getBoundingClientRect();
    if (drag === "split") {
      setSplit(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)));
    } else {
      const dx = e.clientX - last.current.x;
      const dy = e.clientY - last.current.y;
      last.current = { x: e.clientX, y: e.clientY };
      if (scale <= fit + 1e-6) {
        // not zoomed: dragging moves the divider, which feels natural
        setSplit(Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)));
      } else setPan((p) => clampPan({ x: p.x + dx, y: p.y + dy }, scale));
    }
  };

  const imgStyle = { width, height, transform: `translate(${pan.x}px, ${pan.y}px) scale(${scale})` };
  const pct = Math.round((scale / fit) * 100);

  return (
    <div
      ref={box}
      className={clsx("compare", drag && "dragging", pixel && "pixel", scale > fit * 1.01 && "zoomed")}
      onWheel={onWheel}
      onPointerDown={(e) => onDown(e, "pan")}
      onPointerMove={onMove}
      onPointerUp={() => setDrag(null)}
      onDoubleClick={() => (scale > fit * 1.01 ? center(fit) : zoomAt(1 / fit))}
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "ArrowLeft") setSplit((s) => Math.max(0, s - 0.05));
        if (e.key === "ArrowRight") setSplit((s) => Math.min(1, s + 0.05));
        if (e.key === "+" || e.key === "=") zoomAt(1.25);
        if (e.key === "-") zoomAt(0.8);
        if (e.key === "0") center(fit);
      }}
    >
      <div className="compare-layer">
        <img src={original} style={imgStyle} alt="original" draggable={false} />
      </div>
      <div className="compare-layer" style={{ clipPath: `inset(0 0 0 ${split * 100}%)` }}>
        <img src={result} style={imgStyle} alt="enhanced" draggable={false} />
      </div>
      <span className="compare-label left">{t("compare.original")}</span>
      <span className="compare-label right">{afterLabel ?? t("compare.enhanced")}</span>
      <Ticks inset={14} />
      <div className="compare-divider" style={{ left: `${split * 100}%` }} onPointerDown={(e) => onDown(e, "split")}>
        <div className="compare-handle">
          <ChevronsLeftRight size={16} />
        </div>
      </div>
      <div className="compare-tools" onPointerDown={(e) => e.stopPropagation()} onDoubleClick={(e) => e.stopPropagation()}>
        <Button size="sm" variant="ghost" iconOnly icon={<ZoomOut size={15} />} tip={t("compare.zoomOut")} onClick={() => zoomAt(0.8)} />
        <span className="compare-zoom">{pct}%</span>
        <Button size="sm" variant="ghost" iconOnly icon={<ZoomIn size={15} />} tip={t("compare.zoomIn")} onClick={() => zoomAt(1.25)} />
        <Button size="sm" variant="ghost" iconOnly icon={<Maximize size={15} />} tip={t("compare.fit")} onClick={() => center(fit)} />
        <Button size="sm" variant="ghost" iconOnly icon={<Scan size={15} />} tip={t("compare.actual")} onClick={() => zoomAt(1 / scale)} />
        <Button
          size="sm"
          variant="ghost"
          iconOnly
          icon={<Grid3x3 size={15} />}
          tip={t("compare.pixels")}
          onClick={() => setPixel(!pixel)}
          style={pixel ? { background: "rgba(255,255,255,0.15)" } : undefined}
        />
      </div>
      {busy && <div className="compare-busy">{busy}</div>}
    </div>
  );
}
