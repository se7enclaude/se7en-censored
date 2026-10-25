import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, localMediaUrl, mediaUrl } from "../api/client";
import type { PlaybackPlan } from "../api/types";

type Mode = "direct" | "remux" | "transcode";

/**
 * Plays any local file in a <video>/<audio> element:
 *  - "direct": the file itself (Range requests, instant seeking);
 *  - "remux" / "transcode": a live FFmpeg stream for formats Chromium cannot read
 *    (MKV, AVI, HEVC, WMA...). Seeking far restarts the stream at the new position.
 * If the direct attempt fails (codec missing on this PC), the backend's fallback is used.
 */
export function usePlayback(path: string | null, sid = "player") {
  const [plan, setPlan] = useState<PlaybackPlan | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [mode, setMode] = useState<Mode>("direct");
  const [offset, setOffset] = useState(0);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [buffered, setBuffered] = useState(0);
  const [waiting, setWaiting] = useState(false);
  const mediaRef = useRef<HTMLVideoElement | null>(null);
  const resume = useRef(false);

  useEffect(() => {
    setPlan(null);
    setError(null);
    setOffset(0);
    setTime(0);
    setPlaying(false);
    if (!path) return;
    let alive = true;
    api
      .get<PlaybackPlan>(`/api/media/plan?path=${encodeURIComponent(path)}`)
      .then((p) => {
        if (!alive) return;
        setPlan(p);
        setMode(p.mode === "image" ? "direct" : (p.mode as Mode));
      })
      .catch((e) => alive && setError(e));
    return () => {
      alive = false;
    };
  }, [path]);

  useEffect(() => () => void api.post(`/api/media/stop?sid=${sid}`).catch(() => undefined), [sid]);

  const src =
    !plan || !path
      ? undefined
      : plan.mode === "image"
        ? localMediaUrl(path)
        : mode === "direct"
          ? localMediaUrl(path)
          : mediaUrl(`/api/media/stream?path=${encodeURIComponent(path)}&mode=${mode}&t=${offset.toFixed(2)}&sid=${sid}`);

  const duration = plan?.duration ?? 0;

  // element events -> state (absolute time = stream offset + element time)
  useEffect(() => {
    const el = mediaRef.current;
    if (!el) return;
    const onTime = () => setTime(offset + el.currentTime);
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onProgress = () => {
      try {
        const b = el.buffered;
        setBuffered(b.length ? offset + b.end(b.length - 1) : offset);
      } catch {
        /* ignore */
      }
    };
    const onWaiting = () => setWaiting(true);
    const onCanPlay = () => {
      setWaiting(false);
      if (resume.current) {
        resume.current = false;
        el.play().catch(() => undefined);
      }
    };
    const onError = () => {
      if (plan?.fallback && mode === "direct") {
        // Chromium cannot decode this file on this PC: stream it through FFmpeg instead.
        resume.current = true;
        setOffset(el.currentTime || 0);
        setMode(plan.fallback);
      } else if (plan?.fallback === "transcode" && mode === "remux") {
        resume.current = true;
        setMode("transcode");
      } else {
        setError(new ApiError({ code: "playback_failed", message: "This file cannot be played." }, 0));
      }
    };
    el.addEventListener("timeupdate", onTime);
    el.addEventListener("play", onPlay);
    el.addEventListener("pause", onPause);
    el.addEventListener("progress", onProgress);
    el.addEventListener("waiting", onWaiting);
    el.addEventListener("canplay", onCanPlay);
    el.addEventListener("error", onError);
    return () => {
      el.removeEventListener("timeupdate", onTime);
      el.removeEventListener("play", onPlay);
      el.removeEventListener("pause", onPause);
      el.removeEventListener("progress", onProgress);
      el.removeEventListener("waiting", onWaiting);
      el.removeEventListener("canplay", onCanPlay);
      el.removeEventListener("error", onError);
    };
  }, [offset, mode, plan, src]);

  const seek = useCallback(
    (t: number) => {
      const el = mediaRef.current;
      if (!el) return;
      const target = Math.max(0, Math.min(duration || Infinity, t));
      if (mode === "direct") {
        el.currentTime = target;
        setTime(target);
        return;
      }
      // Inside what the stream already delivered: seek locally; otherwise restart FFmpeg there.
      const local = target - offset;
      let inBuffer = false;
      try {
        for (let i = 0; i < el.buffered.length; i++) if (local >= el.buffered.start(i) && local <= el.buffered.end(i) - 0.3) inBuffer = true;
      } catch {
        /* ignore */
      }
      if (inBuffer) {
        el.currentTime = local;
        setTime(target);
      } else {
        resume.current = !el.paused;
        setOffset(target);
        setTime(target);
      }
    },
    [mode, offset, duration],
  );

  const toggle = useCallback(() => {
    const el = mediaRef.current;
    if (!el) return;
    if (el.paused) el.play().catch(() => undefined);
    else el.pause();
  }, []);

  return { plan, error, mode, src, mediaRef, time, duration, playing, buffered, waiting, seek, toggle, offset };
}
