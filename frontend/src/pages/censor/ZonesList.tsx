import clsx from "clsx";
import { Check, Undo2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Button, Segmented } from "../../components/ui";
import { tc } from "./Passages";

export interface ZoneItem {
  id: string;
  cls: string;
  f0: number;
  f1: number;
  hidden?: boolean;
  validated?: boolean;
}
type Filter = "todo" | "ok" | "off";

/** Les zones proposées par l'IA, à trier : on valide celles qui sont justes, on retire les autres (après
 *  confirmation, elles quittent la liste ; « Retirées » permet de revenir en arrière). */
export function ZonesList({ tracks, fps, frame, labels, colors, onSeek, onValidate, onUnvalidate, onRemove, onRestore }: {
  tracks: ZoneItem[];
  fps: number;
  frame: number;
  labels: Record<string, string>;
  colors: Record<string, string>;
  onSeek: (f: number) => void;
  onValidate: (t: ZoneItem) => void;
  onUnvalidate: (t: ZoneItem) => void;
  onRemove: (t: ZoneItem) => void;
  onRestore: (t: ZoneItem) => void;
}) {
  const [filter, setFilter] = useState<Filter>("todo");
  const groups = useMemo(() => ({
    todo: tracks.filter((t) => !t.hidden && !t.validated),
    ok: tracks.filter((t) => !t.hidden && t.validated),
    off: tracks.filter((t) => t.hidden),
  }), [tracks]);
  const list = groups[filter];
  const shown = list.slice(0, 500);
  const listRef = useRef<HTMLDivElement | null>(null);
  const current = shown.find((t) => t.f0 <= frame && frame <= t.f1)?.id;
  // la zone de l'image en cours reste visible dans la liste
  useEffect(() => {
    if (!current) return;
    listRef.current?.querySelector(`[data-id="${CSS.escape(current)}"]`)?.scrollIntoView({ block: "nearest" });
  }, [current]);

  return (
    <div className="cz-zones">
      <Segmented
        size="sm"
        full
        value={filter}
        onChange={setFilter}
        options={[
          { value: "todo", label: `À vérifier · ${groups.todo.length}` },
          { value: "ok", label: `Validées · ${groups.ok.length}` },
          { value: "off", label: `Retirées · ${groups.off.length}` },
        ]}
      />
      <div className="cz-zone-list" ref={listRef}>
        {shown.length === 0 && (
          <p className="faint cz-empty">
            {filter === "todo" ? (tracks.length ? "Tout est vérifié. Les zones validées restent floutées." : "Aucune zone trouvée avec ces réglages.")
              : filter === "ok" ? "Aucune zone validée pour l'instant : ✓ sur une zone juste (ou V sur l'image)."
                : "Aucune zone retirée."}
          </p>
        )}
        {shown.map((t) => (
          <div key={t.id} data-id={t.id} className={clsx("cz-item", t.id === current && "now")}>
            <button className="cz-item-main" onClick={() => onSeek(t.f0 + Math.min(Math.round(fps * 0.3), (t.f1 - t.f0) >> 1))}>
              <span className="cz-dot" style={{ background: colors[t.cls] }} />
              <span className="mono">{tc(Math.floor(t.f0 / fps))}</span>
              <span className="ellipsis">{labels[t.cls] ?? t.cls}</span>
              <span className="faint mono">{((t.f1 - t.f0 + 1) / fps).toFixed(1).replace(".", ",")} s</span>
            </button>
            {filter === "todo" && (
              <>
                <Button size="sm" variant="ghost" iconOnly className="cz-ok" icon={<Check size={14} />} tip="C'est juste : garder floutée (V)" onClick={() => onValidate(t)} />
                <Button size="sm" variant="ghost" iconOnly className="cz-no" icon={<X size={14} />} tip="Pas une zone intime : retirer (X)" onClick={() => onRemove(t)} />
              </>
            )}
            {filter === "ok" && <Button size="sm" variant="ghost" iconOnly icon={<Undo2 size={13} />} tip="Remettre à vérifier" onClick={() => onUnvalidate(t)} />}
            {filter === "off" && <Button size="sm" variant="ghost" iconOnly icon={<Undo2 size={13} />} tip="Flouter à nouveau" onClick={() => onRestore(t)} />}
          </div>
        ))}
        {list.length > shown.length && <p className="faint cz-empty">… et {list.length - shown.length} autres (utilise la frise, N / P).</p>}
      </div>
    </div>
  );
}
