import clsx from "clsx";
import {
  Loader2,
  Maximize,
  Minimize,
  Music2,
  Pause,
  Play,
  Repeat,
  SkipBack,
  SkipForward,
  Volume1,
  Volume2,
  VolumeX,
} from "lucide-react";
import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { thumbUrl } from "../api/client";
import { usePlayback } from "../hooks/usePlayback";
import { useT } from "../i18n";
import { duration as fmt } from "../lib/format";
import { ErrorAlert, Ticks } from "./ui";

const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2];
const LS_VOL = "se7en.volume";

function readVolume(): number {
  try {
    const v = Number(localStorage.getItem(LS_VOL));
    return Number.isFinite(v) && localStorage.getItem(LS_VOL) !== null ? Math.max(0, Math.min(1, v)) : 0.9;
  } catch {
    return 0.9;
  }
}

/** Full media player (video, audio, image) in the se7en style. */
export function MediaPlayer({
  path,
  cover,
  onEnded,
  onPrev,
  onNext,
  autoPlay = true,
  active = true,
  extra,
}: {
  path: string;
  cover?: string | null;
  onEnded?: () => void;
  onPrev?: () => void;
  onNext?: () => void;
  autoPlay?: boolean;
  active?: boolean;
  extra?: ReactNode;
}) {
  const t = useT();
  const pb = usePlayback(path, "player");
  const { plan, error, src, mediaRef, time, duration, playing, buffered, waiting, seek, toggle } = pb;
  const [volume, setVolume] = useState(readVolume);
  const [muted, setMuted] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [loop, setLoop] = useState(false);
  const [full, setFull] = useState(false);
  const [hover, setHover] = useState<number | null>(null);
  const [idle, setIdle] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const analyser = useRef<{ ctx: AudioContext; node: AnalyserNode } | null>(null);
  const isAudio = plan?.kind === "audio";
  const isImage = plan?.mode === "image";

  useEffect(() => {
    const el = mediaRef.current;
    if (!el) return;
    el.volume = volume;
    el.muted = muted;
    el.playbackRate = speed;
    el.loop = loop;
    try {
      localStorage.setItem(LS_VOL, String(volume));
    } catch {
      /* ignore */
    }
  }, [volume, muted, speed, loop, src, mediaRef]);

  // autoplay once the file is ready
  useEffect(() => {
    const el = mediaRef.current;
    if (!el || !autoPlay || isImage) return;
    const go = () => el.play().catch(() => undefined);
    el.addEventListener("loadeddata", go, { once: true });
    return () => el.removeEventListener("loadeddata", go);
  }, [src, autoPlay, isImage, mediaRef]);

  useEffect(() => {
    const el = mediaRef.current;
    if (!el || !onEnded) return;
    el.addEventListener("ended", onEnded);
    return () => el.removeEventListener("ended", onEnded);
  }, [onEnded, mediaRef, src]);

  // Audio visualizer (created on the first play: audio contexts need a user gesture)
  useEffect(() => {
    if (!isAudio || !playing || analyser.current) return;
    const el = mediaRef.current;
    if (!el) return;
    try {
      const ctx = new AudioContext();
      const node = ctx.createAnalyser();
      node.fftSize = 256;
      node.smoothingTimeConstant = 0.78;
      ctx.createMediaElementSource(el).connect(node);
      node.connect(ctx.destination);
      void ctx.resume();
      analyser.current = { ctx, node };
    } catch {
      /* visualizer is optional */
    }
  }, [isAudio, playing, mediaRef]);
  useEffect(
    () => () => {
      // each file gets a new <video>: its visualizer is rebuilt on the next play
      void analyser.current?.ctx.close().catch(() => undefined);
      analyser.current = null;
    },
    [path],
  );
  useEffect(() => {
    if (!isAudio) return;
    let raf = 0;
    const draw = () => {
      raf = requestAnimationFrame(draw);
      const c = canvasRef.current;
      const a = analyser.current;
      if (!c) return;
      const g = c.getContext("2d");
      if (!g) return;
      const w = (c.width = c.clientWidth * devicePixelRatio);
      const h = (c.height = c.clientHeight * devicePixelRatio);
      g.clearRect(0, 0, w, h);
      const bars = 48;
      const data = new Uint8Array(a ? a.node.frequencyBinCount : bars);
      if (a) a.node.getByteFrequencyData(data);
      const bw = w / bars;
      for (let i = 0; i < bars; i++) {
        const v = a ? data[Math.floor((i / bars) * data.length * 0.8)] / 255 : 0.04;
        const bh = Math.max(2 * devicePixelRatio, v * h);
        g.fillStyle = i % 7 === 6 ? "#f22b2b" : `rgba(244,243,241,${0.25 + v * 0.7})`;
        g.fillRect(i * bw + bw * 0.18, h - bh, bw * 0.64, bh);
      }
    };
    draw();
    return () => cancelAnimationFrame(raf);
  }, [isAudio]);

  const fullscreen = useCallback(() => {
    const box = boxRef.current;
    if (!box) return;
    if (document.fullscreenElement) void document.exitFullscreen();
    else void box.requestFullscreen().catch(() => undefined);
  }, []);
  useEffect(() => {
    const on = () => setFull(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", on);
    return () => document.removeEventListener("fullscreenchange", on);
  }, []);

  // hide the controls while watching
  useEffect(() => {
    if (!playing || isAudio) {
      setIdle(false);
      return;
    }
    let h = window.setTimeout(() => setIdle(true), 2600);
    const wake = () => {
      setIdle(false);
      window.clearTimeout(h);
      h = window.setTimeout(() => setIdle(true), 2600);
    };
    const box = boxRef.current;
    box?.addEventListener("mousemove", wake);
    return () => {
      window.clearTimeout(h);
      box?.removeEventListener("mousemove", wake);
    };
  }, [playing, isAudio]);

  // leaving the page: videos pause, music keeps playing in the background
  useEffect(() => {
    if (!active && !isAudio) mediaRef.current?.pause();
  }, [active, isAudio, mediaRef]);

  // keyboard (only while this player is on screen)
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      const tg = e.target as HTMLElement;
      if (tg && (tg.tagName === "INPUT" || tg.tagName === "TEXTAREA" || tg.isContentEditable) || e.ctrlKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === " " || k === "k") {
        e.preventDefault();
        toggle();
      } else if (k === "arrowright") seek(time + 5);
      else if (k === "arrowleft") seek(time - 5);
      else if (k === "l") seek(time + 10);
      else if (k === "j") seek(time - 10);
      else if (k === "arrowup") {
        e.preventDefault();
        setVolume((v) => Math.min(1, v + 0.05));
      } else if (k === "arrowdown") {
        e.preventDefault();
        setVolume((v) => Math.max(0, v - 0.05));
      } else if (k === "m") setMuted((m) => !m);
      else if (k === "f") fullscreen();
      else if (k === "n" && onNext) onNext();
      else if (k === "p" && onPrev) onPrev();
      else if (/^[0-9]$/.test(k) && duration) seek((Number(k) / 10) * duration);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [active, toggle, seek, time, fullscreen, onNext, onPrev, duration]);

  const pct = duration ? (time / duration) * 100 : 0;
  const bpct = duration ? (Math.min(duration, buffered) / duration) * 100 : 0;
  const onBar = (e: React.MouseEvent<HTMLDivElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)) * duration;
  };
  const VolIcon = muted || volume === 0 ? VolumeX : volume < 0.5 ? Volume1 : Volume2;

  if (error) return <ErrorAlert error={error} />;

  return (
    <div ref={boxRef} className={clsx("player", isAudio && "player-audio", idle && "idle", full && "full")}>
      <div className="player-stage" onClick={() => !isImage && toggle()} onDoubleClick={fullscreen}>
        {!plan && <Loader2 className="spin faint" size={22} />}
        {isImage && src && <img src={src} alt="" className="player-image" draggable={false} />}
        {plan && !isImage && (
          <video ref={mediaRef} key={path} src={src} className={clsx("player-video", isAudio && "hidden")} playsInline preload="auto" />
        )}
        {isAudio && (
          <div className="player-audio-art">
            <div className="player-cover">{cover ? <img src={thumbUrl(cover)} alt="" /> : <Music2 size={42} />}</div>
            <div className="player-audio-meta">
              <div className="eyebrow">{plan?.artist || plan?.album || t("player.nowPlaying")}</div>
              <div className="player-audio-title selectable">{plan?.title || plan?.name}</div>
              <canvas ref={canvasRef} className="player-viz" />
            </div>
          </div>
        )}
        {waiting && playing && <div className="player-wait"><Loader2 className="spin" size={26} /></div>}
        {plan && !isImage && !playing && !isAudio && (
          <div className="player-bigplay"><Play size={30} fill="currentColor" /></div>
        )}
        <Ticks inset={12} />
      </div>
      {!isImage && (
        <div className="player-controls" onClick={(e) => e.stopPropagation()}>
          <div
            className="player-bar"
            onMouseMove={(e) => setHover(onBar(e))}
            onMouseLeave={() => setHover(null)}
            onClick={(e) => seek(onBar(e))}
          >
            <div className="player-bar-buffer" style={{ width: `${bpct}%` }} />
            <div className="player-bar-fill" style={{ width: `${pct}%` }} />
            <div className="player-bar-knob" style={{ left: `${pct}%` }} />
            {hover !== null && (
              <div className="player-bar-hover" style={{ left: `${(hover / (duration || 1)) * 100}%` }}>
                {fmt(hover)}
              </div>
            )}
          </div>
          <div className="player-row">
            {onPrev && <button className="pbtn" onClick={onPrev} data-tip={t("player.prev")}><SkipBack size={16} /></button>}
            <button className="pbtn pbtn-main" onClick={toggle} data-tip={playing ? t("player.pause") : t("player.play")}>
              {playing ? <Pause size={18} fill="currentColor" /> : <Play size={18} fill="currentColor" />}
            </button>
            {onNext && <button className="pbtn" onClick={onNext} data-tip={t("player.next")}><SkipForward size={16} /></button>}
            <span className="player-time tabular">
              {fmt(time)} <span className="faint">/ {fmt(duration)}</span>
            </span>
            <span className="spacer" />
            {extra}
            <div className="player-vol">
              <button className="pbtn" onClick={() => setMuted(!muted)} data-tip={t("player.mute")}><VolIcon size={16} /></button>
              <input type="range" min={0} max={1} step={0.01} value={muted ? 0 : volume} onChange={(e) => { setMuted(false); setVolume(Number(e.target.value)); }} />
            </div>
            <button
              className="pbtn mono"
              style={{ width: "auto", padding: "0 8px", fontSize: 11 }}
              data-tip={t("player.speed")}
              onClick={() => setSpeed(SPEEDS[(SPEEDS.indexOf(speed) + 1) % SPEEDS.length])}
            >
              {speed}×
            </button>
            <button className={clsx("pbtn", loop && "on")} onClick={() => setLoop(!loop)} data-tip={t("player.loop")}><Repeat size={15} /></button>
            {!isAudio && (
              <button className="pbtn" onClick={fullscreen} data-tip={t("player.fullscreen")}>
                {full ? <Minimize size={15} /> : <Maximize size={15} />}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
