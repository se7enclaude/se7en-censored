import { useEffect, useRef, useState } from "react";
import { useT } from "../i18n";
import "./splash-censored.css";

/* ---------------------------------------------------------------------------
   se7en Censored launch animation — « la mosaïque », silent, about 2.4 s:
   the logo appears hidden under huge censor pixels → the mosaic gets finer
   and finer until the wordmark is sharp → a soft pink blur bar sweeps across
   it one last time and leaves it clean. On exit the logo is pixelated again
   and dissolves. Click (or any key) to skip once the engine is ready.
   --------------------------------------------------------------------------- */

const HOLD = 2350;
const OUT = 560;
const PINK = "#F28B96";

/** Pixel size over time (ms): coarse blocks → sharp. */
function blockAt(t: number): number {
  if (t < 250) return 64;
  const steps = [64, 40, 26, 16, 10, 6, 3, 1];
  const i = Math.min(steps.length - 1, Math.floor((t - 250) / 165));
  return steps[i];
}

function Mosaic({ leaving }: { leaving: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const leavingRef = useRef(leaving);
  leavingRef.current = leaving;
  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const W = 760, H = 300;
    c.width = W * dpr;
    c.height = H * dpr;
    const ctx = c.getContext("2d")!;
    const src = document.createElement("canvas");
    src.width = c.width;
    src.height = c.height;
    const s = src.getContext("2d")!;
    const small = document.createElement("canvas");
    const sm = small.getContext("2d")!;
    let raf = 0;
    let t0 = 0;
    let leaveAt = 0;

    const paintSource = () => {
      s.clearRect(0, 0, src.width, src.height);
      s.save();
      s.scale(dpr, dpr);
      s.textAlign = "center";
      s.textBaseline = "alphabetic";
      s.font = "400 150px Anton, Impact, sans-serif";
      const word = ["S", "E", "7", "E", "N"];
      const widths = word.map((l) => s.measureText(l).width);
      let x = W / 2 - widths.reduce((a, b) => a + b, 0) / 2;
      word.forEach((l, i) => {
        s.fillStyle = l === "7" ? PINK : "#ffffff";
        s.fillText(l, x + widths[i] / 2, 168);
        x += widths[i];
      });
      s.font = "400 40px Anton, Impact, sans-serif";
      s.fillStyle = PINK;
      s.letterSpacing = "14px";
      s.fillText("CENSORED", W / 2 + 7, 238);
      s.restore();
    };

    const frame = (now: number) => {
      if (!t0) t0 = now;
      const t = now - t0;
      let block = blockAt(t);
      if (leavingRef.current) {
        if (!leaveAt) leaveAt = now;
        const k = Math.min(1, (now - leaveAt) / 420);
        block = Math.max(1, Math.round(1 + k * 50));
      }
      ctx.clearRect(0, 0, c.width, c.height);
      if (block <= 1) {
        ctx.drawImage(src, 0, 0);
      } else {
        const b = block * dpr;
        small.width = Math.max(1, Math.ceil(c.width / b));
        small.height = Math.max(1, Math.ceil(c.height / b));
        sm.imageSmoothingEnabled = true;
        sm.clearRect(0, 0, small.width, small.height);
        sm.drawImage(src, 0, 0, small.width, small.height);
        ctx.imageSmoothingEnabled = false;
        ctx.drawImage(small, 0, 0, small.width * b, small.height * b);
      }
      raf = requestAnimationFrame(frame);
    };
    document.fonts.load("400 150px Anton").catch(() => undefined).finally(() => {
      paintSource();
      raf = requestAnimationFrame(frame);
    });
    return () => cancelAnimationFrame(raf);
  }, []);
  return <canvas ref={ref} className="spc-canvas" />;
}

export function Splash({ onDone, version, ready }: { onDone: () => void; version?: string; ready?: boolean }) {
  const t = useT();
  const [leaving, setLeaving] = useState(false);
  const [held, setHeld] = useState(false);
  const done = useRef(false);
  const readyRef = useRef(ready);
  readyRef.current = ready;

  const leave = () => {
    if (done.current) return;
    done.current = true;
    setLeaving(true);
    window.setTimeout(onDone, OUT);
  };

  useEffect(() => {
    const id = window.setTimeout(() => setHeld(true), HOLD);
    const key = () => readyRef.current && leave();
    window.addEventListener("keydown", key);
    return () => {
      window.clearTimeout(id);
      window.removeEventListener("keydown", key);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (held && ready) leave();
  }, [held, ready]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className={`splash spc ${leaving ? "leaving" : ""}`} onClick={() => ready && leave()}>
      <div className="spc-grid" />
      <div className="spc-stage">
        <Mosaic leaving={leaving} />
        <span className="spc-sweep" />
        <span className="spc-frame tl" /><span className="spc-frame tr" /><span className="spc-frame bl" /><span className="spc-frame br" />
      </div>
      <div className="spc-tag">{t("splash.tagline")}</div>
      <div className="spc-foot">
        <span>v{version ?? ""}</span>
        <span className="spc-bar"><span className={ready ? "full" : ""} /></span>
        <span>{ready ? t("splash.skip") : t("splash.init")}</span>
      </div>
    </div>
  );
}
