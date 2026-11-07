import clsx from "clsx";
import { ChevronLeft, ChevronRight, Eye, EyeOff, Gauge, Loader2, Maximize2, Pause, Play, RotateCcw, RotateCw, SkipBack, SkipForward, Volume2, VolumeX } from "lucide-react";
import { forwardRef, ReactNode, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { Button, Switch } from "../../components/ui";
import { parseTc, tc } from "./Passages";

export type Box = [number, number, number, number];
export interface Mark {
  f0: number;
  f1: number;
  color: string;
  kind: "ai" | "validated" | "manual" | "segment";
}
export interface PlayerApi {
  toggle: () => void;
  pause: () => void;
  seek: (f: number) => void;
  step: (frames: number) => void;
  skip: (seconds: number) => void;
  speed: (dir: 1 | -1) => void;
  mute: () => void;
  fullscreen: () => void;
  playing: () => boolean;
}

export const SPEEDS = [0.25, 0.5, 1, 1.5, 2, 4];

/** Lecteur de la vérification : la vidéo (copie d'aperçu ou original), l'image exacte du moteur en pause, les calques
 *  (flou en direct, cadres), les contrôles et la frise. Le numéro d'image vient de la vidéo elle-même
 *  (requestVideoFrameCallback) : il correspond toujours à l'image affichée. */
export const Player = forwardRef<PlayerApi, {
  src: string | null;
  fps: number;
  total: number;
  frame: number;
  onFrame: (f: number) => void;
  ratio: number;
  stillUrl: string | null;
  onStillLoad?: () => void;
  overlay?: ReactNode;
  marks: Mark[];
  preview: boolean;
  setPreview: (v: boolean) => void;
  stageProps?: React.HTMLAttributes<HTMLDivElement>;
  stageRef?: React.RefObject<HTMLDivElement | null>;
  onPlayingChange?: (p: boolean) => void;
  waitingText?: string | null;
  onVideoClick?: () => void;
  onJump: (dir: 1 | -1) => void;
}>(function Player({ src, fps, total, frame, onFrame, ratio, stillUrl, onStillLoad, overlay, marks, preview, setPreview, stageProps, stageRef, onPlayingChange, waitingText, onVideoClick, onJump }, ref) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const ownStage = useRef<HTMLDivElement | null>(null);
  const stage = stageRef ?? ownStage;
  const [playing, setPlaying] = useState(false);
  const [buffering, setBuffering] = useState(false);
  const [failed, setFailed] = useState(false);
  const [rate, setRate] = useState(1);
  const [muted, setMuted] = useState(false);
  const [volume, setVolume] = useState(1);
  const frameRef = useRef(frame);
  frameRef.current = frame;
  const playable = !!src && !failed;
  const last = Math.max(0, total - 1);

  // le numéro de l'image affichée, donné par la vidéo à chaque image peinte
  useEffect(() => {
    const v = videoRef.current;
    if (!v || !playable) return;
    let handle = 0;
    const rvfc = (v as any).requestVideoFrameCallback?.bind(v);
    const cancelRvfc = (v as any).cancelVideoFrameCallback?.bind(v);
    const fromTime = (t: number) => Math.max(0, Math.min(last, Math.round(t * fps - 0.001)));
    const onPainted = (_: number, meta: { mediaTime: number }) => {
      onFrame(fromTime(meta.mediaTime));
      handle = rvfc(onPainted);
    };
    let raf = 0;
    const tick = () => {
      onFrame(fromTime(v.currentTime - 0.5 / fps));
      if (!v.paused) raf = requestAnimationFrame(tick);
    };
    if (rvfc) handle = rvfc(onPainted);
    const onPlay = () => { setPlaying(true); onPlayingChange?.(true); if (!rvfc) raf = requestAnimationFrame(tick); };
    const onPause = () => { setPlaying(false); onPlayingChange?.(false); setBuffering(false); };
    const onWaiting = () => setBuffering(true);
    const onPlaying = () => setBuffering(false);
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    v.addEventListener("waiting", onWaiting);
    v.addEventListener("playing", onPlaying);
    v.addEventListener("canplay", onPlaying);
    return () => {
      if (rvfc && cancelRvfc) cancelRvfc(handle);
      cancelAnimationFrame(raf);
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
      v.removeEventListener("waiting", onWaiting);
      v.removeEventListener("playing", onPlaying);
      v.removeEventListener("canplay", onPlaying);
    };
  }, [fps, last, playable, onFrame, onPlayingChange]);

  useEffect(() => setFailed(false), [src]);
  useEffect(() => {
    const v = videoRef.current;
    if (v) { v.playbackRate = rate; v.muted = muted; v.volume = volume; }
  }, [rate, muted, volume, playable]);

  const seek = useCallback((f: number) => {
    const k = Math.max(0, Math.min(last, Math.round(f)));
    onFrame(k);
    const v = videoRef.current;
    if (v && playable) v.currentTime = (k + 0.5) / fps; // le milieu de l'image k : jamais la voisine
  }, [fps, last, onFrame, playable]);

  const api: PlayerApi = useMemo(() => ({
    toggle: () => {
      const v = videoRef.current;
      if (!v || !playable) return;
      if (v.paused) v.play().catch(() => undefined);
      else v.pause();
    },
    pause: () => videoRef.current?.pause(),
    seek,
    step: (n) => { videoRef.current?.pause(); seek(frameRef.current + n); },
    skip: (s) => seek(frameRef.current + Math.round(s * fps)),
    speed: (dir) => setRate((r) => SPEEDS[Math.max(0, Math.min(SPEEDS.length - 1, SPEEDS.indexOf(r) + dir))] ?? 1),
    mute: () => setMuted((m) => !m),
    fullscreen: () => {
      const el = stage.current;
      if (!el) return;
      if (document.fullscreenElement) document.exitFullscreen().catch(() => undefined);
      else el.requestFullscreen().catch(() => undefined);
    },
    playing: () => !!videoRef.current && !videoRef.current.paused,
  }), [fps, playable, seek, stage]);
  useImperativeHandle(ref, () => api, [api]);

  const [editing, setEditing] = useState(false);
  const [typed, setTyped] = useState("");
  const goTo = () => {
    const s = parseTc(typed);
    if (s !== null) seek(s * fps);
    setEditing(false);
  };

  return (
    <div className="card cz-viewer">
      <div className="cz-stage" ref={stage} style={{ aspectRatio: String(ratio) }} {...stageProps}>
        {playable && (
          <video
            ref={videoRef}
            src={src!}
            preload="auto"
            playsInline
            onError={() => setFailed(true)}
            onClick={onVideoClick}
            onDoubleClick={api.fullscreen}
          />
        )}
        {stillUrl && <img className={clsx("cz-still", playable && "fade")} src={stillUrl} alt="" draggable={false} onLoad={onStillLoad} />}
        {overlay}
        {buffering && playing && <div className="cz-buffer"><Loader2 size={28} className="spin" /></div>}
        {waitingText && <div className="cz-wait">{waitingText}</div>}
      </div>

      <Timeline marks={marks} total={total} frame={frame} fps={fps} onSeek={seek} />

      <div className="cz-controls">
        <div className="cz-ctl-group">
          <Button size="sm" variant="ghost" iconOnly icon={<SkipBack size={15} />} tip="Zone précédente (P)" onClick={() => onJump(-1)} />
          <Button size="sm" variant="ghost" iconOnly icon={<RotateCcw size={15} />} tip="10 s en arrière (J)" onClick={() => api.skip(-10)} />
          <Button size="sm" variant="ghost" iconOnly icon={<ChevronLeft size={17} />} tip="Image précédente (← ou ,)" onClick={() => api.step(-1)} />
          <Button size="sm" variant="primary" iconOnly icon={playing ? <Pause size={16} /> : <Play size={16} />} tip="Lecture / pause (Espace ou K)" disabled={!playable} onClick={api.toggle} />
          <Button size="sm" variant="ghost" iconOnly icon={<ChevronRight size={17} />} tip="Image suivante (→ ou .)" onClick={() => api.step(1)} />
          <Button size="sm" variant="ghost" iconOnly icon={<RotateCw size={15} />} tip="10 s en avant (L)" onClick={() => api.skip(10)} />
          <Button size="sm" variant="ghost" iconOnly icon={<SkipForward size={15} />} tip="Zone suivante (N)" onClick={() => onJump(1)} />
        </div>

        <div className="cz-time mono">
          {editing ? (
            <input
              className="cz-time-input mono"
              autoFocus
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              onBlur={goTo}
              onKeyDown={(e) => { if (e.key === "Enter") goTo(); if (e.key === "Escape") setEditing(false); e.stopPropagation(); }}
            />
          ) : (
            <button className="cz-time-btn" data-tip="Clique pour aller à un instant précis" onClick={() => { setTyped(tc(frame / fps)); setEditing(true); }}>
              {tc(frame / fps)}
            </button>
          )}
          <span className="faint"> / {tc(total / fps)}</span>
          <span className="faint cz-frame">image {frame}</span>
        </div>

        <span className="spacer" />

        <div className="cz-ctl-group">
          <button className="cz-speed" data-tip="Vitesse ([ et ])" disabled={!playable} onClick={() => setRate((r) => SPEEDS[(SPEEDS.indexOf(r) + 1) % SPEEDS.length])}>
            <Gauge size={13} /> {String(rate).replace(".", ",")}×
          </button>
          <Button size="sm" variant="ghost" iconOnly icon={muted || volume === 0 ? <VolumeX size={15} /> : <Volume2 size={15} />} tip="Son (M)" disabled={!playable} onClick={() => setMuted((m) => !m)} />
          <input className="cz-volume" type="range" min={0} max={1} step={0.05} value={muted ? 0 : volume} aria-label="Volume" disabled={!playable} onChange={(e) => { setVolume(Number(e.target.value)); setMuted(false); }} />
          <label className="row clip-check cz-preview-toggle" data-tip="Montre le flou tel qu'il sera exporté, en pause comme en lecture (B)">
            <Switch checked={preview} onChange={setPreview} />
            <span>{preview ? <Eye size={14} /> : <EyeOff size={14} />} Aperçu flouté</span>
          </label>
          <Button size="sm" variant="ghost" iconOnly icon={<Maximize2 size={14} />} tip="Plein écran (F ou double-clic)" onClick={api.fullscreen} />
        </div>
      </div>
      {!playable && !waitingText && (
        <div className="faint cz-noplay">Cette vidéo ne se lit pas dans la fenêtre : avance image par image ou clique sur la frise. L'export n'est pas concerné.</div>
      )}
    </div>
  );
});

