import clsx from "clsx";
import { Clock, FileText, ImageIcon, ScanText, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, token } from "../../api/client";
import { Button, Modal, Segmented, Switch } from "../../components/ui";

export type SegmentMode = "full" | "ai";
export interface Segment {
  id: string;
  f0: number;
  f1: number;
  mode: SegmentMode;
  label?: string;
}

interface Range {
  start: number;
  end: number;
  line: number;
  text: string;
  point: boolean;
  note: string;
}
interface Skipped {
  line: number;
  text: string;
  reason: string;
}
interface Parsed {
  ranges: Range[];
  skipped: Skipped[];
  lines?: string[];
}
interface Row {
  key: string;
  start: string;
  end: string;
  mode: SegmentMode;
  keep: boolean;
  src: Range;
}

const MODES = [
  { value: "full" as const, label: "Plein cadre" },
  { value: "ai" as const, label: "Zones IA" },
];

/** 1:02:03.500 (ou 2:03.5 sous une heure). */
export function tc(t: number): string {
  const ms = Math.round(t * 1000);
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  const r = ms % 1000;
  const base = h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
  return r ? `${base}.${String(r).padStart(3, "0").replace(/0+$/, "")}` : base;
}

/** « 1:02:03.5 », « 12:34 » ou « 754 » (secondes) → secondes ; null si illisible. */
export function parseTc(v: string): number | null {
  const m = v.trim().replace(",", ".").match(/^(?:(\d+):)?(?:(\d+):)?(\d+(?:\.\d+)?)$/);
  if (!m) return null;
  const parts = [m[1], m[2]].filter((x) => x !== undefined).map(Number);
  const sec = Number(m[3]);
  if (parts.length && sec >= 60) return null;
  if (parts.length === 2 && parts[1] >= 60) return null;
  const [h, mi] = parts.length === 2 ? parts : [0, parts[0] ?? 0];
  return h * 3600 + mi * 60 + sec;
}

export function Passages({ pid, fps, frames, segments, aiOutside, margin, frame, defaultMode = "full", onSeek, onSaved, onError, setOpt }: {
  pid: string;
  fps: number;
  frames: number;
  segments: Segment[];
  aiOutside: boolean;
  margin: number;
  frame: number;
  defaultMode?: SegmentMode;
  onSeek: (f: number) => void;
  onSaved: () => void;
  onError: (e: unknown) => void;
  setOpt: (k: "ai_outside" | "point_margin", v: boolean | number) => void;
}) {
  const [open, setOpen] = useState(false);
  const put = (next: Segment[]) => api.put(`/api/censor/projects/${pid}/segments`, { segments: next }).then(onSaved);
  const save = (next: Segment[]) => put(next).catch(onError);
  const add = (rows: { start: number; end: number; mode: SegmentMode; label: string }[]) => {
    const made = rows.map((r) => ({
      id: Math.random().toString(36).slice(2, 10),
      f0: Math.max(0, Math.floor(r.start * fps + 1e-6)),
      f1: Math.min(frames - 1, Math.max(Math.floor(r.start * fps + 1e-6), Math.ceil(r.end * fps - 1e-6) - 1)),
      mode: r.mode,
      label: r.label,
    }));
    return put([...segments, ...made].sort((a, b) => a.f0 - b.f0));
  };

  return (
    <div className="card card-pad cz-passages">
      <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
        <span className="label">Passages par timecodes</span>
        <span className="spacer" />
        <Button size="sm" variant="outline" icon={<Clock size={14} />} tip="Colle une liste, ouvre un fichier ou une capture d'écran" onClick={() => setOpen(true)}>
          Ajouter des timecodes
        </Button>
      </div>
      {segments.length === 0 ? (
        <p className="faint" style={{ fontSize: 12, margin: "8px 0 0" }}>
          Tu connais déjà les passages à censurer ? Colle leurs timecodes (« 12:34 - 13:10 »), ouvre un .txt, .srt ou .csv, ou une capture d'écran : ils sont lus ici, sur ton PC. Par défaut, toute l'image est floutée pendant ces passages.
        </p>
      ) : (
        <>
          <div className="cz-seglist">
            {segments.map((s) => (
              <div key={s.id} className={clsx("cz-seg", s.f0 <= frame && frame <= s.f1 && "now")}>
                <button className="cz-seg-time mono" onClick={() => onSeek(s.f0)} title={s.label || undefined}>
                  {tc(s.f0 / fps)} → {tc((s.f1 + 1) / fps)}
                </button>
                <Segmented size="sm" value={s.mode} onChange={(v) => save(segments.map((x) => (x.id === s.id ? { ...x, mode: v } : x)))} options={MODES} />
                <Button size="sm" variant="ghost" iconOnly icon={<Trash2 size={13} />} tip="Retirer ce passage" onClick={() => save(segments.filter((x) => x.id !== s.id))} />
              </div>
            ))}
          </div>
          <label className="row clip-check" style={{ marginTop: 10 }} data-tip="Coupé : en dehors des passages « Zones IA », les zones trouvées par l'IA ne sont plus floutées">
            <Switch checked={aiOutside} onChange={(v) => setOpt("ai_outside", v)} />
            <span>Zones de l'IA aussi en dehors de ces passages</span>
          </label>
        </>
      )}
      <ImportTimecodes open={open} onClose={() => setOpen(false)} pid={pid} margin={margin} defaultMode={defaultMode} setMargin={(v) => setOpt("point_margin", v)} onAdd={(rows) => add(rows).then(() => setOpen(false)).catch(onError)} onError={onError} />
    </div>
  );
}

