/**
 * se7en sound design — synthesized with Web Audio (no audio files, instant, tiny).
 * Short, dry and low in the mix: they confirm actions without getting in the way.
 *
 *   success  two bright notes            (task finished)
 *   error    low falling pulse           (task failed)
 *   start    short rising blip           (added to the queue)
 *   tick     tiny click                  (tab change, interface feedback)
 *   drop     airy sweep                  (files / links dropped)
 *   update   three-note arpeggio         (update ready)
 *
 * playIntro() scores the launch animation itself (see Splash.tsx): cursor blips, one rising note per
 * resolution step over a filtered riser, an impact + chord when "se7en" turns crisp, a stereo whoosh
 * following the light sweep, typing ticks for the tagline, then a reverb tail into the app.
 */
export type SoundName = "success" | "error" | "start" | "tick" | "drop" | "update";

let ctx: AudioContext | null = null;
let volume = 0.6;
let enabled = { events: true, ui: true, intro: true };
const PREFS_KEY = "se7en.sound";

// The launch animation starts before the settings arrive: remember the last known preferences.
try {
  const saved = JSON.parse(localStorage.getItem(PREFS_KEY) || "null");
  if (saved) {
    volume = typeof saved.volume === "number" ? saved.volume : volume;
    enabled = { ...enabled, ...saved.enabled };
  }
} catch {
  /* storage unavailable */
}

export function configureSounds(opts: { volume?: number; events?: boolean; ui?: boolean; intro?: boolean }): void {
  if (opts.volume !== undefined) volume = Math.max(0, Math.min(1, opts.volume));
  enabled = { events: opts.events ?? enabled.events, ui: opts.ui ?? enabled.ui, intro: opts.intro ?? enabled.intro };
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ volume, enabled }));
  } catch {
    /* ignore */
  }
}

function audio(): AudioContext | null {
  try {
    if (!ctx) ctx = new AudioContext();
    if (ctx.state === "suspended") void ctx.resume();
    return ctx;
  } catch {
    return null;
  }
}

// Unlock audio on the first interaction (browser autoplay policy).
if (typeof window !== "undefined") {
  const unlock = () => {
    audio();
    window.removeEventListener("pointerdown", unlock);
    window.removeEventListener("keydown", unlock);
  };
  window.addEventListener("pointerdown", unlock);
  window.addEventListener("keydown", unlock);
}

function tone(a: BaseAudioContext, out: AudioNode, t0: number, freq: number, dur: number, type: OscillatorType, gain: number, glide?: number) {
  const o = a.createOscillator();
  const g = a.createGain();
  o.type = type;
  o.frequency.setValueAtTime(freq, t0);
  if (glide) o.frequency.exponentialRampToValueAtTime(glide, t0 + dur);
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.exponentialRampToValueAtTime(gain, t0 + Math.min(0.012, dur / 4));
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
  o.connect(g).connect(out);
  o.start(t0);
  o.stop(t0 + dur + 0.02);
}

function noise(a: BaseAudioContext, out: AudioNode, t0: number, dur: number, gain: number, from: number, to: number) {
  const len = Math.max(1, Math.floor(a.sampleRate * dur));
  const buf = a.createBuffer(1, len, a.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < len; i++) data[i] = Math.random() * 2 - 1;
  const src = a.createBufferSource();
  src.buffer = buf;
  const f = a.createBiquadFilter();
  f.type = "bandpass";
  f.Q.value = 1.2;
  f.frequency.setValueAtTime(from, t0);
  f.frequency.exponentialRampToValueAtTime(to, t0 + dur);
  const g = a.createGain();
  g.gain.setValueAtTime(0.0001, t0);
  g.gain.exponentialRampToValueAtTime(gain, t0 + dur * 0.3);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
  src.connect(f).connect(g).connect(out);
  src.start(t0);
  src.stop(t0 + dur + 0.02);
}

