import clsx from "clsx";
import { ArrowLeft, Check as CheckIcon, FolderOpen, Link2, Pin, Plus, RefreshCw, ShieldCheck, Square, Trash2, Upload, Video, WandSparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, localMediaUrl, mediaUrl } from "../api/client";
import { events } from "../api/ws";
import { Button, Empty, ErrorAlert, Modal, PageHead, Progress, Segmented, Switch, Ticks } from "../components/ui";
import { useFileIntake } from "../hooks/useFileIntake";
import { duration as fmtDuration } from "../lib/format";
import { useApp } from "../store/app";
import { useConfirm } from "./censor/Confirm";
import { Passages, Segment, tc } from "./censor/Passages";
import { LiveBlur, Mark, Player, PlayerApi } from "./censor/Player";
import { ZoneItem, ZonesList } from "./censor/ZonesList";
import { ScopeField } from "./censor/ScopeField";
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
  ai_outside?: boolean; // zones de l'IA aussi en dehors des passages donnés par timecodes
  point_margin?: number; // un timecode seul : ± secondes
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
  segments?: Segment[];
  export: { file: string; size: number; seconds: number; blurred_frames: number; mode?: "smart" | "full"; reencoded_share?: number; smart_skipped?: string; encoder?: string } | null;
  stats: { zones?: number; seconds?: number; analysis_seconds?: number };
  detect?: { v?: number; fps: number; frames: number; seconds: number; gpu: boolean; refined?: number };
  preview?: { path?: string; width?: number; height?: number; direct?: boolean } | null; // copie d'aperçu lue par l'appli
  preview_job?: string | null;
  preview_error?: { message: string } | null;
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
  validated?: boolean; // vérifiée et gardée
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
  segments?: Segment[]; // passages donnés par timecodes
  scope_only?: boolean; // seuls les passages « zones de l'IA » sont analysés
  unanalysed?: number; // images de passages pas encore analysées
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
  segment: "#B7A6FF", // passage donné par timecodes
};