function ImportTimecodes({ open, onClose, pid, margin, defaultMode, setMargin, onAdd, onError }: {
  open: boolean;
  defaultMode: SegmentMode;
  onClose: () => void;
  pid: string;
  margin: number;
  setMargin: (v: number) => void;
  onAdd: (rows: { start: number; end: number; mode: SegmentMode; label: string }[]) => void;
  onError: (e: unknown) => void;
}) {
  const [text, setText] = useState("");
  const [fromImage, setFromImage] = useState(false);
  const [reading, setReading] = useState<"" | "text" | "image">("");
  const [parsed, setParsed] = useState<Parsed | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const imgRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) {
      setText("");
      setParsed(null);
      setRows([]);
      setFromImage(false);
    }
  }, [open]);

  // le texte est relu dès qu'on arrête de taper (ou que la marge change)
  useEffect(() => {
    if (!open || !text.trim()) {
      setParsed(null);
      setRows([]);
      return;
    }
    const h = setTimeout(() => {
      setReading("text");
      api
        .post<Parsed>(`/api/censor/projects/${pid}/timecodes`, { text, margin, ocr: fromImage })
        .then((r) => {
          setParsed(r);
          setRows((old) => {
            const prev = new Map(old.map((o) => [o.key, o]));
            return r.ranges.map((x, i) => {
              const key = `${x.line}-${i}`;
              const o = prev.get(key);
              return { key, start: tc(x.start), end: tc(x.end), mode: o?.mode ?? defaultMode, keep: o?.keep ?? true, src: x };
            });
          });
        })
        .catch(onError)
        .finally(() => setReading(""));
    }, 350);
    return () => clearTimeout(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, margin, open, pid, fromImage]);

  const readImage = async (blob: Blob) => {
    setReading("image");
    try {
      const res = await fetch(`/api/censor/projects/${pid}/timecodes/image?margin=${margin}`, {
        method: "POST",
        headers: { "X-MediaLab-Token": token, "Content-Type": blob.type || "application/octet-stream" },
        body: blob,
      });
      const data = await res.json();
      if (!res.ok) throw data;
      setFromImage(true);
      setText((data.lines as string[]).join("\n")); // le texte lu reste modifiable avant d'être appliqué
    } catch (e) {
      onError(e);
    } finally {
      setReading("");
    }
  };

  const onPaste = (e: React.ClipboardEvent) => {
    const item = [...e.clipboardData.items].find((i) => i.type.startsWith("image/"));
    const blob = item?.getAsFile();
    if (blob) {
      e.preventDefault();
      readImage(blob);
    }
  };

  const valid = rows.map((r) => {
    const a = parseTc(r.start);
    const b = parseTc(r.end);
    return a !== null && b !== null && b >= a ? { start: a, end: b } : null;
  });
  const chosen = rows.flatMap((r, i) => (r.keep && valid[i] ? [{ ...valid[i]!, mode: r.mode, label: r.src.text }] : []));
  const setRow = (k: string, patch: Partial<Row>) => setRows(rows.map((r) => (r.key === k ? { ...r, ...patch } : r)));

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Passages à censurer"
      title="Ajouter des timecodes"
      width={760}
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>Annuler</Button>
          <Button variant="primary" disabled={!chosen.length} onClick={() => onAdd(chosen)}>
            {chosen.length ? `Ajouter ${chosen.length} passage${chosen.length > 1 ? "s" : ""}` : "Ajouter"}
          </Button>
        </>
      }
    >
      <div className="cz-tc">
        <textarea
          className="cz-tc-text mono"
          value={text}
          placeholder={"Colle tes timecodes, un passage par ligne :\n00:12:34 - 00:13:10\n54:34 (un instant seul : ± la marge ci-dessous)\n1:02:03.500 → 1:02:10\n…ou colle directement une capture d'écran (Ctrl+V)"}
          onChange={(e) => { setText(e.target.value); if (!e.target.value) setFromImage(false); }}
          onPaste={onPaste}
          spellCheck={false}
        />
        <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
          <Button size="sm" variant="outline" icon={<FileText size={14} />} onClick={() => fileRef.current?.click()}>Ouvrir un fichier (.txt, .srt, .csv)</Button>
          <Button size="sm" variant="outline" icon={<ImageIcon size={14} />} loading={reading === "image"} onClick={() => imgRef.current?.click()}>Lire une capture d'écran</Button>
          <span className="spacer" />
          <label className="row faint" style={{ gap: 6, fontSize: 12 }} data-tip="Un timecode seul devient un passage qui commence autant avant et finit autant après">
            Timecode seul : ±
            <input className="cz-tc-num mono" type="number" min={0} max={60} step={0.5} value={margin} onChange={(e) => setMargin(Math.max(0, Math.min(60, Number(e.target.value) || 0)))} />
            s
          </label>
          <input ref={fileRef} type="file" accept=".txt,.srt,.csv,.tsv,.vtt,text/plain" hidden onChange={async (e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) { setFromImage(false); setText(await f.text()); } }} />
          <input ref={imgRef} type="file" accept="image/*" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) readImage(f); }} />
        </div>
        {fromImage && (
          <p className="faint" style={{ fontSize: 12, margin: "8px 0 0" }}>
            <ScanText size={12} style={{ verticalAlign: -2 }} /> Texte lu sur la capture, sur ton PC : corrige-le au besoin, la liste se met à jour.
          </p>
        )}

        {parsed && (
          <div className="cz-tc-result">
            <div className="label" style={{ margin: "14px 0 6px" }}>
              {rows.length ? `${rows.length} passage${rows.length > 1 ? "s" : ""} trouvé${rows.length > 1 ? "s" : ""} — vérifie avant d'ajouter` : "Aucun passage trouvé"}
            </div>
            {rows.map((r, i) => (
              <div key={r.key} className={clsx("cz-tc-row", !r.keep && "off")}>
                <input type="checkbox" checked={r.keep} onChange={(e) => setRow(r.key, { keep: e.target.checked })} />
                <input className={clsx("cz-tc-time mono", parseTc(r.start) === null && "bad")} value={r.start} onChange={(e) => setRow(r.key, { start: e.target.value })} />
                <span className="faint">→</span>
                <input className={clsx("cz-tc-time mono", (parseTc(r.end) === null || !valid[i]) && "bad")} value={r.end} onChange={(e) => setRow(r.key, { end: e.target.value })} />
                <Segmented size="sm" value={r.mode} onChange={(v) => setRow(r.key, { mode: v })} options={MODES} />
                <span className="faint ellipsis cz-tc-src" title={r.src.text}>
                  l. {r.src.line}{r.src.point ? " · instant" : ""}{r.src.note ? ` · ${r.src.note}` : ""}
                </span>
              </div>
            ))}
            {rows.length > 1 && (
              <div className="row" style={{ gap: 8, marginTop: 6 }}>
                <span className="faint" style={{ fontSize: 12 }}>Pour tous :</span>
                <Button size="sm" variant="ghost" onClick={() => setRows(rows.map((r) => ({ ...r, mode: "full" })))}>Plein cadre</Button>
                <Button size="sm" variant="ghost" onClick={() => setRows(rows.map((r) => ({ ...r, mode: "ai" })))}>Zones IA</Button>
              </div>
            )}
            {parsed.skipped.length > 0 && (
              <div className="cz-tc-skipped">
                <div className="label" style={{ margin: "12px 0 4px" }}>Lignes ignorées</div>
                {parsed.skipped.map((s) => (
                  <div key={s.line} className="faint" style={{ fontSize: 12 }}>
                    l. {s.line} <span className="mono">« {s.text.length > 60 ? s.text.slice(0, 60) + "…" : s.text} »</span> : {s.reason}
                  </div>
                ))}
              </div>
            )}
            <p className="faint" style={{ fontSize: 12, margin: "12px 0 0" }}>
              <b>Plein cadre</b> : toute l'image est floutée pendant le passage (rien ne peut passer). <b>Zones IA</b> : seules les zones trouvées par l'IA sont floutées, l'image reste regardable.
            </p>
          </div>
        )}
      </div>
    </Modal>
  );
}