function Timeline({ marks, total, frame, fps, onSeek }: { marks: Mark[]; total: number; frame: number; fps: number; onSeek: (f: number) => void }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const at = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * total;
  };
  return (
    <div
      className="cz-timeline"
      ref={ref}
      role="slider"
      tabIndex={0}
      aria-label="Frise de la vidéo"
      aria-valuemin={0}
      aria-valuemax={Math.max(0, total - 1)}
      aria-valuenow={frame}
      onPointerDown={(e) => { (e.target as HTMLElement).setPointerCapture(e.pointerId); onSeek(at(e)); }}
      onPointerMove={(e) => { const f = at(e); setHover(f); if (e.buttons === 1) onSeek(f); }}
      onPointerLeave={() => setHover(null)}
    >
      {marks.map((m, i) => (
        <span key={i} className={clsx("cz-mark", `is-${m.kind}`)} style={{ left: `${(m.f0 / total) * 100}%`, width: `max(2px, ${((m.f1 - m.f0 + 1) / total) * 100}%)`, background: m.kind === "segment" ? undefined : m.color }} />
      ))}
      <span className="cz-head" style={{ left: `${(frame / total) * 100}%` }} />
      {hover !== null && <span className="cz-hover mono" style={{ left: `${(hover / total) * 100}%` }}>{tc(hover / fps)}</span>}
    </div>
  );
}