export function play(name: SoundName): void {
  const ui = name === "tick" || name === "drop" || name === "start";
  if (ui ? !enabled.ui : !enabled.events) return;
  if (volume <= 0) return;
  const a = audio();
  if (!a || a.state !== "running") return;
  const master = a.createGain();
  master.gain.value = volume * 0.9;
  // gentle high cut keeps everything soft
  const lp = a.createBiquadFilter();
  lp.type = "lowpass";
  lp.frequency.value = 7000;
  master.connect(lp).connect(a.destination);
  const t = a.currentTime + 0.005;
  switch (name) {
    case "success":
      tone(a, master, t, 1318.5, 0.16, "sine", 0.16);
      tone(a, master, t + 0.085, 1975.5, 0.32, "sine", 0.14);
      tone(a, master, t + 0.085, 3951, 0.18, "sine", 0.025);
      break;
    case "error":
      tone(a, master, t, 220, 0.16, "triangle", 0.2, 196);
      tone(a, master, t + 0.13, 164.8, 0.28, "triangle", 0.2, 146.8);
      break;
    case "start":
      tone(a, master, t, 880, 0.09, "triangle", 0.1, 1480);
      break;
    case "tick":
      noise(a, master, t, 0.018, 0.05, 5000, 3000);
      break;
    case "drop":
      noise(a, master, t, 0.32, 0.08, 400, 3200);
      tone(a, master, t + 0.12, 660, 0.16, "sine", 0.06, 990);
      break;
    case "update":
      [987.8, 1318.5, 1975.5].forEach((f, i) => tone(a, master, t + i * 0.09, f, 0.22, "sine", 0.12));
      break;
  }
}

/* ------------------------------------------------------------------ launch intro */

/** Moments of the launch animation, in ms from its start (Splash.tsx passes its own constants). */
export interface IntroTimeline {
  cursor: number[]; // red cursor blinks
  t0: number; // first resolution step
  step: number; // ms between steps
  steps: number;
  crisp: number; // "se7en" sharp: impact + light sweep
  sweep: number; // light sweep duration
  typeStart: number; // tagline typing
  typeEvery: number;
  chars: number;
  exit: number; // zoom-out into the app
  end: number;
}

export interface IntroHandle {
  stop(): void;
}

// A minor pentatonic, one note per resolution step (144p → 4K).
const STEP_NOTES = [220, 261.63, 293.66, 329.63, 392, 440, 523.25, 587.33, 659.25, 783.99, 880];
// A minor add9 for the impact.
const CHORD = [110, 164.81, 220, 261.63, 329.63, 493.88];

function impulse(a: BaseAudioContext, seconds: number, decay: number): AudioBuffer {
  const len = Math.floor(a.sampleRate * seconds);
  const buf = a.createBuffer(2, len, a.sampleRate);
  for (let ch = 0; ch < 2; ch++) {
    const d = buf.getChannelData(ch);
    for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, decay);
  }
  return buf;
}

