import clsx from "clsx";
import { ArrowLeft, Check as CheckIcon, ChevronLeft, ChevronRight, Eye, EyeOff, FolderOpen, Link2, Pause, Pin, Play, Plus, RefreshCw, ShieldCheck, SkipBack, SkipForward, Square, Trash2, Undo2, Upload, Video, WandSparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, localMediaUrl, mediaUrl } from "../api/client";
import { events } from "../api/ws";
import { Button, Empty, ErrorAlert, PageHead, Progress, Segmented, Switch, Ticks } from "../components/ui";
import { useFileIntake } from "../hooks/useFileIntake";
import { duration as fmtDuration } from "../lib/format";
import { useApp } from "../store/app";
import "../styles/clip.css";
import "../styles/censor.css";

type Style = "blur" | "pixel" | "black";
type Shape = "ellipse" | "rect";
type Box = [number, number, number, number];

interface Options {
  classes: string[];
  sensitivity: number;
  margin: number;
  style: Style;
  shape: Shape;
  strength: number;
  quality: "full" | "fast";
  careful: boolean;
  verify?: boolean; // 2e vérification (CLIP) des zones trouvées
}

interface Project {
  id: string;
  name: string;
  status: "new" | "queued" | "analyzing" | "review" | "exporting" | "done" | "error" | "paused";
  step: string | null;
  error: { code?: string; message: string; hint?: string } | null;
  notes: string[];
  source: { url: string | null; path: string | null };
  video: { duration: number; width: number; height: number; fps: number } | null;
  options: Options;
  manual: Manual[];
  hidden: { cls: string; f0: number; f1: number }[];
  export: { file: string; size: number; seconds: number; blurred_frames: number } | null;
  stats: { zones?: number; seconds?: number; analysis_seconds?: number };
  detect?: { v?: number; fps: number; frames: number; seconds: number; gpu: boolean; refined?: number };
}

interface Summary {
  id: string;
  name: string;
  status: Project["status"];
  duration: number | null;
  zones: number | null;
  thumb: string | null;
  url: string | null;
  exported: boolean;
}

interface Track {
  id: string;
  cls: string;
  f0: number;
  f1: number;
  score: number;
  hits: number;
  boxes: Box[];
  hidden: boolean;
}

interface Manual {
  id: string;
  keys: { f: number; box: Box }[];
  fixed?: boolean; // posée telle quelle, sans suivre le mouvement
}

const FIXED_S = 2; // durée d'une zone fixe à sa pose (« Finir ici » pour l'arrêter ou l'allonger)

interface Zones {
  fps: number;
  frames: number;
  tracks: Track[];
  manual: Manual[];
  hidden: { cls: string; f0: number; f1: number }[];
  cuts: number[]; // premières images des nouveaux plans (2e passe)
  refined: boolean;
  stats: { zones: number; seconds: number };
}

interface Live {
  percent: number;
  label: string;
  eta: number | null;
  op: string;
}

const CLASSES: { id: string; label: string }[] = [
  { id: "FEMALE_BREAST_EXPOSED", label: "Poitrines" },
  { id: "FEMALE_GENITALIA_EXPOSED", label: "Sexe féminin" },
  { id: "MALE_GENITALIA_EXPOSED", label: "Sexe masculin" },
  { id: "BUTTOCKS_EXPOSED", label: "Fesses" },
  { id: "ANUS_EXPOSED", label: "Anus" },
  { id: "MALE_BREAST_EXPOSED", label: "Torse masculin" },
];
const CLASS_LABEL = Object.fromEntries(CLASSES.map((c) => [c.id, c.label]));
const CLASS_COLOR: Record<string, string> = {
  FEMALE_BREAST_EXPOSED: "#F1B9B2",
  FEMALE_GENITALIA_EXPOSED: "#FF7A90",
  MALE_GENITALIA_EXPOSED: "#7FC8D2",
  BUTTOCKS_EXPOSED: "#F0C467",
  ANUS_EXPOSED: "#C9A0FF",
  MALE_BREAST_EXPOSED: "#9FD9B5",
  manual: "#FFFFFF",
};

const isUrl = (s: string) => /^https?:\/\//i.test(s.trim());
const cleanPath = (s: string) => s.trim().replace(/^"(.*)"$/, "$1");
const isPath = (s: string) => /^([A-Za-z]:\\|\\\\)[^<>|*?]+\.\w{2,5}$/.test(cleanPath(s));
const isSource = (s: string) => isUrl(s) || isPath(s);
const fileName = (s: string) => cleanPath(s).split(/[\\/]/).pop() ?? s;
const running = (s: string) => s === "queued" || s === "analyzing";

function eta(sec: number | null | undefined) {
  if (sec == null || !isFinite(sec) || sec <= 0) return "";
  return sec < 90 ? `~${Math.round(sec)} s` : `~${Math.round(sec / 60)} min`;
}