/** Flou affiché tout de suite par-dessus la vidéo (pendant la lecture, en pause, en bougeant les réglages). Proche
 *  du rendu de l'export ; l'image exacte du moteur le remplace en pause. */
export function LiveBlur({ boxes, style, shape, strength }: { boxes: Box[]; style: string; shape: string; strength: number }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState<[number, number]>([0, 0]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize([el.clientWidth, el.clientHeight]));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  // comme l'export : la force dépend de la taille de la zone (20 cellules au plus léger, 4 au plus fort)
  const cells = 20 - (16 * Math.max(0, Math.min(100, strength))) / 100;
  return (
    <div className="cz-live" ref={ref}>
      {boxes.map((b, i) => {
        const w = (b[2] - b[0]) * size[0];
        const h = (b[3] - b[1]) * size[1];
        const r = Math.max(3, (1.3 * Math.min(w, h)) / cells);
        const whole = b[2] - b[0] > 0.98 && b[3] - b[1] > 0.98; // passage plein cadre : toujours rectangulaire
        return (
          <span
            key={i}
            className={clsx("cz-live-zone", `is-${style}`, shape === "ellipse" && !whole && "round")}
            style={{
              left: `${b[0] * 100}%`, top: `${b[1] * 100}%`, width: `${(b[2] - b[0]) * 100}%`, height: `${(b[3] - b[1]) * 100}%`,
              ...(style === "black" ? {} : { backdropFilter: `blur(${(style === "pixel" ? r * 0.7 : r).toFixed(1)}px)`, ["--cell" as any]: `${Math.max(4, r * 1.6)}px` }),
            }}
          />
        );
      })}
    </div>
  );
}
