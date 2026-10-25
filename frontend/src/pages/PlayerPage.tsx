import clsx from "clsx";
import { AnimatePresence, motion } from "motion/react";
import { Clapperboard, FolderOpen, Image as ImageIcon, ListMusic, Music2, Plus, Scissors, Search, Trash2, Video, Wand2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, thumbUrl } from "../api/client";
import type { LibraryItem, LocalFile } from "../api/types";
import { MediaPlayer } from "../components/MediaPlayer";
import { Badge, Button, Empty, PageHead, Segmented, Ticks } from "../components/ui";
import { useFileIntake } from "../hooks/useFileIntake";
import { useT } from "../i18n";
import { basename, bytes, duration } from "../lib/format";
import { useApp } from "../store/app";

interface QueueItem {
  path: string;
  name: string;
  kind: string;
  thumbnail?: string | null;
  duration?: number | null;
  size?: number | null;
}

type Tab = "queue" | "library";

export function PlayerPage() {
  const t = useT();
  const page = useApp((s) => s.page);
  const playerQueue = useApp((s) => s.playerQueue);
  const sendFiles = useApp((s) => s.sendFiles);
  const version = useApp((s) => s.historyVersion);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [index, setIndex] = useState(0);
  const [tab, setTab] = useState<Tab>("queue");
  const [library, setLibrary] = useState<LibraryItem[]>([]);
  const [libKind, setLibKind] = useState<"" | "video" | "audio" | "image">("");
  const [q, setQ] = useState("");
  const [shuffle] = useState(false);

  const add = useCallback((files: LocalFile[], play = true) => {
    const items = files.map((f) => ({ path: f.path, name: f.name, kind: f.kind === "animated" ? "image" : f.kind ?? "video", thumbnail: f.thumbnail, duration: f.duration, size: f.size }));
    setQueue((prev) => {
      const known = new Set(prev.map((p) => p.path));
      const fresh = items.filter((i) => !known.has(i.path));
      const next = [...prev, ...fresh];
      if (play) {
        const first = items[0] ? next.findIndex((x) => x.path === items[0].path) : -1;
        if (first >= 0) setIndex(first);
      }
      return next;
    });
  }, []);
  const intake = useFileIntake({ page: "player", kind: "playable", onFiles: (f) => add(f) });

  // "Play" from the history, the cutter, the converter...
  useEffect(() => {
    if (!playerQueue) return;
    intake.addPaths(playerQueue.paths);
    setTab("queue");
  }, [playerQueue?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    api.get<{ items: LibraryItem[] }>("/api/media/library").then((r) => setLibrary(r.items)).catch(() => undefined);
  }, [version]);

  const current = queue[index];
  const next = useCallback(() => {
    if (!queue.length) return;
    setIndex((i) => (shuffle ? Math.floor(Math.random() * queue.length) : (i + 1) % queue.length));
  }, [queue.length, shuffle]);
  const prev = useCallback(() => queue.length && setIndex((i) => (i - 1 + queue.length) % queue.length), [queue.length]);
  const onEnded = useCallback(() => {
    if (index < queue.length - 1) setIndex(index + 1);
  }, [index, queue.length]);

  const libFiltered = useMemo(
    () => library.filter((i) => (!libKind || i.kind === libKind) && (!q || (i.title || i.name).toLowerCase().includes(q.toLowerCase()))),
    [library, libKind, q],
  );

  const kindIcon = (k: string) => (k === "audio" ? <Music2 size={14} /> : k === "image" ? <ImageIcon size={14} /> : <Video size={14} />);

  return (
    <div className="page-inner wide">
      <PageHead
        eyebrow={t("player.eyebrow")}
        title={t("player.title")}
        right={
          <>
            <Button icon={<Plus size={15} />} onClick={intake.pick}>{t("player.open")}</Button>
          </>
        }
      />
      <input {...intake.inputProps} />
      <div className="player-layout">
        <div className="col rise" style={{ gap: 14, minWidth: 0, animationDelay: "60ms" }}>
          {current ? (
            <>
              <MediaPlayer
                key={current.path}
                path={current.path}
                cover={current.thumbnail}
                onEnded={onEnded}
                onPrev={queue.length > 1 ? prev : undefined}
                onNext={queue.length > 1 ? next : undefined}
                active={page === "player"}
              />
              <div className="card row" style={{ padding: "12px 16px", gap: 12, flexWrap: "wrap" }}>
                {kindIcon(current.kind)}
                <span className="ellipsis selectable" style={{ fontWeight: 600, flex: 1, minWidth: 160 }}>{current.name}</span>
                {current.duration ? <Badge mono>{duration(current.duration)}</Badge> : null}
                {current.size ? <Badge mono>{bytes(current.size)}</Badge> : null}
                <Button size="sm" variant="ghost" icon={<Scissors size={13} />} disabled={current.kind === "image"} onClick={() => sendFiles([current.path], "trim")}>{t("player.cut")}</Button>
                <Button size="sm" variant="ghost" icon={<Clapperboard size={13} />} onClick={() => sendFiles([current.path], "convert")}>{t("player.convert")}</Button>
                <Button size="sm" variant="ghost" icon={<Wand2 size={13} />} disabled={current.kind === "audio"} onClick={() => sendFiles([current.path], "upscale")}>{t("player.enhance")}</Button>
                <Button size="sm" variant="ghost" iconOnly icon={<FolderOpen size={14} />} tip={t("common.showInFolder")} onClick={() => api.post("/api/system/open", { path: current.path, reveal: true })} />
              </div>
            </>
          ) : (
            <div className="player-empty" onClick={intake.pick}>
              <Ticks inset={14} />
              <div>
                <div className="empty-icon" style={{ borderColor: "var(--accent)", color: "var(--accent)" }}>
                  <ListMusic size={24} />
                </div>
                <div className="empty-title">{t("player.emptyTitle")}</div>
                <div className="faint" style={{ maxWidth: 420, margin: "0 auto" }}>{t("player.emptyBody")}</div>
                <Button variant="primary" style={{ marginTop: 18 }} icon={<Plus size={16} />} onClick={(e) => { e.stopPropagation(); intake.pick(); }}>
                  {t("player.open")}
                </Button>
                <div className="label" style={{ marginTop: 14 }}>{t("player.keys")}</div>
              </div>
            </div>
          )}
        </div>

        <div className="card player-side rise" style={{ animationDelay: "120ms" }}>
          <div style={{ padding: 14, borderBottom: "1px solid var(--border-subtle)" }}>
            <Segmented<Tab>
              value={tab}
              onChange={setTab}
              full
              options={[
                { value: "queue", label: `${t("player.queue")} · ${queue.length}` },
                { value: "library", label: `${t("player.library")} · ${library.length}` },
              ]}
            />
          </div>
          {tab === "queue" ? (
            <div className="player-list">
              {queue.length === 0 && <Empty icon={<ListMusic size={22} />} title={t("player.queueEmpty")} body={t("player.queueEmptyBody")} />}
              <AnimatePresence initial={false}>
                {queue.map((it, i) => (
                  <motion.div key={it.path} layout initial={{ opacity: 0, x: 10 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} className={clsx("pl-item", i === index && "on")} onClick={() => setIndex(i)}>
                    <span className="mono faint" style={{ width: 22 }}>{i === index ? "▶" : String(i + 1).padStart(2, "0")}</span>
                    <div className="pl-thumb">{it.thumbnail ? <img src={thumbUrl(it.thumbnail)} alt="" /> : kindIcon(it.kind)}</div>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div className="ellipsis" style={{ fontWeight: 600, fontSize: 13 }}>{it.name}</div>
                      <div className="mono faint" style={{ fontSize: 10.5 }}>{it.duration ? duration(it.duration) : t(`kind.${it.kind}`)}</div>
                    </div>
                    <button
                      className="pbtn"
                      onClick={(e) => {
                        e.stopPropagation();
                        setQueue((xs) => xs.filter((x) => x.path !== it.path));
                        setIndex((cur) => (i < cur ? cur - 1 : Math.min(cur, queue.length - 2)));
                      }}
                    >
                      <X size={13} />
                    </button>
                  </motion.div>
                ))}
              </AnimatePresence>
              {queue.length > 0 && (
                <div className="row" style={{ padding: 12 }}>
                  <Button size="sm" variant="ghost" icon={<Plus size={13} />} onClick={intake.pick}>{t("player.add")}</Button>
                  <span className="spacer" />
                  <Button size="sm" variant="ghost" icon={<Trash2 size={13} />} onClick={() => { setQueue([]); setIndex(0); }}>{t("common.clear")}</Button>
                </div>
              )}
            </div>
          ) : (
            <div className="player-list">
              <div className="col" style={{ padding: 12, gap: 8 }}>
                <div className="search" style={{ minWidth: 0 }}>
                  <Search size={14} />
                  <input className="input input-sm" placeholder={t("history.search")} value={q} onChange={(e) => setQ(e.target.value)} />
                </div>
                <Segmented
                  value={libKind}
                  onChange={setLibKind}
                  full
                  size="sm"
                  options={[
                    { value: "", label: t("queue.filter.all") },
                    { value: "video", label: t("kind.video") },
                    { value: "audio", label: t("kind.audio") },
                    { value: "image", label: t("kind.image") },
                  ]}
                />
              </div>
              {libFiltered.length === 0 && <Empty icon={<Search size={22} />} title={t("player.libraryEmpty")} />}
              {libFiltered.map((it) => (
                <div key={it.path} className="pl-item" onDoubleClick={() => add([{ path: it.path, name: it.name, kind: it.kind, ok: true, thumbnail: it.thumbnail, duration: it.duration ?? undefined, size: it.size ?? undefined }])}>
                  <div className="pl-thumb">{it.thumbnail ? <img src={thumbUrl(it.thumbnail)} alt="" /> : kindIcon(it.kind)}</div>
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className="ellipsis" style={{ fontWeight: 600, fontSize: 13 }} title={it.path}>{it.title || basename(it.path)}</div>
                    <div className="mono faint" style={{ fontSize: 10.5 }}>{[it.duration ? duration(it.duration) : null, it.size ? bytes(it.size) : null].filter(Boolean).join(" · ")}</div>
                  </div>
                  <Button size="sm" variant="ghost" iconOnly icon={<Plus size={14} />} tip={t("player.addToQueue")} onClick={() => add([{ path: it.path, name: it.name, kind: it.kind, ok: true, thumbnail: it.thumbnail, duration: it.duration ?? undefined, size: it.size ?? undefined }], false)} />
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
