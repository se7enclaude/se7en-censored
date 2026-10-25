import clsx from "clsx";
import { ChevronsLeftRight, Combine, Minus, Plus, Redo2, Replace, Scissors, Search, Trash2, Undo2, X } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { SubCue } from "../../api/types";
import { useT } from "../../i18n";
import { cps, flat, fmtTime, parseTime } from "../../lib/subtitles";
import { Button } from "../ui";

const ROW = 78;

/** Editable list of cues (only the visible rows are rendered, so long films stay fluid). */
export function CueEditor({
  cues,
  selected,
  current,
  playing,
  maxCps,
  canUndo,
  canRedo,
  onSelect,
  onSeek,
  onEdit,
  onAdd,
  onDelete,
  onMerge,
  onSplit,
  onShift,
  onReplace,
  onUndo,
  onRedo,
}: {
  cues: SubCue[];
  selected: Set<string>;
  current: string | null;
  playing: boolean;
  maxCps: number;
  canUndo: boolean;
  canRedo: boolean;
  onSelect: (id: string, mode: "one" | "toggle" | "range") => void;
  onSeek: (t: number) => void;
  onEdit: (id: string, patch: Partial<SubCue>, commit: boolean) => void;
  onAdd: () => void;
  onDelete: () => void;
  onMerge: () => void;
  onSplit: (id: string, caret?: number) => void;
  onShift: (seconds: number) => void;
  onReplace: (find: string, replace: string, all: boolean, matchCase: boolean) => number;
  onUndo: () => void;
  onRedo: () => void;
}) {
  const t = useT();
  const list = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [height, setHeight] = useState(500);
  const [search, setSearch] = useState(false);
  const [find, setFind] = useState("");
  const [repl, setRepl] = useState("");
  const [matchCase, setMatchCase] = useState(false);
  const caret = useRef<{ id: string; pos: number } | null>(null);

  useLayoutEffect(() => {
    const el = list.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setHeight(el.clientHeight));
    ro.observe(el);
    setHeight(el.clientHeight);
    return () => ro.disconnect();
  }, []);

  // keep the spoken cue in view while playing
  useEffect(() => {
    const el = list.current;
    if (!el || !playing || !current) return;
    const i = cues.findIndex((c) => c.id === current);
    if (i < 0) return;
    const y = i * ROW;
    if (y < el.scrollTop || y > el.scrollTop + el.clientHeight - ROW) el.scrollTop = Math.max(0, y - el.clientHeight / 3);
  }, [current, playing, cues]);

  const matches = useMemo(() => {
    if (!find) return new Set<string>();
    const f = matchCase ? find : find.toLowerCase();
    return new Set(cues.filter((c) => (matchCase ? c.text : c.text.toLowerCase()).includes(f)).map((c) => c.id));
  }, [cues, find, matchCase]);

  const first = Math.max(0, Math.floor(scrollTop / ROW) - 4);
  const last = Math.min(cues.length, Math.ceil((scrollTop + height) / ROW) + 4);
  const sel = selected.size;

  return (
    <div className="cue-editor">
      <div className="cue-toolbar">
        <Button size="sm" variant="ghost" icon={<Plus size={13} />} tip={t("subs.add")} onClick={onAdd}>{t("subs.addShort")}</Button>
        <Button size="sm" variant="ghost" iconOnly icon={<Combine size={13} />} tip={t("subs.merge")} disabled={sel < 1} onClick={onMerge} />
        <Button size="sm" variant="ghost" iconOnly icon={<Scissors size={13} />} tip={t("subs.split")} disabled={sel !== 1}
          onClick={() => {
            const id = [...selected][0];
            onSplit(id, caret.current?.id === id ? caret.current.pos : undefined);
          }} />
        <Button size="sm" variant="ghost" iconOnly icon={<Trash2 size={13} />} tip={t("subs.delete")} disabled={sel < 1} onClick={onDelete} />
        <span className="cue-toolbar-sep" />
        <Button size="sm" variant="ghost" iconOnly icon={<Minus size={13} />} tip={t("subs.shiftBack")} disabled={sel < 1} onClick={() => onShift(-0.1)} />
        <ChevronsLeftRight size={13} className="faint" />
        <Button size="sm" variant="ghost" iconOnly icon={<Plus size={13} />} tip={t("subs.shiftForward")} disabled={sel < 1} onClick={() => onShift(0.1)} />
        <span className="cue-toolbar-sep" />
        <Button size="sm" variant="ghost" iconOnly icon={<Undo2 size={13} />} tip={t("subs.undo")} disabled={!canUndo} onClick={onUndo} />
        <Button size="sm" variant="ghost" iconOnly icon={<Redo2 size={13} />} tip={t("subs.redo")} disabled={!canRedo} onClick={onRedo} />
        <span className="spacer" />
        <Button size="sm" variant={search ? "default" : "ghost"} iconOnly icon={<Search size={13} />} tip={t("subs.search")} onClick={() => setSearch((v) => !v)} />
      </div>
      {search && (
        <div className="cue-search">
          <input className="input input-sm" autoFocus placeholder={t("subs.find")} value={find} onChange={(e) => setFind(e.target.value)} />
          <input className="input input-sm" placeholder={t("subs.replaceWith")} value={repl} onChange={(e) => setRepl(e.target.value)} />
          <button className={clsx("chip", matchCase && "on")} onClick={() => setMatchCase((v) => !v)} title={t("subs.matchCase")}>Aa</button>
          <span className="mono faint" style={{ fontSize: 10.5, minWidth: 54 }}>{find ? t("subs.matches", { n: matches.size }) : ""}</span>
          <Button size="sm" variant="ghost" icon={<Replace size={13} />} disabled={!find || !matches.size} onClick={() => onReplace(find, repl, false, matchCase)}>{t("subs.replaceOne")}</Button>
          <Button size="sm" disabled={!find || !matches.size} onClick={() => onReplace(find, repl, true, matchCase)}>{t("subs.replaceAll")}</Button>
          <Button size="sm" variant="ghost" iconOnly icon={<X size={13} />} onClick={() => { setSearch(false); setFind(""); }} />
        </div>
      )}
      <div className="cue-list" ref={list} onScroll={(e) => setScrollTop((e.target as HTMLDivElement).scrollTop)}>
        <div style={{ height: cues.length * ROW, position: "relative" }}>
          {cues.slice(first, last).map((c, k) => {
            const i = first + k;
            const speed = cps(c);
            return (
              <div
                key={c.id}
                className={clsx("cue-row", selected.has(c.id) && "on", current === c.id && "live", matches.has(c.id) && "match")}
                style={{ top: i * ROW, height: ROW }}
                onMouseDown={(e) => {
                  const tag = (e.target as HTMLElement).tagName;
                  if (tag === "INPUT" || tag === "TEXTAREA") {
                    if (!selected.has(c.id)) onSelect(c.id, "one");
                    return;
                  }
                  onSelect(c.id, e.ctrlKey || e.metaKey ? "toggle" : e.shiftKey ? "range" : "one");
                  onSeek(c.start + 0.001);
                }}
              >
                <div className="cue-idx mono">{i + 1}</div>
                <div className="cue-times">
                  <TimeInput value={c.start} onCommit={(v) => onEdit(c.id, { start: v }, true)} onFocus={() => onSeek(c.start + 0.001)} />
                  <TimeInput value={c.end} onCommit={(v) => onEdit(c.id, { end: v }, true)} onFocus={() => onSeek(Math.max(c.start, c.end - 0.3))} />
                  <span className={clsx("cue-cps mono", speed > maxCps * 1.15 ? "bad" : speed > maxCps ? "warn" : "ok")} title={t("subs.cpsTip", { n: maxCps })}>
                    {speed.toFixed(0)} {t("subs.cps")} · {(c.end - c.start).toFixed(1)}s
                  </span>
                </div>
                <textarea
                  className="cue-text"
                  value={c.text}
                  rows={2}
                  spellCheck
                  onChange={(e) => onEdit(c.id, { text: e.target.value.split("\n").slice(0, 3).join("\n") }, false)}
                  onBlur={() => onEdit(c.id, {}, true)}
                  onSelect={(e) => {
                    const el = e.target as HTMLTextAreaElement;
                    caret.current = { id: c.id, pos: flat(el.value.slice(0, el.selectionStart)).length };
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                      e.preventDefault();
                      const el = e.target as HTMLTextAreaElement;
                      onSplit(c.id, flat(el.value.slice(0, el.selectionStart)).length);
                    }
                  }}
                />
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function TimeInput({ value, onCommit, onFocus }: { value: number; onCommit: (v: number) => void; onFocus: () => void }) {
  const [text, setText] = useState(fmtTime(value));
  const [bad, setBad] = useState(false);
  useEffect(() => setText(fmtTime(value)), [value]);
  const commit = () => {
    const v = parseTime(text);
    if (v === null) {
      setBad(true);
      return;
    }
    setBad(false);
    if (Math.abs(v - value) > 0.0005) onCommit(v);
    else setText(fmtTime(value));
  };
  return (
    <input
      className={clsx("cue-time mono", bad && "bad")}
      value={text}
      onFocus={onFocus}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "ArrowUp" || e.key === "ArrowDown") {
          e.preventDefault();
          const step = e.shiftKey ? 1 : 0.1;
          onCommit(Math.max(0, value + (e.key === "ArrowUp" ? step : -step)));
        }
      }}
    />
  );
}
