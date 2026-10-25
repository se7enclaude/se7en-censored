import { CSSProperties, useEffect, useRef, useState } from "react";
import { useT } from "../i18n";

/* ---------------------------------------------------------------------------
   se7en launch animation — "Studio Glass", silent, about 2.6 s:
   soft rose clouds drift over the red sky → a liquid-glass drop swells into a
   pill → SE7EN rises letter by letter inside it (the 7 in red) → a specular
   sheen crosses the glass → the tagline fades in → everything lifts and
   dissolves into the app. Click (or any key) to skip once the engine is ready.
   --------------------------------------------------------------------------- */

const LETTERS = ["S", "E", "7", "E", "N"];
const HOLD = 2100; // the wordmark is complete: the splash may leave from here (once the engine is ready)
const OUT = 520; // exit duration

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

  // leave as soon as the animation is complete and the engine answers (a slow first start keeps the pill on)
  useEffect(() => {
    if (held && ready) leave();
  }, [held, ready]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className={`splash splash-glass ${leaving ? "leaving" : ""}`} onClick={() => ready && leave()}>
      <div className="splash-cloud c1" />
      <div className="splash-cloud c2" />
      <div className="splash-cloud c3" />
      <div className="splash-center">
        <div className="splash-pill">
          <span className="splash-sheen" />
          <span className="splash-word" aria-label="se7en">
            {LETTERS.map((l, i) => (
              <span key={i} className={l === "7" ? "seven" : undefined} style={{ "--i": i } as CSSProperties}>
                {l}
              </span>
            ))}
          </span>
        </div>
        <div className="splash-tag">{t("splash.tagline")}</div>
      </div>
      <div className="splash-foot">
        <span className="splash-ver">v{version ?? ""}</span>
        <span className="splash-bar"><span className={ready ? "full" : ""} /></span>
        <span className="splash-ver">{ready ? t("splash.skip") : t("splash.init")}</span>
      </div>
    </div>
  );
}