function clock(sec: number) {
  const s = Math.max(0, sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = Math.floor(s % 60);
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(r).padStart(2, "0")}` : `${m}:${String(r).padStart(2, "0")}`;
}

/** se7en Censored : importer une vidéo, laisser l'IA trouver les zones intimes, vérifier, exporter floutée. */
export function CensorPage() {
  const toast = useApp((s) => s.toast);
  const [projects, setProjects] = useState<Summary[]>([]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [live, setLive] = useState<Record<string, Live>>({});

  const refresh = useCallback(() => {
    api.get<{ projects: Summary[] }>("/api/censor/projects").then((r) => setProjects(r.projects)).catch(() => undefined);
  }, []);

  useEffect(() => {
    refresh();
    return events.on((ev) => {
      if (ev.type === "censor_progress") {
        const d = ev.data;
        setLive((l) => ({ ...l, [d.project]: { percent: d.percent, label: d.label, eta: d.eta, op: d.op } }));
      } else if (ev.type === "censor_project") refresh();
    });
  }, [refresh]);

  if (openId) return <ProjectView id={openId} live={live[openId]} onBack={() => { setOpenId(null); refresh(); }} />;

  return (
    <div className="page-inner wide">
      <PageHead eyebrow="se7en Censored" title="Floutage auto" echo="pour Twitch" sub="Importez un film ou une vidéo : l'IA repère les parties intimes visibles et les floute, uniquement là où il faut." />
      <NewProject
        onCreated={(p) => {
          refresh();
          setOpenId(p.id);
        }}
        onError={(e) => toast({ kind: "error", title: e.message, body: e.hint })}
      />
      <div className="label" style={{ margin: "28px 0 12px" }}>Projets</div>
      {projects.length === 0 ? (
        <Empty icon={<ShieldCheck size={22} />} title="Aucun projet pour l'instant" body="Glissez une vidéo ci-dessus, choisissez-la ou collez son lien : l'analyse démarre tout de suite." />
      ) : (
        <div className="clip-projects">
          {projects.map((p) => {
            const l = live[p.id];
            return (
              <button key={p.id} className="clip-project card" onClick={() => setOpenId(p.id)}>
                <div className="clip-project-thumb">{p.thumb ? <img src={localMediaUrl(p.thumb)} alt="" /> : <Video size={20} />}</div>
                <div className="clip-project-body">
                  <div className="ellipsis" style={{ fontWeight: 600 }} title={p.name}>{p.name}</div>
                  <div className="mono faint" style={{ fontSize: 11 }}>
                    {[p.duration ? fmtDuration(p.duration) : null, p.url ? new URL(p.url).hostname.replace(/^www\./, "") : "fichier", p.zones != null ? `${p.zones} zones` : null].filter(Boolean).join(" · ")}
                  </div>
                  {running(p.status) || p.status === "exporting" ? (
                    <div style={{ marginTop: 8 }}>
                      <Progress value={l?.percent ?? null} state="active" />
                      <div className="faint ellipsis" style={{ fontSize: 11, marginTop: 4 }}>{l?.label ?? "En attente…"}</div>
                    </div>
                  ) : (
                    <span className={clsx("clip-status", `is-${p.status === "done" || p.status === "review" ? "ready" : p.status}`)}>{statusText(p.status)}</span>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function statusText(s: string) {
  return ({ review: "À vérifier", done: "Exportée", exporting: "Export…", error: "Erreur", paused: "Arrêté", new: "Nouveau", queued: "En attente", analyzing: "Analyse…" } as Record<string, string>)[s] ?? s;
}

// ------------------------------------------------------------------ nouveau projet
function NewProject({ onCreated, onError }: { onCreated: (p: Project) => void; onError: (e: any) => void }) {
  const [source, setSource] = useState("");
  const [quality, setQuality] = useState<"full" | "fast">("full");
  const [careful, setCareful] = useState(false);
  const [sending, setSending] = useState(false);

  const create = async (src: string) => {
    if (!src.trim()) return;
    setSending(true);
    try {
      const r = await api.post<{ project: Project }>("/api/censor/projects", { source: isUrl(src) ? src.trim() : cleanPath(src), options: { quality, careful } });
      setSource("");
      onCreated(r.project);
    } catch (e) {
      onError(e);
    } finally {
      setSending(false);
    }
  };

  const intake = useFileIntake({
    page: "censor",
    kind: "video",
    onFiles: (fs) => {
      const vids = fs.filter((f) => f.kind === "video");
      if (vids.length === 1) setSource(vids[0].path);
      else vids.forEach((v) => create(v.path));
    },
  });

  const pending = useApp((s) => s.pendingUrls);
  useEffect(() => {
    const u = pending?.length ? useApp.getState().consumeUrls() : null;
    if (u?.length) setSource(u[0]);
  }, [pending]);
  const local = isPath(source);
  const [typing, setTyping] = useState(false);

  return (
    <div className="clip-hero card rise">
      <Ticks inset={12} />
      <input {...intake.inputProps} />
      <div className="clip-hero-input">
        {local ? <Video size={18} className="accent-text" /> : <Link2 size={18} className="faint" />}
        <input
          className="clip-url"
          placeholder="Glissez une vidéo, choisissez-la, ou collez son chemin ou un lien"
          value={local && !typing ? fileName(source) : source}
          title={local ? cleanPath(source) : undefined}
          onChange={(e) => setSource(e.target.value)}
          onFocus={(e) => { setTyping(true); if (local) e.target.select(); }}
          onBlur={() => setTyping(false)}
          onKeyDown={(e) => e.key === "Enter" && isSource(source) && create(source)}
          spellCheck={false}
        />
        <Button variant={local ? "ghost" : "outline"} icon={<Upload size={15} />} onClick={intake.pick}>
          {local ? "Changer" : "Choisir une vidéo"}
        </Button>
        <Button variant="primary" icon={<WandSparkles size={16} />} loading={sending} disabled={!isSource(source)} onClick={() => create(source)}>
          Analyser
        </Button>
      </div>
      <div className="clip-options">
        <div className="clip-opt">
          <span className="label">Analyse</span>
          <Segmented
            size="sm"
            value={quality}
            onChange={setQuality}
            options={[
              { value: "full", label: "Complète", tip: "Toutes les images : rien ne passe entre deux (recommandé)" },
              { value: "fast", label: "Rapide", tip: "Une image sur deux, deux fois plus vite ; les trous sont comblés" },
            ]}
          />
        </div>
        <div className="clip-opt">
          <span className="label">Mode prudent</span>
          <label className="row clip-check" data-tip="Analyse aussi chaque image retournée : quelques zones de plus trouvées, deux fois plus long">
            <Switch checked={careful} onChange={setCareful} />
            <span>{careful ? "Oui" : "Non"}</span>
          </label>
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ un projet
function ProjectView({ id, live, onBack }: { id: string; live?: Live; onBack: () => void }) {
  const toast = useApp((s) => s.toast);
  const [p, setP] = useState<Project | null>(null);
  const [zones, setZones] = useState<Zones | null>(null);
  const load = useCallback(
    () =>
      api
        .get<Project>(`/api/censor/projects/${id}`)
        .then((proj) => {
          setP(proj);
          if (["review", "done", "exporting"].includes(proj.status)) api.get<Zones>(`/api/censor/projects/${id}/zones`).then(setZones).catch(() => undefined);
        })
        .catch((e) => toast({ kind: "error", title: e.message })),
    [id, toast],
  );

  useEffect(() => {
    load();
    return events.on((ev) => {
      if ((ev.type === "censor_project" && ev.data?.project === id) || (ev.type === "job" && ev.data?.kind === "censor" && ["completed", "failed"].includes(ev.data?.status))) load();
    });
  }, [id, load]);

  const act = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      load();
    } catch (e: any) {
      toast({ kind: "error", title: e.message, body: e.hint });
    }
  };

  if (!p) return <div className="page-inner wide" />;
  const analyzing = running(p.status);
  const exporting = p.status === "exporting";
  const reviewable = ["review", "done", "exporting"].includes(p.status) && zones;

  return (
    <div className="page-inner wide">
      <PageHead
        eyebrow={
          <button className="clip-back" onClick={onBack}>
            <ArrowLeft size={13} /> Projets
          </button>
        }
        title={<span className="ellipsis" style={{ maxWidth: 760, display: "inline-block", verticalAlign: "bottom" }}>{p.name}</span>}
        sub={[p.video ? fmtDuration(p.video.duration) : null, p.video ? `${p.video.width}×${p.video.height}` : null, p.detect?.seconds ? `analysé en ${fmtDuration(p.detect.seconds)}${p.detect.gpu ? "" : " (sans carte graphique)"}` : null].filter(Boolean).join(" · ")}
        right={
          <>
            {analyzing || exporting ? (
              <Button icon={<Square size={14} />} onClick={() => act(() => api.post(`/api/censor/projects/${id}/cancel`))}>Arrêter</Button>
            ) : (
              <>
                {p.export?.file && <Button icon={<FolderOpen size={15} />} onClick={() => act(() => api.post(`/api/censor/projects/${id}/open-folder`))}>Dossier</Button>}
                {(p.status === "paused" || p.status === "error") && (
                  <Button variant="primary" icon={<RefreshCw size={15} />} onClick={() => act(() => api.post(`/api/censor/projects/${id}/analyze`))}>{p.status === "paused" ? "Reprendre l'analyse" : "Relancer"}</Button>
                )}
              </>
            )}
            <Button
              variant="ghost"
              iconOnly
              icon={<Trash2 size={15} />}
              tip="Supprimer le projet (la vidéo exportée reste dans son dossier)"
              onClick={() => act(async () => { await api.del(`/api/censor/projects/${id}`); onBack(); })}
            />
          </>
        }
      />

      {analyzing && (
        <div className="card card-pad rise clip-run">
          <div className="clip-steps">
            {[...(p.source.url ? [{ id: "download", label: "Téléchargement" }] : []), { id: "detect", label: "Analyse des images" }, { id: "verify", label: "2e passe" }, { id: "review", label: "Vérification" }].map((s, k, all) => {
              const cur = all.findIndex((x) => x.id === (p.step || "detect"));
              return (
                <div key={s.id} className={clsx("clip-step", k < cur && "done", k === cur && "on")}>
                  <span className="clip-step-dot">{k < cur ? <CheckIcon size={11} strokeWidth={3} /> : null}</span>
                  {s.label}
                </div>
              );
            })}
          </div>
          <Progress value={live?.op === "analyze" ? live.percent : null} state="active" large showValue />
          <div className="row" style={{ marginTop: 8 }}>
            <span className="faint ellipsis">{live?.op === "analyze" ? live.label : p.status === "queued" ? "En attente…" : "Démarrage…"}</span>
            <span className="spacer" />
            <span className="mono faint">{live?.op === "analyze" ? eta(live.eta) : ""}</span>
          </div>
          <p className="faint" style={{ marginTop: 14, fontSize: 12 }}>
            Vous pouvez arrêter et reprendre plus tard : l'analyse repart là où elle s'était arrêtée.
          </p>
        </div>
      )}

      {p.error && !analyzing && <ErrorAlert error={{ code: p.error.code ?? "error", message: p.error.message, hint: p.error.hint }} />}
      {reviewable && p.detect && (p.detect.v ?? 1) < 2 && (
        <div className="clip-hint" style={{ marginBottom: 12 }}>
          <span>Analysé avec une version précédente : relancez l'analyse pour profiter du suivi visuel des zones (moins de trous dans le flou). Vos zones à la main sont gardées.</span>
          <Button size="sm" variant="primary" icon={<RefreshCw size={14} />} onClick={() => act(() => api.post(`/api/censor/projects/${id}/analyze`))}>Réanalyser</Button>
        </div>
      )}
      {reviewable && p.detect && (p.detect.v ?? 1) >= 2 && !p.detect.refined && (
        <div className="clip-hint" style={{ marginBottom: 12 }}>
          <span>Nouveau : la 2e passe revoit les moments où l'IA a vu de la nudité. Elle écarte ce qui n'en est pas (pecs, verre, bras), trouve les parties petites ou lointaines et coupe les zones aux changements de plan. Quelques minutes, sans refaire l'analyse ; vos zones à la main sont gardées.</span>
          <Button size="sm" variant="primary" icon={<ShieldCheck size={14} />} onClick={() => act(() => api.post(`/api/censor/projects/${id}/analyze`))}>Lancer la 2e passe</Button>
        </div>
      )}
      {p.notes?.map((n) => (
        <div key={n} className="clip-hint" style={{ marginBottom: 12 }}>{n}</div>
      ))}

      {reviewable && p.video && zones && <Review project={p} zones={zones} live={live} exporting={exporting} onChange={load} onError={(e) => toast({ kind: "error", title: e.message, body: e.hint })} />}
    </div>
  );
}

// ------------------------------------------------------------------ vérification
function Review({ project: p, zones: z, live, exporting, onChange, onError }: { project: Project; zones: Zones; live?: Live; exporting: boolean; onChange: () => void; onError: (e: any) => void }) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [canPlay, setCanPlay] = useState(true);
  const [preview, setPreview] = useState(true);
  const [stillUrl, setStillUrl] = useState<string | null>(null);
  const [opts, setOpts] = useState<Options>(p.options);
  const [selected, setSelected] = useState<string | null>(null); // zone manuelle choisie
  const [drawing, setDrawing] = useState<"new" | "fixed" | "key" | null>(null);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const justDrew = useRef(false); // le clic qui termine un cadre ne doit pas lancer la lecture
  const [following, setFollowing] = useState(false);
  const toast = useApp((s) => s.toast);
  const fps = z.fps || p.video?.fps || 25;
  const total = z.frames;
  const W = p.video!.width;
  const H = p.video!.height;
  const visible = useMemo(() => z.tracks.filter((t) => !t.hidden), [z.tracks]);

  useEffect(() => setOpts(p.options), [p.options]);

  // la vidéo pilote le numéro d'image ; sans lecture possible (codec), on avance image par image
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    let raf = 0;
    const tick = () => {
      setFrame(Math.min(total - 1, Math.floor(v.currentTime * fps + 1e-3)));
      if (!v.paused) raf = requestAnimationFrame(tick);
    };
    const onPlay = () => { setPlaying(true); raf = requestAnimationFrame(tick); };
    const onPause = () => { setPlaying(false); tick(); };
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    v.addEventListener("seeked", tick);
    return () => {
      cancelAnimationFrame(raf);
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
      v.removeEventListener("seeked", tick);
    };
  }, [fps, total]);

  const seek = useCallback(
    (f: number) => {
      const k = Math.max(0, Math.min(total - 1, Math.round(f)));
      setFrame(k);
      const v = videoRef.current;
      if (v && canPlay) v.currentTime = (k + 0.5) / fps;
    },
    [fps, total, canPlay],
  );

  // image floutée par le moteur (exactement comme à l'export) quand la vidéo est en pause
  const optsKey = JSON.stringify([opts.style, opts.shape, opts.strength, opts.margin, opts.sensitivity, opts.classes, p.manual, p.hidden]);
  useEffect(() => {
    if (playing) {
      setStillUrl(null);
      return;
    }
    if (canPlay && !preview) {
      setStillUrl(null);
      return;
    }
    const t = setTimeout(() => setStillUrl(mediaUrl(`/api/censor/projects/${p.id}/frame?f=${frame}&blur=${preview ? 1 : 0}&k=${encodeURIComponent(optsKey)}`)), 120);
    return () => clearTimeout(t);
  }, [frame, playing, preview, canPlay, p.id, optsKey]);

  // gros plan flouté d'une zone, à côté de l'intensité : la zone de l'image en cours, sinon la plus longue
  const [sample, setSample] = useState<{ f: number; url: string } | null>(null);
  const fxKey = JSON.stringify([opts.style, opts.shape, opts.strength, opts.margin, p.manual, p.hidden]);
  useEffect(() => {
    if (playing) return;
    let f: number | null = null;
    if (z.tracks.some((t) => !t.hidden && t.f0 <= frame && frame <= t.f1) || p.manual.some((m) => manualAt(m, frame))) f = frame;
    else if (sample && sample.f !== frame && (visible.some((t) => t.f0 <= sample.f && sample.f <= t.f1) || p.manual.some((m) => manualAt(m, sample.f)))) f = sample.f;
    else {
      const t = [...visible].sort((a, b) => b.f1 - b.f0 - (a.f1 - a.f0))[0];
      const m = p.manual[0];
      f = t ? (t.f0 + t.f1) >> 1 : m ? (m.keys[0].f + m.keys[m.keys.length - 1].f) >> 1 : null;
    }
    if (f == null) {
      setSample(null);
      return;
    }
    const at = f;
    const h = setTimeout(() => setSample({ f: at, url: mediaUrl(`/api/censor/projects/${p.id}/frame?f=${at}&zoom=1&k=${encodeURIComponent(fxKey)}`) }), 250);
    return () => clearTimeout(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frame, playing, fxKey, visible, p.id]);

  const saveOpts = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const setOpt = <K extends keyof Options>(k: K, v: Options[K]) => {
    const next = { ...opts, [k]: v };
    setOpts(next);
    clearTimeout(saveOpts.current);
    saveOpts.current = setTimeout(() => api.patch(`/api/censor/projects/${p.id}/options`, { [k]: v }).then(onChange).catch(onError), 300);
  };

  const boxesAt = (f: number) => {
    const out: { box: Box; color: string; manual?: string; hidden?: boolean }[] = [];
    for (const t of z.tracks) {
      if (t.f0 > f || t.f1 < f) continue;
      out.push({ box: t.boxes[f - t.f0], color: CLASS_COLOR[t.cls] ?? "#fff", hidden: t.hidden });
    }
    for (const m of p.manual) {
      const b = manualAt(m, f);
      if (b) out.push({ box: b, color: CLASS_COLOR.manual, manual: m.id });
    }
    return out;
  };

  const marks = useMemo(() => {
    const all = [...visible.map((t) => ({ f0: t.f0, f1: t.f1, color: CLASS_COLOR[t.cls] ?? "#fff" })), ...p.manual.map((m) => ({ f0: m.keys[0].f, f1: m.keys[m.keys.length - 1].f, color: CLASS_COLOR.manual }))];
    return all.sort((a, b) => a.f0 - b.f0);
  }, [visible, p.manual]);

  const jump = (dir: 1 | -1) => {
    const starts = marks.map((m) => m.f0);
    const target = dir > 0 ? starts.find((s) => s > frame + 1) : [...starts].reverse().find((s) => s < frame - 1);
    if (target != null) seek(target);
  };

  const sel = p.manual.find((m) => m.id === selected) ?? null;
  const saveManual = (manual: Manual[]) => api.put(`/api/censor/projects/${p.id}/manual`, { manual }).then(onChange).catch(onError);

  // dessin d'une zone sur l'image
  const rel = (e: React.PointerEvent) => {
    const r = stageRef.current!.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)) };
  };
  const onDown = (e: React.PointerEvent) => {
    if (!drawing) return;
    videoRef.current?.pause();
    const q = rel(e);
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    setDrag({ x0: q.x, y0: q.y, x1: q.x, y1: q.y });
  };
  const onMove = (e: React.PointerEvent) => {
    if (!drag) return;
    const q = rel(e);
    setDrag({ ...drag, x1: q.x, y1: q.y });
  };
  const onUp = () => {
    if (!drag) return;
    const box: Box = [Math.min(drag.x0, drag.x1), Math.min(drag.y0, drag.y1), Math.max(drag.x0, drag.x1), Math.max(drag.y0, drag.y1)];
    setDrag(null);
    justDrew.current = true;
    setTimeout(() => (justDrew.current = false), 250);
    const mode = drawing;
    setDrawing(null);
    if (box[2] - box[0] < 0.01 || box[3] - box[1] < 0.01) return;
    if (mode === "new") {
      const id = Math.random().toString(36).slice(2, 10);
      followFrom(frame, box, (keys) => [...p.manual, { id, keys }]);
      setSelected(id);
    } else if (mode === "fixed") {
      const id = Math.random().toString(36).slice(2, 10);
      const nextCut = z.cuts?.find((c) => c > frame);
      const end = Math.min(total - 1, frame + Math.round(FIXED_S * fps), nextCut != null ? nextCut - 1 : total);
      saveManual([...p.manual, { id, fixed: true, keys: [{ f: frame, box }, { f: end, box }] }]);
      setSelected(id);
      const atCut = nextCut != null && end === nextCut - 1;
      toast({ kind: "info", title: atCut ? `Zone fixe posée pour ${((end - frame + 1) / fps).toFixed(1).replace(".", ",")} s, jusqu'au changement de plan` : `Zone fixe posée pour ${FIXED_S} s`, body: "Elle ne bouge pas. Avancez jusqu'à la fin voulue et « Finir ici » (F) pour l'arrêter ou l'allonger.", timeout: 3500 });
    } else if (mode === "key" && sel?.fixed) {
      // zone fixe : le nouveau cadre vaut de cette image jusqu'à sa fin, sans suivi
      const keep = sel.keys.filter((k) => k.f < frame);
      const end = Math.max(frame, sel.keys[sel.keys.length - 1].f);
      saveManual(p.manual.map((m) => (m.id === sel.id ? { ...m, keys: [...keep, { f: frame, box }, ...(end > frame ? [{ f: end, box }] : [])] } : m)));
    } else if (mode === "key" && selected) {
      // on garde les images clés d'avant, la suite est refaite en suivant la zone redessinée
      const keep = p.manual.find((m) => m.id === selected)?.keys.filter((k) => k.f < frame) ?? [];
      followFrom(frame, box, (keys) => p.manual.map((m) => (m.id === selected ? { ...m, keys: [...keep, ...keys] } : m)));
    }
  };

  /** La zone tracée suit toute seule le mouvement (jusqu'à 10 s, ou jusqu'à ce qu'elle sorte du cadre). */
  const followFrom = async (f: number, box: Box, place: (keys: { f: number; box: Box }[]) => Manual[]) => {
    setFollowing(true);
    try {
      const r = await api.post<{ keys: { f: number; box: Box }[]; reason: string }>(`/api/censor/projects/${p.id}/follow`, { f, box, seconds: 10 });
      await saveManual(place(r.keys));
      const secs = (r.keys[r.keys.length - 1].f - f) / fps;
      toast({
        kind: "info",
        title: r.reason === "flat" ? "Zone sans détail : posée pour 2 s" : `Zone suivie sur ${secs.toFixed(1).replace(".", ",")} s${r.reason === "cut" ? ", jusqu'au changement de plan" : ""}`,
        body: r.reason === "lost" ? "Elle a été perdue ensuite (changement de plan ou sortie du cadre) : « Finir ici » ou « Recadrer » pour ajuster." : r.reason === "limit" ? "Allez plus loin et « Recadrer » pour continuer le suivi." : undefined,
        timeout: 3500,
      });
    } catch (e) {
      onError(e);
    } finally {
      setFollowing(false);
    }
  };

  const finishHere = (m: Manual) => {
    const b = manualAt(m, Math.min(frame, m.keys[m.keys.length - 1].f)) ?? m.keys[m.keys.length - 1].box;
    saveManual(p.manual.map((x) => (x.id === m.id ? { ...x, keys: [...x.keys.filter((k) => k.f < frame), { f: Math.max(frame, x.keys[0].f), box: b }] } : x)));
  };
  const removeManual = (m: Manual) => {
    saveManual(p.manual.filter((x) => x.id !== m.id));
    setSelected(null);
  };
  const togglePlay = () => {
    const v = videoRef.current;
    if (!v || !canPlay) return;
    if (v.paused) v.play();
    else v.pause();
  };

  const here = boxesAt(frame);

  // raccourcis clavier de la vérification (sauf pendant la saisie dans un champ)
  const keys = useRef<(e: KeyboardEvent) => void>(() => undefined);
  keys.current = (e: KeyboardEvent) => {
    const el = e.target as HTMLElement;
    if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return;
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    const k = e.key.toLowerCase();
    const step = e.shiftKey ? Math.round(fps) : 1;
    const done = () => e.preventDefault();
    if (k === " ") { done(); togglePlay(); }
    else if (k === "arrowleft") { done(); videoRef.current?.pause(); seek(frame - step); }
    else if (k === "arrowright") { done(); videoRef.current?.pause(); seek(frame + step); }
    else if (k === "n") { done(); jump(1); }
    else if (k === "p") { done(); jump(-1); }
    else if (k === "a") { done(); videoRef.current?.pause(); setDrawing(drawing === "new" ? null : "new"); }
    else if (k === "z") { done(); videoRef.current?.pause(); setDrawing(drawing === "fixed" ? null : "fixed"); }
    else if (k === "r" && sel) { done(); videoRef.current?.pause(); setDrawing(drawing === "key" ? null : "key"); }
    else if (k === "f" && sel) { done(); finishHere(sel); }
    else if (k === "b") { done(); setPreview(!preview); }
    else if (k === "escape") {
      if (drawing || drag) { done(); setDrawing(null); setDrag(null); }
      else if (selected) { done(); setSelected(null); }
    } else if (k === "delete" || k === "backspace") {
      done();
      if (sel) removeManual(sel);
      else {
        const t = z.tracks.find((t) => !t.hidden && t.f0 <= frame && frame <= t.f1);
        if (t) api.post(`/api/censor/projects/${p.id}/hide`, { track: t.id }).then(onChange).catch(onError);
      }
    }
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => keys.current(e);
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  const listed = useMemo(() => z.tracks.slice(0, 400), [z.tracks]);

  return (
    <div className="cz-layout rise">
      <div className="cz-main">
        <div className="card cz-viewer">
          <div className="cz-stage" ref={stageRef} style={{ aspectRatio: `${W} / ${H}` }} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp}>
            {canPlay && p.source.path && (
              <video ref={videoRef} src={localMediaUrl(p.source.path)} preload="auto" onError={() => setCanPlay(false)} onClick={() => !drawing && !justDrew.current && togglePlay()} />
            )}
            {stillUrl && <img className="cz-still" src={stillUrl} alt="" draggable={false} />}
            <svg className={clsx("cz-boxes", drawing && "drawing")} viewBox="0 0 1 1" preserveAspectRatio="none">
              {(!preview || playing || !stillUrl) &&
                here.map((b, i) => (
                  <rect key={i} x={b.box[0]} y={b.box[1]} width={b.box[2] - b.box[0]} height={b.box[3] - b.box[1]} className={clsx(b.hidden && "off", b.manual && "manual", b.manual === selected && "sel")} style={{ stroke: b.color }} vectorEffect="non-scaling-stroke" />
                ))}
              {drag && <rect x={Math.min(drag.x0, drag.x1)} y={Math.min(drag.y0, drag.y1)} width={Math.abs(drag.x1 - drag.x0)} height={Math.abs(drag.y1 - drag.y0)} className="draft" vectorEffect="non-scaling-stroke" />}
            </svg>
            {drawing && <div className="cz-draw-hint">{drawing === "fixed" || (drawing === "key" && sel?.fixed) ? "Tracez un cadre : il restera tel quel, sans bouger" : "Tracez un cadre sur la zone à flouter : elle suivra le mouvement toute seule"} · Échap pour annuler</div>}
            {following && <div className="cz-draw-hint">Suivi de la zone…</div>}
          </div>
          <div className="cz-controls">
            <Button size="sm" variant="ghost" iconOnly icon={<SkipBack size={15} />} tip="Zone précédente (P)" onClick={() => jump(-1)} />
            <Button size="sm" variant="ghost" iconOnly icon={<ChevronLeft size={16} />} tip="Image précédente (←, Maj+← : 1 s)" onClick={() => seek(frame - 1)} />
            {canPlay && <Button size="sm" variant="primary" iconOnly icon={playing ? <Pause size={15} /> : <Play size={15} />} tip="Lecture / pause (Espace)" onClick={togglePlay} />}
            <Button size="sm" variant="ghost" iconOnly icon={<ChevronRight size={16} />} tip="Image suivante (→, Maj+→ : 1 s)" onClick={() => seek(frame + 1)} />
            <Button size="sm" variant="ghost" iconOnly icon={<SkipForward size={15} />} tip="Zone suivante (N)" onClick={() => jump(1)} />
            <span className="mono cz-time">{clock(frame / fps)} <span className="faint">/ {clock(total / fps)} · image {frame}</span></span>
            <span className="spacer" />
            <label className="row clip-check" data-tip="Montre l'image floutée telle qu'elle sera exportée, vidéo en pause (B)">
              <Switch checked={preview} onChange={setPreview} />
              <span>{preview ? <Eye size={14} /> : <EyeOff size={14} />} Aperçu flouté</span>
            </label>
          </div>
          <Timeline marks={marks} total={total} frame={frame} onSeek={seek} />
          <div className="cz-keys">
            {KEYS.map(([k, l]) => (
              <span key={k}>
                <kbd className="kbd">{k}</kbd> {l}
              </span>
            ))}
          </div>
          {!canPlay && <div className="faint" style={{ fontSize: 12, padding: "6px 14px 12px" }}>Ce format ne se lit pas dans la fenêtre : avancez image par image ou cliquez sur la frise. L'export n'est pas concerné.</div>}
        </div>

        <div className="card card-pad cz-manual">
          <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
            <span className="label">Zones à la main</span>
            <span className="spacer" />
            <Button size="sm" variant={drawing === "new" ? "primary" : "outline"} icon={<Plus size={14} />} loading={following} tip="Tracez un cadre : la zone suit le mouvement toute seule (A)" onClick={() => setDrawing(drawing === "new" ? null : "new")}>Zone qui suit</Button>
            <Button size="sm" variant={drawing === "fixed" ? "primary" : "outline"} icon={<Pin size={13} />} tip={`Tracez un cadre : il reste tel quel, sans bouger, ${FIXED_S} s (Z)`} onClick={() => setDrawing(drawing === "fixed" ? null : "fixed")}>Zone fixe</Button>
            {sel && (
              <>
                <Button size="sm" variant={drawing === "key" ? "primary" : "outline"} onClick={() => setDrawing(drawing === "key" ? null : "key")} tip={sel.fixed ? "Redessinez la zone à cette image : ce cadre vaut jusqu'à sa fin (R)" : "Redessinez la zone à cette image : le suivi repart d'ici (R)"}>Recadrer à cette image</Button>
                <Button size="sm" variant="outline" tip="La zone s'arrête à cette image, plus tôt ou plus tard (F)" onClick={() => finishHere(sel)}>Finir ici</Button>
                <Button size="sm" variant="ghost" icon={<Trash2 size={14} />} tip="Supprimer cette zone (Suppr)" onClick={() => removeManual(sel)}>Supprimer</Button>
              </>
            )}
          </div>
          {p.manual.length > 0 ? (
            <div className="cz-chips">
              {p.manual.map((m, i) => (
                <button key={m.id} className={clsx("cz-chip", m.id === selected && "on")} onClick={() => { setSelected(m.id); seek(m.keys[0].f); }}>
                  Zone {i + 1}{m.fixed ? " · fixe" : ""} · {clock(m.keys[0].f / fps)} → {clock(m.keys[m.keys.length - 1].f / fps)}
                </button>
              ))}
            </div>
          ) : (
            <p className="faint" style={{ fontSize: 12, margin: "8px 0 0" }}>Une zone ratée par l'IA ? Mettez la vidéo au bon moment, appuyez sur A (ou « Zone qui suit ») et tracez un cadre : la zone suit le mouvement toute seule, jusqu'à 10 s. Pour un simple cadre qui ne bouge pas, choisissez « Zone fixe » (Z). « Recadrer » redessine la zone, « Finir ici » l'arrête.</p>
          )}
        </div>
      </div>

      <div className="cz-side">
        <div className="card card-pad">
          <div className="cz-stats">
            <div><b>{z.stats.zones}</b><span>zones</span></div>
            <div><b>{fmtDuration(z.stats.seconds)}</b><span>floutées</span></div>
          </div>
          {exporting ? (
            <div style={{ marginTop: 14 }}>
              <Progress value={live?.op === "export" ? live.percent : null} state="active" showValue />
              <div className="row" style={{ marginTop: 6 }}>
                <span className="faint ellipsis" style={{ fontSize: 12 }}>{live?.op === "export" ? live.label : "Préparation…"}</span>
                <span className="spacer" />
                <span className="mono faint" style={{ fontSize: 12 }}>{live?.op === "export" ? eta(live.eta) : ""}</span>
              </div>
            </div>
          ) : (
            <Button variant="primary" full icon={<ShieldCheck size={16} />} style={{ marginTop: 14 }} onClick={() => api.post(`/api/censor/projects/${p.id}/export`).then(onChange).catch(onError)}>
              {p.export?.file ? "Exporter à nouveau" : "Exporter la vidéo floutée"}
            </Button>
          )}
          {p.export?.file && !exporting && (
            <div className="cz-done">
              <CheckIcon size={14} /> <span className="ellipsis" title={p.export.file}>{fileName(p.export.file)}</span>
            </div>
          )}
        </div>

        <div className="card card-pad cz-settings">
          <div className="label">À flouter</div>
          <div className="cz-classes">
            {CLASSES.map((c) => {
              const on = opts.classes.includes(c.id);
              return (
                <button key={c.id} className={clsx("cz-class", on && "on")} onClick={() => setOpt("classes", on ? opts.classes.filter((x) => x !== c.id) : [...opts.classes, c.id])}>
                  <span className="cz-dot" style={{ background: CLASS_COLOR[c.id] }} />
                  {c.label}
                </button>
              );
            })}
          </div>
          <Range label="Sensibilité" left="Strict" right="Au moindre doute" value={opts.sensitivity} min={0} max={100} onChange={(v) => setOpt("sensitivity", v)} />
          {z.refined && (
            <label className="row clip-check" style={{ marginTop: 10 }} data-tip="Une 2e IA (CLIP) revoit chaque zone : celles qu'elle juge non nues (pecs, verre, bras…) ne sont pas floutées">
              <Switch checked={opts.verify !== false} onChange={(v) => setOpt("verify", v)} />
              <span>2e vérification</span>
            </label>
          )}
          <Range label="Marge autour" left="Serrée" right="Large" value={Math.round(opts.margin * 100)} min={0} max={60} onChange={(v) => setOpt("margin", v / 100)} />
          <div className="label" style={{ marginTop: 14 }}>Effet</div>
          <Segmented full size="sm" value={opts.style} onChange={(v) => setOpt("style", v)} options={[{ value: "blur", label: "Flou" }, { value: "pixel", label: "Pixels" }, { value: "black", label: "Noir" }]} />
          <div style={{ height: 8 }} />
          <Segmented full size="sm" value={opts.shape} onChange={(v) => setOpt("shape", v)} options={[{ value: "ellipse", label: "Ovale" }, { value: "rect", label: "Rectangle" }]} />
          {opts.style !== "black" && <Range label="Intensité" left="Léger" right="Fort" value={opts.strength} min={0} max={100} onChange={(v) => setOpt("strength", v)} />}
          {sample && (
            <button className="cz-sample" onClick={() => seek(sample.f)} data-tip="Gros plan sur une zone, floutée exactement comme à l'export. Cliquez pour aller à ce moment.">
              <img src={sample.url} alt="Aperçu du flou" draggable={false} />
              <span>Aperçu du flou · {clock(sample.f / fps)}</span>
            </button>
          )}
        </div>

        <div className="card card-pad cz-list">
          <div className="label">Zones trouvées par l'IA</div>
          {z.tracks.length === 0 && <p className="faint" style={{ fontSize: 12 }}>Aucune zone avec ces réglages.</p>}
          {listed.map((t) => (
            <div key={t.id} className={clsx("cz-item", t.hidden && "off", t.f0 <= frame && frame <= t.f1 && "now")}>
              <button className="cz-item-main" onClick={() => seek(t.f0 + Math.min(Math.round(fps * 0.3), (t.f1 - t.f0) >> 1))}>
                <span className="cz-dot" style={{ background: CLASS_COLOR[t.cls] }} />
                <span className="mono">{clock(t.f0 / fps)}</span>
                <span className="ellipsis">{CLASS_LABEL[t.cls] ?? t.cls}</span>
                <span className="faint mono">{((t.f1 - t.f0 + 1) / fps).toFixed(1)} s</span>
              </button>
              {t.hidden ? (
                <Button size="sm" variant="ghost" iconOnly icon={<Undo2 size={13} />} tip="Flouter à nouveau" onClick={() => { const i = z.hidden.findIndex((h) => h.cls === t.cls && h.f0 <= t.f1 && h.f1 >= t.f0); api.post(`/api/censor/projects/${p.id}/unhide`, { index: i }).then(onChange).catch(onError); }} />
              ) : (
                <Button size="sm" variant="ghost" iconOnly icon={<EyeOff size={13} />} tip="Pas une zone intime : ne pas flouter" onClick={() => api.post(`/api/censor/projects/${p.id}/hide`, { track: t.id }).then(onChange).catch(onError)} />
              )}
            </div>
          ))}
          {z.tracks.length > listed.length && <p className="faint" style={{ fontSize: 12 }}>… et {z.tracks.length - listed.length} autres (utilisez la frise).</p>}
        </div>
      </div>
    </div>
  );
}