/** Build the intro graph on any context (also used to render it offline in tests). */
export function scoreIntro(a: BaseAudioContext, tl: IntroTimeline): GainNode {
  const t = a.currentTime + 0.02;
  const at = (ms: number) => t + ms / 1000;
  const level = volume * 1.25;

  // master → soft high cut → gentle compressor (glue, no clipping) → speakers
  const master = a.createGain();
  master.gain.setValueAtTime(level, t);
  master.gain.setValueAtTime(level, at(tl.end));
  master.gain.linearRampToValueAtTime(0.0001, at(tl.end + 1400)); // rings a little into the app
  const lp = a.createBiquadFilter();
  lp.type = "lowpass";
  lp.frequency.value = 9500;
  const comp = a.createDynamicsCompressor();
  comp.threshold.value = -14;
  comp.knee.value = 6;
  comp.ratio.value = 4;
  comp.attack.value = 0.004;
  comp.release.value = 0.25;
  master.connect(lp).connect(comp).connect(a.destination);

  const verb = a.createConvolver();
  verb.buffer = impulse(a, 2.8, 3);
  verb.connect(master);
  const bus = (reverb: number, pan = 0): GainNode => {
    const g = a.createGain();
    const p = a.createStereoPanner();
    p.pan.value = pan;
    g.connect(p).connect(master);
    const send = a.createGain();
    send.gain.value = reverb;
    p.connect(send).connect(verb);
    return g;
  };

  // one shared noise buffer, played from random offsets
  const noiseBuf = a.createBuffer(1, a.sampleRate * 2, a.sampleRate);
  const nd = noiseBuf.getChannelData(0);
  for (let i = 0; i < nd.length; i++) nd[i] = Math.random() * 2 - 1;
  const noiseSrc = (start: number, dur: number) => {
    const src = a.createBufferSource();
    src.buffer = noiseBuf;
    src.loop = true;
    src.start(start, Math.random() * 1.5);
    src.stop(start + dur + 0.05);
    return src;
  };
  const burst = (when: number, dur: number, gain: number, filter: BiquadFilterType, hz: number, out: AudioNode) => {
    const f = a.createBiquadFilter();
    f.type = filter;
    f.frequency.value = hz;
    const g = a.createGain();
    g.gain.setValueAtTime(gain, when);
    g.gain.exponentialRampToValueAtTime(0.0001, when + dur);
    noiseSrc(when, dur).connect(f).connect(g).connect(out);
  };

  // 1. cursor blinks: small digital blips
  for (const ms of tl.cursor) {
    tone(a, bus(0.35), at(ms), 1760, 0.08, "sine", 0.2);
    tone(a, bus(0.2), at(ms), 3520, 0.04, "sine", 0.04);
  }

  // 2. riser under the resolution steps: detuned saws gliding up an octave through an opening filter
  {
    const f = a.createBiquadFilter();
    f.type = "lowpass";
    f.Q.value = 5;
    f.frequency.setValueAtTime(140, at(tl.t0));
    f.frequency.exponentialRampToValueAtTime(2600, at(tl.crisp - 30));
    const g = a.createGain();
    g.gain.setValueAtTime(0.0001, at(tl.t0));
    g.gain.exponentialRampToValueAtTime(0.1, at(tl.crisp - 60));
    g.gain.exponentialRampToValueAtTime(0.0001, at(tl.crisp)); // short breath before the impact
    f.connect(g).connect(bus(0.25));
    const voices: [number, OscillatorType][] = [[55, "sawtooth"], [55.35, "sawtooth"], [110.2, "square"]];
    for (const [hz, type] of voices) {
      const o = a.createOscillator();
      o.type = type;
      o.frequency.setValueAtTime(hz, at(tl.t0));
      o.frequency.exponentialRampToValueAtTime(hz * 2, at(tl.crisp));
      o.connect(f);
      o.start(at(tl.t0));
      o.stop(at(tl.crisp) + 0.05);
    }
    // airy noise riser on top
    const bp = a.createBiquadFilter();
    bp.type = "bandpass";
    bp.Q.value = 0.8;
    bp.frequency.setValueAtTime(500, at(tl.t0));
    bp.frequency.exponentialRampToValueAtTime(7000, at(tl.crisp));
    const ng = a.createGain();
    ng.gain.setValueAtTime(0.0001, at(tl.t0));
    ng.gain.exponentialRampToValueAtTime(0.045, at(tl.crisp - 40));
    ng.gain.exponentialRampToValueAtTime(0.0001, at(tl.crisp));
    noiseSrc(at(tl.t0), (tl.crisp - tl.t0) / 1000).connect(bp).connect(ng).connect(bus(0.3));
  }

  // 3. one rising "pixel" note per resolution step (144p … 4K), alternating left/right
  for (let i = 0; i < tl.steps; i++) {
    const when = at(tl.t0 + i * tl.step);
    const hz = STEP_NOTES[Math.min(i, STEP_NOTES.length - 1)];
    const out = bus(0.35, i % 2 ? 0.35 : -0.35);
    tone(a, out, when, hz * 2, 0.12, "square", 0.06 + i * 0.006, hz);
    tone(a, out, when, hz, 0.24, "triangle", 0.12 + i * 0.008);
    burst(when, 0.02, 0.08, "highpass", 4000, out);
  }

  // 4. impact when the wordmark is crisp: sub drop + noise hit + wide chord + shimmer
  {
    const hit = at(tl.crisp);
    tone(a, bus(0.1), hit, 150, 0.9, "sine", 0.55, 36);
    const nf = a.createBiquadFilter();
    nf.type = "lowpass";
    nf.frequency.setValueAtTime(5000, hit);
    nf.frequency.exponentialRampToValueAtTime(250, hit + 0.45);
    const ng = a.createGain();
    ng.gain.setValueAtTime(0.16, hit);
    ng.gain.exponentialRampToValueAtTime(0.0001, hit + 0.5);
    noiseSrc(hit, 0.5).connect(nf).connect(ng).connect(bus(0.6));

    CHORD.forEach((hz, i) => {
      const pg = a.createGain();
      pg.gain.setValueAtTime(0.0001, hit);
      pg.gain.exponentialRampToValueAtTime(1, hit + 0.02);
      pg.gain.exponentialRampToValueAtTime(0.55, hit + 0.6);
      pg.gain.exponentialRampToValueAtTime(0.4, at(tl.exit));
      pg.gain.setTargetAtTime(0.0001, at(tl.exit), 0.45); // natural release during the zoom-out
      const vf = a.createBiquadFilter();
      vf.type = "lowpass";
      vf.Q.value = 1.5;
      vf.frequency.setValueAtTime(4200, hit);
      vf.frequency.exponentialRampToValueAtTime(1100, hit + 2.4);
      vf.connect(pg).connect(bus(0.45, (i / (CHORD.length - 1)) * 1.2 - 0.6));
      for (const cents of [-7, 7]) {
        const o = a.createOscillator();
        o.type = "sawtooth";
        o.frequency.value = hz;
        o.detune.value = cents;
        const vg = a.createGain();
        vg.gain.value = 0.045;
        o.connect(vg).connect(vf);
        o.start(hit);
        o.stop(at(tl.end + 1450));
      }
    });
    [1760, 2637, 3520].forEach((hz, i) => tone(a, bus(0.8, [-0.5, 0.5, 0][i]), hit + 0.03 + i * 0.05, hz, 2.6, "sine", 0.035 - i * 0.008));
  }

  // 5. light sweep: whoosh travelling left → right with the white highlight
  {
    const s0 = at(tl.crisp);
    const d = tl.sweep / 1000;
    const bp = a.createBiquadFilter();
    bp.type = "bandpass";
    bp.Q.value = 1.4;
    bp.frequency.setValueAtTime(800, s0);
    bp.frequency.exponentialRampToValueAtTime(7500, s0 + d);
    const g = a.createGain();
    g.gain.setValueAtTime(0.0001, s0);
    g.gain.exponentialRampToValueAtTime(0.06, s0 + d * 0.5);
    g.gain.exponentialRampToValueAtTime(0.0001, s0 + d);
    const p = a.createStereoPanner();
    p.pan.setValueAtTime(-0.9, s0);
    p.pan.linearRampToValueAtTime(0.9, s0 + d);
    const send = a.createGain();
    send.gain.value = 0.4;
    noiseSrc(s0, d).connect(bp).connect(g).connect(p);
    p.connect(master);
    p.connect(send).connect(verb);
  }

  // 6. tagline typing: soft ticks (every other character)
  for (let i = 0; i < tl.chars; i += 2) {
    burst(at(tl.typeStart + i * tl.typeEvery), 0.012, 0.03, "highpass", 3500, bus(0.2, i % 4 ? 0.15 : -0.15));
  }

  // 7. exit into the app: low whoosh down
  {
    const x0 = at(tl.exit);
    const d = (tl.end - tl.exit) / 1000;
    const f = a.createBiquadFilter();
    f.type = "lowpass";
    f.frequency.setValueAtTime(2600, x0);
    f.frequency.exponentialRampToValueAtTime(180, x0 + d);
    const g = a.createGain();
    g.gain.setValueAtTime(0.0001, x0);
    g.gain.exponentialRampToValueAtTime(0.05, x0 + d * 0.25);
    g.gain.exponentialRampToValueAtTime(0.0001, x0 + d);
    noiseSrc(x0, d).connect(f).connect(g).connect(bus(0.5));
  }
  return master;
}

/**
 * Score the launch animation. Returns a handle to fade it out when the animation is skipped;
 * null when disabled or when audio is not allowed yet (it never starts late and out of sync).
 */
export function playIntro(tl: IntroTimeline): IntroHandle | null {
  if (!enabled.intro || volume <= 0) return null;
  const a = audio();
  if (!a) return null;
  let out: GainNode | null = null;
  let cancelled = false;
  const begin = () => {
    if (!cancelled && a.state === "running" && !out) out = scoreIntro(a, tl);
  };
  if (a.state === "running") begin();
  else {
    const asked = performance.now();
    void a
      .resume()
      .then(() => performance.now() - asked < 250 && begin())
      .catch(() => undefined);
  }
  return {
    stop() {
      cancelled = true;
      const o = out;
      if (!o) return;
      const now = a.currentTime;
      o.gain.cancelScheduledValues(now);
      o.gain.setValueAtTime(Math.max(0.0001, o.gain.value), now);
      o.gain.linearRampToValueAtTime(0.0001, now + 0.35);
      window.setTimeout(() => o.disconnect(), 600);
    },
  };
}