const isUrl = (s: string) => /^https?:\/\//i.test(s.trim());
const cleanPath = (s: string) => s.trim().replace(/^"(.*)"$/, "$1");
const isPath = (s: string) => /^([A-Za-z]:\\|\\\\)[^<>|*?]+\.\w{2,5}$/.test(cleanPath(s));
const isSource = (s: string) => isUrl(s) || isPath(s);
const fileName = (s: string) => cleanPath(s).split(/[\\/]/).pop() ?? s;
const running = (s: string) => s === "queued" || s === "analyzing";
const host = (u: string) => {
  try {
    return new URL(u).hostname.replace(/^www\./, "");
  } catch {
    return "lien";
  }
};
// les raccourcis de la vérification ne valent que sur la page Floutage, sans fenêtre ouverte par-dessus
const reviewKeysActive = () => useApp.getState().page === "censor" && !document.querySelector(".modal-backdrop");

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
      } else if (ev.type === "censor_project") {
        const id = ev.data?.project;
        if (id) setLive((l) => { const { [id]: _, ...rest } = l; return rest; });
        refresh();
      }
    });
  }, [refresh]);
  const connected = useApp((s) => s.connected);
  useEffect(() => { if (connected) refresh(); }, [connected, refresh]);

  // une vidéo déposée ou ouverte avec se7en pendant qu'un projet est ouvert : retour à la liste pour la recevoir
  const incoming = useApp((s) => (s.pendingFilesPage === "censor" && s.pendingFiles?.length) || s.pendingUrls?.length);
  useEffect(() => { if (incoming) setOpenId(null); }, [incoming]);

  if (openId) return <ProjectView id={openId} live={live[openId]} onBack={() => { setOpenId(null); refresh(); }} />;

  return (
    <div className="page-inner wide">
      <PageHead eyebrow="se7en Censored" title="Floutage auto" echo="pour Twitch" sub="Importe un film ou une vidéo : l'IA repère les parties intimes visibles et les floute, uniquement là où il faut." />
      <NewProject
        onCreated={(p) => {
          refresh();
          setOpenId(p.id);
        }}
        onError={(e) => toast({ kind: "error", title: e.message, body: e.hint })}
      />
      <div className="label" style={{ margin: "28px 0 12px" }}>Projets</div>
      {projects.length === 0 ? (
        <Empty icon={<ShieldCheck size={22} />} title="Aucun projet pour l'instant" body="Glisse une vidéo ci-dessus, choisis-la ou colle son lien : l'analyse démarre tout de suite." />
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
                    {[p.duration ? fmtDuration(p.duration) : null, p.url ? host(p.url) : "fichier", p.zones != null ? `${p.zones} zones` : null].filter(Boolean).join(" · ")}
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
  const [scope, setScope] = useState<"all" | "passages">("all");
  const [scopeText, setScopeText] = useState("");
  const [scopeMargin, setScopeMargin] = useState(2);
  const [scopeCount, setScopeCount] = useState(0);
  const onlyPassages = scope === "passages";
  const ready = isSource(source) && (!onlyPassages || scopeCount > 0);

  const create = async (src: string) => {
    if (!src.trim() || (onlyPassages && !scopeCount)) return;
    setSending(true);
    try {
      const r = await api.post<{ project: Project }>("/api/censor/projects", {
        source: isUrl(src) ? src.trim() : cleanPath(src),
        options: { quality, careful, point_margin: scopeMargin },
        ...(onlyPassages ? { timecodes: scopeText, margin: scopeMargin } : {}),
      });
      setSource("");
      setScopeText("");
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
      if (!vids.length) onError({ message: "Ce n'est pas une vidéo", hint: "se7en Censored floute des vidéos (mp4, mkv, mov…)." });
      else if (vids.length === 1) setSource(vids[0].path);
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
          placeholder="Glisse une vidéo, choisis-la, ou colle son chemin ou un lien"
          value={local && !typing ? fileName(source) : source}
          title={local ? cleanPath(source) : undefined}
          onChange={(e) => setSource(e.target.value)}
          onFocus={(e) => { setTyping(true); if (local) e.target.select(); }}
          onBlur={() => setTyping(false)}
          onKeyDown={(e) => e.key === "Enter" && ready && !sending && create(source)}
          spellCheck={false}
        />
        <Button variant={local ? "ghost" : "outline"} icon={<Upload size={15} />} onClick={intake.pick}>
          {local ? "Changer" : "Choisir une vidéo"}
        </Button>
        <Button variant="primary" icon={<WandSparkles size={16} />} loading={sending} disabled={!ready} onClick={() => create(source)}>
          {onlyPassages ? "Analyser ces passages" : "Analyser"}
        </Button>
      </div>
      <div className="clip-options">
        <div className="clip-opt">
          <span className="label">À vérifier</span>
          <Segmented
            size="sm"
            value={scope}
            onChange={setScope}
            options={[
              { value: "all", label: "Toute la vidéo", tip: "L'IA passe sur toutes les images" },
              { value: "passages", label: "Seulement des passages", tip: "Donne les timecodes : l'IA ne vérifie que ces passages, bien plus vite" },
            ]}
          />
        </div>
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
      {onlyPassages && (
        <ScopeField text={scopeText} setText={setScopeText} margin={scopeMargin} setMargin={setScopeMargin} onCount={setScopeCount} onError={onError} />
      )}
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

  const [busy, setBusy] = useState<string | null>(null);
  const act = async (what: string, fn: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(what);
    try {
      await fn();
      load();
    } catch (e: any) {
      toast({ kind: "error", title: e.message, body: e.hint });
    } finally {
      setBusy(null);
    }
  };
  const [confirmDelete, setConfirmDelete] = useState(false);
  const remove = async () => {
    setBusy("delete");
    try {
      await api.del(`/api/censor/projects/${id}`);
      setConfirmDelete(false);
      onBack();
    } catch (e: any) {
      toast({ kind: "error", title: e.message, body: e.hint });
      setBusy(null);
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
              <Button icon={<Square size={14} />} loading={busy === "cancel"} onClick={() => act("cancel", () => api.post(`/api/censor/projects/${id}/cancel`))}>Arrêter</Button>
            ) : (
              <>
                {p.export?.file && <Button icon={<FolderOpen size={15} />} onClick={() => act("folder", () => api.post(`/api/censor/projects/${id}/open-folder`))}>Dossier</Button>}
                {(p.status === "paused" || p.status === "error") && (
                  <Button variant="primary" icon={<RefreshCw size={15} />} loading={busy === "analyze"} onClick={() => act("analyze", () => api.post(`/api/censor/projects/${id}/analyze`))}>{p.status === "paused" ? "Reprendre l'analyse" : "Relancer"}</Button>
                )}
              </>
            )}
            <Button
              variant="ghost"
              iconOnly
              icon={<Trash2 size={15} />}
              tip="Supprimer le projet (la vidéo exportée reste dans son dossier)"
              onClick={() => setConfirmDelete(true)}
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
            Tu peux arrêter et reprendre plus tard : l'analyse repart là où elle s'était arrêtée.
          </p>
        </div>
      )}

      {p.error && !analyzing && <ErrorAlert error={{ code: p.error.code ?? "error", message: p.error.message, hint: p.error.hint }} />}
      {reviewable && p.detect && (p.detect.v ?? 1) < 2 && (
        <div className="clip-hint" style={{ marginBottom: 12 }}>
          <span>Analysé avec une version précédente : relance l'analyse pour profiter du suivi visuel des zones (moins de trous dans le flou). Tes zones à la main sont gardées.</span>
          <Button size="sm" variant="primary" icon={<RefreshCw size={14} />} loading={busy === "analyze"} onClick={() => act("analyze", () => api.post(`/api/censor/projects/${id}/analyze`))}>Réanalyser</Button>
        </div>
      )}
      {reviewable && p.detect && (p.detect.v ?? 1) >= 2 && !p.detect.refined && (
        <div className="clip-hint" style={{ marginBottom: 12 }}>
          <span>Nouveau : la 2e passe revoit les moments où l'IA a vu de la nudité. Elle écarte ce qui n'en est pas (pecs, verre, bras), trouve les parties petites ou lointaines et coupe les zones aux changements de plan. Quelques minutes, sans refaire l'analyse ; tes zones à la main sont gardées.</span>
          <Button size="sm" variant="primary" icon={<ShieldCheck size={14} />} loading={busy === "analyze"} onClick={() => act("analyze", () => api.post(`/api/censor/projects/${id}/analyze`))}>Lancer la 2e passe</Button>
        </div>
      )}
      {reviewable && zones?.scope_only && !(zones.unanalysed ?? 0) && (
        <div className="clip-hint" style={{ marginBottom: 12 }}>
          <span>Seuls tes passages ont été analysés (en violet sur la frise). Ajoute d'autres passages plus bas, ou analyse toute la vidéo : ce qui est déjà fait est gardé.</span>
          <Button size="sm" variant="outline" icon={<RefreshCw size={14} />} loading={busy === "analyze"} onClick={() => act("analyze", () => api.post(`/api/censor/projects/${id}/analyze?all=1`))}>Analyser toute la vidéo</Button>
        </div>
      )}
      {reviewable && (zones?.unanalysed ?? 0) > 0 && (
        <div className="clip-hint" style={{ marginBottom: 12 }}>
          <span>Des passages « Zones IA » n'ont pas encore été analysés ({fmtDuration((zones!.unanalysed ?? 0) / (zones!.fps || 25))}) : l'IA n'y a rien cherché.</span>
          <Button size="sm" variant="primary" icon={<WandSparkles size={14} />} loading={busy === "analyze"} onClick={() => act("analyze", () => api.post(`/api/censor/projects/${id}/analyze`))}>Analyser ces passages</Button>
        </div>
      )}
      {p.notes?.map((n) => (
        <div key={n} className="clip-hint" style={{ marginBottom: 12 }}>{n}</div>
      ))}

      {reviewable && p.video && zones && <Review project={p} zones={zones} live={live} exporting={exporting} onChange={load} onError={(e) => toast({ kind: "error", title: e.message, body: e.hint })} />}

      <Modal
        open={confirmDelete}
        onClose={() => busy !== "delete" && setConfirmDelete(false)}
        eyebrow="Supprimer"
        title="Supprimer ce projet ?"
        actions={
          <>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>Annuler</Button>
            <Button variant="primary" icon={<Trash2 size={15} />} loading={busy === "delete"} onClick={remove}>Supprimer</Button>
          </>
        }
      >
        {analyzing || exporting ? "L'analyse ou l'export en cours sera arrêté. " : ""}
        L'analyse, tes zones à la main et tes passages seront perdus. La vidéo d'origine et la vidéo exportée restent dans leur dossier.
      </Modal>
    </div>
  );
}

// ------------------------------------------------------------------ vérification
type Tab = "zones" | "passages" | "manual" | "settings";

function Review({ project: p, zones: z, live, exporting, onChange, onError }: { project: Project; zones: Zones; live?: Live; exporting: boolean; onChange: () => void; onError: (e: any) => void }) {
  const player = useRef<PlayerApi | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [preview, setPreview] = useState(true);
  const [stillUrl, setStillUrl] = useState<string | null>(null);
  const [stillReady, setStillReady] = useState(false);
  const [opts, setOpts] = useState<Options>(p.options);
  const [selected, setSelected] = useState<string | null>(null); // zone manuelle choisie
  const [drawing, setDrawing] = useState<"new" | "fixed" | "key" | null>(null);
  const [drag, setDrag] = useState<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const justDrew = useRef(false); // le clic qui termine un cadre ne doit pas lancer la lecture
  const [following, setFollowing] = useState(false);
  const [tab, setTab] = useState<Tab>("zones");
  const [markIn, setMarkIn] = useState<number | null>(null);
  const [help, setHelp] = useState(false);
  const toast = useApp((s) => s.toast);
  const ask = useConfirm(p.id);
  const fps = z.fps || p.video?.fps || 25;
  const total = z.frames;
  const W = p.video!.width;
  const H = p.video!.height;
  const visible = useMemo(() => z.tracks.filter((t) => !t.hidden), [z.tracks]);
  const segments = useMemo(() => p.segments ?? z.segments ?? [], [p.segments, z.segments]);

  // la vidéo lue : la copie d'aperçu si elle existe, sinon l'original quand la fenêtre sait le lire
  const pv = p.preview;
  const src = pv?.path ? localMediaUrl(pv.path) : pv?.direct && p.source.path ? localMediaUrl(p.source.path) : null;
  const preparing = !src && (!!p.preview_job || live?.op === "preview");
  useEffect(() => {
    if (!pv?.path && !pv?.direct && !p.preview_job && !p.preview_error && p.source.path)
      api.post(`/api/censor/projects/${p.id}/preview`).then(onChange).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.id, pv?.path, pv?.direct]);

  const pendingOpts = useRef<Partial<Options>>({});
  useEffect(() => setOpts({ ...p.options, ...pendingOpts.current }), [p.options]);

  const onFrame = useCallback((f: number) => setFrame(f), []);
  const seek = useCallback((f: number) => player.current?.seek(f), []);

  // ce qui est flouté à cette image, comme à l'export (passages plein cadre, zones de l'IA, zones à la main)
  const blurredAt = useCallback(
    (f: number): Box[] => {
      if (segments.some((g) => g.mode === "full" && g.f0 <= f && f <= g.f1)) return [[0, 0, 1, 1]];
      const out: Box[] = [];
      if (opts.ai_outside !== false || segments.some((g) => g.mode === "ai" && g.f0 <= f && f <= g.f1))
        for (const t of visible) if (t.f0 <= f && f <= t.f1) out.push(t.boxes[f - t.f0]);
      for (const m of p.manual) {
        const b = manualAt(m, f);
        if (b) out.push(b);
      }
      return out;
    },
    [segments, opts.ai_outside, visible, p.manual],
  );
  const blurred = useMemo(() => blurredAt(frame), [blurredAt, frame]);

  // image exacte du moteur, en pause, quand on ne bouge plus (le flou en direct la précède)
  const optsKey = useMemo(
    () => JSON.stringify([opts.style, opts.shape, opts.strength, opts.margin, opts.sensitivity, opts.classes, p.manual, p.hidden, segments, opts.ai_outside]),
    [opts.style, opts.shape, opts.strength, opts.margin, opts.sensitivity, opts.classes, p.manual, p.hidden, segments, opts.ai_outside],
  );
  useEffect(() => {
    setStillReady(false);
    if (playing || (src && !preview)) {
      setStillUrl(null);
      return;
    }
    const t = setTimeout(() => setStillUrl(mediaUrl(`/api/censor/projects/${p.id}/frame?f=${frame}&blur=${preview ? 1 : 0}&k=${encodeURIComponent(optsKey)}`)), src ? 400 : 90);
    return () => clearTimeout(t);
  }, [frame, playing, preview, src, p.id, optsKey]);

  // gros plan flouté d'une zone (réglage de l'intensité) : la zone de l'image en cours, sinon la plus longue
  const [sample, setSample] = useState<{ f: number; url: string } | null>(null);
  const fxKey = useMemo(
    () => JSON.stringify([opts.style, opts.shape, opts.strength, opts.margin, p.manual, p.hidden, segments, opts.ai_outside]),
    [opts.style, opts.shape, opts.strength, opts.margin, p.manual, p.hidden, segments, opts.ai_outside],
  );
  useEffect(() => {
    if (playing || tab !== "settings") return;
    let f: number | null = null;
    if (blurred.length && !(blurred.length === 1 && blurred[0][2] - blurred[0][0] > 0.98)) f = frame;
    else if (sample && blurredAt(sample.f).length) f = sample.f;
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
    const h = setTimeout(() => setSample({ f: at, url: mediaUrl(`/api/censor/projects/${p.id}/frame?f=${at}&zoom=1&k=${encodeURIComponent(fxKey)}`) }), 300);
    return () => clearTimeout(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [frame, playing, fxKey, visible, p.id, tab]);

  // réglages : cumulés et envoyés ensemble ; l'export attend qu'ils soient enregistrés
  const saveOpts = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const flushOpts = useCallback(async () => {
    clearTimeout(saveOpts.current);
    const patch = pendingOpts.current;
    if (!Object.keys(patch).length) return;
    pendingOpts.current = {};
    await api.patch(`/api/censor/projects/${p.id}/options`, patch);
  }, [p.id]);
  const setOpt = <K extends keyof Options>(k: K, v: Options[K]) => {
    setOpts((o) => ({ ...o, [k]: v }));
    pendingOpts.current = { ...pendingOpts.current, [k]: v };
    clearTimeout(saveOpts.current);
    saveOpts.current = setTimeout(() => flushOpts().then(onChange).catch(onError), 300);
  };
  useEffect(() => () => { flushOpts().catch(() => undefined); }, [flushOpts]);
  const [exportSending, setExportSending] = useState(false);
  const startExport = async () => {
    setExportSending(true);
    try {
      await flushOpts();
      await api.post(`/api/censor/projects/${p.id}/export`);
      onChange();
    } catch (e) {
      onError(e);
    } finally {
      setExportSending(false);
    }
  };

  const marks = useMemo(() => {
    const all: Mark[] = [
      ...visible.map((t) => ({ f0: t.f0, f1: t.f1, color: CLASS_COLOR[t.cls] ?? "#fff", kind: (t.validated ? "validated" : "ai") as Mark["kind"] })),
      ...p.manual.map((m) => ({ f0: m.keys[0].f, f1: m.keys[m.keys.length - 1].f, color: CLASS_COLOR.manual, kind: "manual" as const })),
      ...segments.map((g) => ({ f0: g.f0, f1: g.f1, color: CLASS_COLOR.segment, kind: "segment" as const })),
    ];
    return all.sort((a, b) => a.f0 - b.f0);
  }, [visible, p.manual, segments]);

  const jump = useCallback((dir: 1 | -1) => {
    const starts = marks.map((m) => m.f0);
    const target = dir > 0 ? starts.find((s) => s > frame + 1) : [...starts].reverse().find((s) => s < frame - 1);
    if (target != null) seek(target);
  }, [marks, frame, seek]);

  const sel = p.manual.find((m) => m.id === selected) ?? null;
  const saveManual = (manual: Manual[]) => api.put(`/api/censor/projects/${p.id}/manual`, { manual }).then(onChange).catch(onError);
  const putSegments = (next: Segment[]) => api.put(`/api/censor/projects/${p.id}/segments`, { segments: next }).then(onChange);

  // trier les propositions de l'IA
  const validate = (t: Track) => api.post(`/api/censor/projects/${p.id}/validate`, { track: t.id }).then(onChange).catch(onError);
  const unvalidate = (t: Track) => api.post(`/api/censor/projects/${p.id}/unvalidate`, { track: t.id }).then(onChange).catch(onError);
  const retire = (t: Track) =>
    ask.confirm({
      title: "Retirer cette zone ?",
      body: `${CLASS_LABEL[t.cls] ?? t.cls} à ${tc(Math.floor(t.f0 / fps))} (${((t.f1 - t.f0 + 1) / fps).toFixed(1).replace(".", ",")} s) ne sera plus floutée et sort de la liste. Tu la retrouves dans « Retirées ».`,
      ok: "Retirer la zone",
      run: () => api.post(`/api/censor/projects/${p.id}/hide`, { track: t.id }).then(onChange).catch(onError),
    });
  const restore = (t: Track) => {
    const i = z.hidden.findIndex((h) => h.cls === t.cls && h.f0 <= t.f1 && h.f1 >= t.f0);
    api.post(`/api/censor/projects/${p.id}/unhide`, { index: i }).then(onChange).catch(onError);
  };
  const atFrame = (list: Track[]) => list.find((t) => t.f0 <= frame && frame <= t.f1);

  // listes (React.memo) : des fonctions stables et seulement ce qui change vraiment, pas l'image en cours
  const latest = useRef({ validate, unvalidate, retire, restore, setOpt, putSegments, onChange, onError, confirm: ask.confirm });
  latest.current = { validate, unvalidate, retire, restore, setOpt, putSegments, onChange, onError, confirm: ask.confirm };
  const panel = useMemo(() => ({
    onValidate: (t: ZoneItem) => latest.current.validate(t as Track),
    onUnvalidate: (t: ZoneItem) => latest.current.unvalidate(t as Track),
    onRemove: (t: ZoneItem) => latest.current.retire(t as Track),
    onRestore: (t: ZoneItem) => latest.current.restore(t as Track),
    onSaved: () => latest.current.onChange(),
    onError: (e: unknown) => latest.current.onError(e),
    setOpt: (k: "ai_outside" | "point_margin", v: boolean | number) => latest.current.setOpt(k, v as never),
    confirm: (a: { title: string; body: string; ok: string; run: () => void }) => latest.current.confirm(a),
    onPut: (next: Segment[]) => latest.current.putSegments(next),
  }), []);
  const nowZones = useMemo(() => z.tracks.filter((t) => t.f0 <= frame && frame <= t.f1).map((t) => t.id).join(" "), [z.tracks, frame]);
  const nowSegs = useMemo(() => segments.filter((g) => g.f0 <= frame && frame <= g.f1).map((g) => g.id).join(" "), [segments, frame]);

  // dessin d'une zone sur l'image
  const rel = (e: React.PointerEvent) => {
    const r = stageRef.current!.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)) };
  };
  const onDown = (e: React.PointerEvent) => {
    if (!drawing) return;
    player.current?.pause();
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
    setTab("manual");
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
      toast({ kind: "info", title: atCut ? `Zone fixe posée pour ${((end - frame + 1) / fps).toFixed(1).replace(".", ",")} s, jusqu'au changement de plan` : `Zone fixe posée pour ${FIXED_S} s`, body: "Elle ne bouge pas. Avance jusqu'à la fin voulue et « Finir ici » (E) pour l'arrêter ou l'allonger.", timeout: 3500 });
    } else if (mode === "key" && sel?.fixed) {
      const keep = sel.keys.filter((k) => k.f < frame);
      const end = Math.max(frame, sel.keys[sel.keys.length - 1].f);
      saveManual(p.manual.map((m) => (m.id === sel.id ? { ...m, keys: [...keep, { f: frame, box }, ...(end > frame ? [{ f: end, box }] : [])] } : m)));
    } else if (mode === "key" && selected) {
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
        body: r.reason === "lost" ? "Elle a été perdue ensuite (changement de plan ou sortie du cadre) : « Finir ici » ou « Recadrer » pour ajuster." : r.reason === "limit" ? "Va plus loin et « Recadrer » pour continuer le suivi." : undefined,
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
  const removeManual = (m: Manual) =>
    ask.confirm({
      title: "Supprimer cette zone à la main ?",
      body: `La zone de ${tc(m.keys[0].f / fps)} à ${tc(m.keys[m.keys.length - 1].f / fps)} ne sera plus floutée.`,
      ok: "Supprimer",
      run: () => { saveManual(p.manual.filter((x) => x.id !== m.id)); setSelected(null); },
    });

  // passage plein cadre marqué en regardant : I au début, O à la fin
  const markOut = () => {
    if (markIn == null) {
      toast({ kind: "info", title: "Marque d'abord le début avec I", timeout: 2500 });
      return;
    }
    const a = Math.min(markIn, frame);
    const b = Math.max(markIn, frame);
    setMarkIn(null);
    putSegments([...segments, { id: Math.random().toString(36).slice(2, 10), f0: a, f1: b, mode: "full" as const, label: "marqué à la main", validated: true }].sort((x, y) => x.f0 - y.f0))
      .then(() => { setTab("passages"); toast({ kind: "success", title: `Passage flouté en plein cadre : ${tc(a / fps)} → ${tc((b + 1) / fps)}`, timeout: 3000 }); })
      .catch(onError);
  };

  // raccourcis clavier de la vérification (sauf pendant la saisie dans un champ)
  const keys = useRef<(e: KeyboardEvent) => void>(() => undefined);
  keys.current = (e: KeyboardEvent) => {
    if (!reviewKeysActive()) return;
    const el = e.target as HTMLElement;
    if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable)) return;
    if (e.ctrlKey || e.altKey || e.metaKey) return;
    const k = e.key.toLowerCase();
    const pl = player.current;
    const done = () => e.preventDefault();
    if (k === " " || k === "k") { done(); pl?.toggle(); }
    else if (k === "arrowleft" || k === ",") { done(); if (e.shiftKey) pl?.skip(-1); else pl?.step(-1); }
    else if (k === "arrowright" || k === ".") { done(); if (e.shiftKey) pl?.skip(1); else pl?.step(1); }
    else if (k === "j") { done(); pl?.skip(-10); }
    else if (k === "l") { done(); pl?.skip(10); }
    else if (k === "home") { done(); seek(0); }
    else if (k === "end") { done(); seek(total - 1); }
    else if (k === "n") { done(); jump(1); }
    else if (k === "p") { done(); jump(-1); }
    else if (k === "[") { done(); pl?.speed(-1); }
    else if (k === "]") { done(); pl?.speed(1); }
    else if (k === "m") { done(); pl?.mute(); }
    else if (k === "f") { done(); pl?.fullscreen(); }
    else if (k === "b") { done(); setPreview((v) => !v); }
    else if (k === "v") {
      done();
      const t = atFrame(visible.filter((x) => !x.validated));
      if (t) validate(t);
    } else if (k === "x" || k === "delete" || k === "backspace") {
      done();
      if (sel) removeManual(sel);
      else {
        const t = atFrame(visible);
        if (t) retire(t);
      }
    }
    else if (k === "i") { done(); setMarkIn(frame); toast({ kind: "info", title: `Début du passage : ${tc(frame / fps)}`, body: "Va à la fin et appuie sur O.", timeout: 2500 }); }
    else if (k === "o") { done(); markOut(); }
    else if (k === "a") { done(); pl?.pause(); setDrawing(drawing === "new" ? null : "new"); }
    else if (k === "z") { done(); pl?.pause(); setDrawing(drawing === "fixed" ? null : "fixed"); }
    else if (k === "r" && sel) { done(); pl?.pause(); setDrawing(drawing === "key" ? null : "key"); }
    else if (k === "e" && sel) { done(); finishHere(sel); }
    else if (k === "?") { done(); setHelp(true); }
    else if (k === "escape") {
      if (drawing || drag) { done(); setDrawing(null); setDrag(null); }
      else if (markIn != null) { done(); setMarkIn(null); }
      else if (selected) { done(); setSelected(null); }
    }
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => keys.current(e);
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  const page = useApp((s) => s.page);
  useEffect(() => {
    if (page !== "censor") player.current?.pause();
  }, [page]);

  const showLive = preview && blurred.length > 0 && !(stillReady && !playing);
  const todoCount = visible.filter((t) => !t.validated).length;

  return (
    <div className="cz-layout rise">
      <div className="cz-main">
        <Player
          ref={player}
          src={src}
          fps={fps}
          total={total}
          frame={frame}
          onFrame={onFrame}
          ratio={W / H}
          stillUrl={stillUrl}
          onStillLoad={() => setStillReady(true)}
          marks={marks}
          preview={preview}
          setPreview={setPreview}
          stageRef={stageRef}
          stageProps={{ onPointerDown: onDown, onPointerMove: onMove, onPointerUp: onUp }}
          onPlayingChange={setPlaying}
          onJump={jump}
          onVideoClick={() => !drawing && !justDrew.current && player.current?.toggle()}
          waitingText={preparing ? `Préparation de l'aperçu vidéo${live?.op === "preview" ? ` · ${Math.round(live.percent)} %` : "…"} En attendant : image par image.` : null}
          overlay={
            <>
              {showLive && <LiveBlur boxes={blurred} style={opts.style} shape={opts.shape} strength={opts.strength} />}
              <svg className={clsx("cz-boxes", drawing && "drawing")} viewBox="0 0 1 1" preserveAspectRatio="none">
                {!preview &&
                  boxesAt(z.tracks, p.manual, frame).map((b, i) => (
                    <rect key={i} x={b.box[0]} y={b.box[1]} width={b.box[2] - b.box[0]} height={b.box[3] - b.box[1]} className={clsx(b.hidden && "off", b.manual && "manual", b.manual === selected && "sel")} style={{ stroke: b.color }} vectorEffect="non-scaling-stroke" />
                  ))}
                {preview && sel && manualAt(sel, frame) && (() => { const b = manualAt(sel, frame)!; return <rect x={b[0]} y={b[1]} width={b[2] - b[0]} height={b[3] - b[1]} className="manual sel" style={{ stroke: "#fff" }} vectorEffect="non-scaling-stroke" />; })()}
                {drag && <rect x={Math.min(drag.x0, drag.x1)} y={Math.min(drag.y0, drag.y1)} width={Math.abs(drag.x1 - drag.x0)} height={Math.abs(drag.y1 - drag.y0)} className="draft" vectorEffect="non-scaling-stroke" />}
              </svg>
              {drawing && <div className="cz-draw-hint">{drawing === "fixed" || (drawing === "key" && sel?.fixed) ? "Trace un cadre : il restera tel quel, sans bouger" : "Trace un cadre sur la zone à flouter : elle suivra le mouvement toute seule"} · Échap pour annuler</div>}
              {following && <div className="cz-draw-hint">Suivi de la zone…</div>}
              {markIn != null && !drawing && <div className="cz-draw-hint">Passage depuis {tc(markIn / fps)} · O pour la fin · Échap pour annuler</div>}
            </>
          }
        />
        <div className="cz-keys">
          {KEYS.slice(0, 7).map(([k, l]) => (
            <span key={k}>
              <kbd className="kbd">{k}</kbd> {l}
            </span>
          ))}
          <button className="cz-keys-more" onClick={() => setHelp(true)}>Tous les raccourcis (?)</button>
        </div>
      </div>

      <div className="cz-side">
        <div className="card card-pad cz-export">
          <div className="cz-stats">
            <div><b>{z.stats.zones}</b><span>zones</span></div>
            <div><b>{fmtDuration(z.stats.seconds)}</b><span>floutées</span></div>
            <div><b>{todoCount}</b><span>à vérifier</span></div>
          </div>
          {exporting ? (
            <div style={{ marginTop: 12 }}>
              <Progress value={live?.op === "export" ? live.percent : null} state="active" showValue />
              <div className="row" style={{ marginTop: 6 }}>
                <span className="faint ellipsis" style={{ fontSize: 12 }}>{live?.op === "export" ? live.label : "Préparation…"}</span>
                <span className="spacer" />
                <span className="mono faint" style={{ fontSize: 12 }}>{live?.op === "export" ? eta(live.eta) : ""}</span>
              </div>
            </div>
          ) : (
            <Button variant="primary" full icon={<ShieldCheck size={16} />} style={{ marginTop: 12 }} loading={exportSending} onClick={startExport}>
              {p.export?.file ? "Exporter à nouveau" : "Exporter la vidéo floutée"}
            </Button>
          )}
          {p.export?.file && !exporting && (
            <>
              <div className="cz-done">
                <CheckIcon size={14} /> <span className="ellipsis" title={p.export.file}>{fileName(p.export.file)}</span>
              </div>
              <div className="faint" style={{ fontSize: 11, marginTop: 4 }}>
                {p.export.mode === "smart"
                  ? `Rendu intelligent : ${Math.max(0.1, Math.round((p.export.reencoded_share ?? 0) * 1000) / 10).toString().replace(".", ",")} % réencodé, le reste copié à l'identique · ${fmtDuration(p.export.seconds)}`
                  : p.export.mode === "full"
                    ? `Export complet${p.export.smart_skipped ? ` (${p.export.smart_skipped})` : ""} · ${fmtDuration(p.export.seconds)}`
                    : null}
              </div>
            </>
          )}
        </div>

        <div className="card cz-panel">
          <div className="cz-tabs" role="tablist">
            {([
              ["zones", "Zones IA", todoCount],
              ["passages", "Passages", segments.length],
              ["manual", "À la main", p.manual.length],
              ["settings", "Réglages", null],
            ] as [Tab, string, number | null][]).map(([id, label, n]) => (
              <button key={id} role="tab" aria-selected={tab === id} className={clsx("cz-tab", tab === id && "on")} onClick={() => setTab(id)}>
                {label}
                {n != null && n > 0 && <span className="cz-tab-n">{n}</span>}
              </button>
            ))}
          </div>
          <div className="cz-panel-body">
            {tab === "zones" && (
              <ZonesList
                tracks={z.tracks}
                fps={fps}
                now={nowZones}
                labels={CLASS_LABEL}
                colors={CLASS_COLOR}
                onSeek={seek}
                onValidate={panel.onValidate}
                onUnvalidate={panel.onUnvalidate}
                onRemove={panel.onRemove}
                onRestore={panel.onRestore}
              />
            )}
            {tab === "passages" && (
              <Passages
                pid={p.id}
                fps={fps}
                frames={total}
                segments={segments}
                aiOutside={opts.ai_outside !== false}
                margin={opts.point_margin ?? 2}
                now={nowSegs}
                defaultMode={z.scope_only ? "ai" : "full"}
                onSeek={seek}
                onSaved={panel.onSaved}
                onError={panel.onError}
                setOpt={panel.setOpt}
                confirm={panel.confirm}
                onPut={panel.onPut}
              />
            )}
            {tab === "manual" && (
              <div className="cz-manual">
                <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                  <Button size="sm" variant={drawing === "new" ? "primary" : "outline"} icon={<Plus size={14} />} loading={following} tip="Trace un cadre : la zone suit le mouvement toute seule (A)" onClick={() => setDrawing(drawing === "new" ? null : "new")}>Zone qui suit</Button>
                  <Button size="sm" variant={drawing === "fixed" ? "primary" : "outline"} icon={<Pin size={13} />} tip={`Trace un cadre : il reste tel quel, sans bouger, ${FIXED_S} s (Z)`} onClick={() => setDrawing(drawing === "fixed" ? null : "fixed")}>Zone fixe</Button>
                </div>
                {sel && (
                  <div className="row cz-sel-actions">
                    <Button size="sm" variant={drawing === "key" ? "primary" : "outline"} onClick={() => setDrawing(drawing === "key" ? null : "key")} tip={sel.fixed ? "Redessine la zone à cette image : ce cadre vaut jusqu'à sa fin (R)" : "Redessine la zone à cette image : le suivi repart d'ici (R)"}>Recadrer ici</Button>
                    <Button size="sm" variant="outline" tip="La zone s'arrête à cette image, plus tôt ou plus tard (E)" onClick={() => finishHere(sel)}>Finir ici</Button>
                    <Button size="sm" variant="ghost" icon={<Trash2 size={14} />} tip="Supprimer cette zone (X)" onClick={() => removeManual(sel)}>Supprimer</Button>
                  </div>
                )}
                {p.manual.length > 0 ? (
                  <div className="cz-chips">
                    {p.manual.map((m, i) => (
                      <button key={m.id} className={clsx("cz-chip", m.id === selected && "on")} aria-pressed={m.id === selected} onClick={() => { setSelected(m.id); seek(m.keys[0].f); }}>
                        Zone {i + 1}{m.fixed ? " · fixe" : ""} · {clock(m.keys[0].f / fps)} → {clock(m.keys[m.keys.length - 1].f / fps)}
                      </button>
                    ))}
                  </div>
                ) : (
                  <p className="faint cz-empty">Une zone ratée par l'IA ? Mets la vidéo au bon moment, appuie sur A (ou « Zone qui suit ») et trace un cadre : elle suit le mouvement toute seule, jusqu'à 10 s. Pour un cadre qui ne bouge pas : « Zone fixe » (Z). Pour flouter tout un passage : I au début, O à la fin.</p>
                )}
              </div>
            )}
            {tab === "settings" && (
              <div className="cz-settings">
                <div className="label">À flouter</div>
                <div className="cz-classes">
                  {CLASSES.map((c) => {
                    const on = opts.classes.includes(c.id);
                    return (
                      <button key={c.id} className={clsx("cz-class", on && "on")} aria-pressed={on} onClick={() => setOpt("classes", on ? opts.classes.filter((x) => x !== c.id) : [...opts.classes, c.id])}>
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
                  <button className="cz-sample" onClick={() => seek(sample.f)} data-tip="Gros plan sur une zone, floutée exactement comme à l'export. Clique pour aller à ce moment.">
                    <img src={sample.url} alt="Aperçu du flou" draggable={false} />
                    <span>Aperçu du flou · {clock(sample.f / fps)}</span>
                  </button>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {ask.modal}
      <Modal open={help} onClose={() => setHelp(false)} eyebrow="Vérification" title="Raccourcis clavier" width={560}>
        <div className="cz-help">
          {KEYS.map(([k, l]) => (
            <div key={k} className="cz-help-row">
              <kbd className="kbd">{k}</kbd>
              <span>{l}</span>
            </div>
          ))}
        </div>
      </Modal>
    </div>
  );
}

function boxesAt(tracks: Track[], manual: Manual[], f: number) {
  const out: { box: Box; color: string; manual?: string; hidden?: boolean }[] = [];
  for (const t of tracks) {
    if (t.f0 > f || t.f1 < f) continue;
    out.push({ box: t.boxes[f - t.f0], color: CLASS_COLOR[t.cls] ?? "#fff", hidden: t.hidden });
  }
  for (const m of manual) {
    const b = manualAt(m, f);
    if (b) out.push({ box: b, color: CLASS_COLOR.manual, manual: m.id });
  }
  return out;
}

const KEYS: [string, string][] = [
  ["Espace / K", "lecture / pause"],
  ["← →", "image par image"],
  ["J / L", "10 s avant / après"],
  ["N / P", "zone suivante / précédente"],
  ["V", "valider la zone"],
  ["X", "retirer la zone"],
  ["B", "aperçu flouté"],
  ["Maj + ← →", "1 s avant / après"],
  [", .", "image par image"],
  ["Début / Fin", "début / fin de la vidéo"],
  ["[ ]", "vitesse"],
  ["M", "son"],
  ["F", "plein écran"],
  ["I / O", "début / fin d'un passage plein cadre"],
  ["A", "zone à la main qui suit"],
  ["Z", "zone à la main fixe"],
  ["R / E", "recadrer / finir ici (zone choisie)"],
  ["Suppr", "retirer la zone"],
  ["Échap", "annuler"],
  ["?", "cette aide"],
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
      <input type="range" aria-label={label} min={min} max={max} value={value} onChange={(e) => onChange(Number(e.target.value))} style={{ ["--pct" as any]: `${((value - min) / (max - min)) * 100}%` }} />
      <div className="row faint cz-range-ends">
        <span>{left}</span>
        <span className="spacer" />
        <span>{right}</span>
      </div>
    </div>
  );
}