const KEYS: [string, string][] = [
  ["Espace", "lecture"],
  ["← →", "image"],
  ["Maj + ← →", "1 s"],
  ["N / P", "zone suivante / précédente"],
  ["A / Z", "zone qui suit / fixe"],
  ["R / F", "recadrer / finir ici"],
  ["Suppr", "pas une zone"],
  ["B", "aperçu flouté"],
];

function manualAt(m: Manual, f: number): Box | null {
  const k = m.keys;
  if (!k.length || f < k[0].f || f > k[k.length - 1].f) return null;
  for (let i = 0; i < k.length - 1; i++) {
    if (f <= k[i + 1].f) {
      const t = k[i + 1].f === k[i].f ? 0 : (f - k[i].f) / (k[i + 1].f - k[i].f);
      return k[i].box.map((v, j) => v + (k[i + 1].box[j] - v) * t) as Box;
    }
  }
  return k[k.length - 1].box;
}

function Range({ label, left, right, value, min, max, onChange }: { label: string; left: string; right: string; value: number; min: number; max: number; onChange: (v: number) => void }) {
  return (
    <div className="cz-range">
      <div className="row">
        <span className="label">{label}</span>
        <span className="spacer" />
        <span className="mono faint">{value}</span>
      </div>
      <input type="range" min={min} max={max} value={value} onChange={(e) => onChange(Number(e.target.value))} style={{ ["--pct" as any]: `${((value - min) / (max - min)) * 100}%` }} />
      <div className="row faint cz-range-ends">
        <span>{left}</span>
        <span className="spacer" />
        <span>{right}</span>
      </div>
    </div>
  );
}

function Timeline({ marks, total, frame, onSeek }: { marks: { f0: number; f1: number; color: string }[]; total: number; frame: number; onSeek: (f: number) => void }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const at = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect();
    onSeek(((e.clientX - r.left) / r.width) * total);
  };
  return (
    <div
      className="cz-timeline"
      ref={ref}
      onPointerDown={(e) => { (e.target as HTMLElement).setPointerCapture(e.pointerId); at(e); }}
      onPointerMove={(e) => e.buttons === 1 && at(e)}
    >
      {marks.map((m, i) => (
        <span key={i} className="cz-mark" style={{ left: `${(m.f0 / total) * 100}%`, width: `max(2px, ${((m.f1 - m.f0 + 1) / total) * 100}%)`, background: m.color }} />
      ))}
      <span className="cz-head" style={{ left: `${(frame / total) * 100}%` }} />
    </div>
  );
}
