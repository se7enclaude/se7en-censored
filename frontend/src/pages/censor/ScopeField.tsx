import { FileText, ImageIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, token } from "../../api/client";
import { Button } from "../../components/ui";
import { tc } from "./Passages";

interface Preview {
  ranges: { start: number; end: number; line: number; point: boolean }[];
  skipped: { line: number; text: string; reason: string }[];
}

/** Timecodes donnés avant l'analyse : seuls ces passages seront analysés. Aperçu de ce qui est lu, en direct. */
export function ScopeField({ text, setText, margin, setMargin, onCount, onError }: {
  text: string;
  setText: (v: string) => void;
  margin: number;
  setMargin: (v: number) => void;
  onCount: (n: number) => void;
  onError: (e: unknown) => void;
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [reading, setReading] = useState(false);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const imgRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!text.trim()) {
      setPreview(null);
      onCount(0);
      return;
    }
    const h = setTimeout(() => {
      api
        .post<Preview>("/api/censor/timecodes", { text, margin })
        .then((r) => { setPreview(r); onCount(r.ranges.length); })
        .catch(onError);
    }, 300);
    return () => clearTimeout(h);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, margin]);

  const readImage = async (blob: Blob) => {
    setReading(true);
    try {
      const res = await fetch(`/api/censor/timecodes/image?margin=${margin}`, {
        method: "POST",
        headers: { "X-MediaLab-Token": token, "Content-Type": blob.type || "application/octet-stream" },
        body: blob,
      });
      const data = await res.json();
      if (!res.ok) throw data;
      setText([text.trim(), ...(data.lines as string[])].filter(Boolean).join("\n"));
    } catch (e) {
      onError(e);
    } finally {
      setReading(false);
    }
  };

  const total = preview?.ranges.reduce((s, r) => s + (r.end - r.start), 0) ?? 0;

  return (
    <div className="cz-scope">
      <textarea
        className="cz-tc-text mono"
        value={text}
        placeholder={"Les passages à vérifier, un par ligne :\n00:12:34 - 00:13:10\n54:34 (un instant seul : ± la marge)\n…ou colle une capture d'écran (Ctrl+V)"}
        onChange={(e) => setText(e.target.value)}
        onPaste={(e) => {
          const blob = [...e.clipboardData.items].find((i) => i.type.startsWith("image/"))?.getAsFile();
          if (blob) { e.preventDefault(); readImage(blob); }
        }}
        spellCheck={false}
      />
      <div className="row" style={{ gap: 8, flexWrap: "wrap", marginTop: 8 }}>
        <Button size="sm" variant="outline" icon={<FileText size={14} />} onClick={() => fileRef.current?.click()}>Fichier (.txt, .srt, .csv)</Button>
        <Button size="sm" variant="outline" icon={<ImageIcon size={14} />} loading={reading} onClick={() => imgRef.current?.click()}>Capture d'écran</Button>
        <span className="spacer" />
        <label className="row faint" style={{ gap: 6, fontSize: 12 }} data-tip="Un timecode seul devient un passage qui commence autant avant et finit autant après">
          Timecode seul : ±
          <input className="cz-tc-num mono" type="number" min={0} max={60} step={0.5} value={margin} onChange={(e) => setMargin(Math.max(0, Math.min(60, Number(e.target.value) || 0)))} />
          s
        </label>
        <input ref={fileRef} type="file" accept=".txt,.srt,.csv,.tsv,.vtt,text/plain" hidden onChange={async (e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) setText(await f.text()); }} />
        <input ref={imgRef} type="file" accept="image/*" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) readImage(f); }} />
      </div>
      {preview && (
        <div className="cz-scope-sum">
          {preview.ranges.length ? (
            <div>
              <b>{preview.ranges.length} passage{preview.ranges.length > 1 ? "s" : ""}</b> · {tc(total)} à analyser :{" "}
              <span className="mono faint">{preview.ranges.slice(0, 6).map((r) => `${tc(r.start)}→${tc(r.end)}`).join("  ")}{preview.ranges.length > 6 ? "  …" : ""}</span>
            </div>
          ) : (
            <div className="faint">Aucun timecode lu pour l'instant.</div>
          )}
          {preview.skipped.slice(0, 4).map((s) => (
            <div key={s.line} className="faint">l. {s.line} ignorée : {s.reason}</div>
          ))}
          {preview.skipped.length > 4 && <div className="faint">… et {preview.skipped.length - 4} autres lignes ignorées</div>}
        </div>
      )}
    </div>
  );
}
