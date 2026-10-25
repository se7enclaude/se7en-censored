import { motion } from "motion/react";
import { File, FolderOpen, History, LayoutGrid, List, PlayCircle, RefreshCcw, RotateCcw, Scissors, Search, Sparkles, Trash2 } from "lucide-react";
import { ReactNode, useEffect, useMemo, useState } from "react";
import { api, thumbUrl } from "../api/client";
import type { HistoryItem } from "../api/types";
import { Badge, Button, Empty, Modal, PageHead, PlatformChip, Segmented, Switch, Thumb } from "../components/ui";
import { useT } from "../i18n";
import { bytes, duration, relative } from "../lib/format";
import { useApp } from "../store/app";

type Kind = "" | "download" | "upscale" | "convert" | "trim" | "studio" | "subtitles";
const KIND_ICON: Record<string, ReactNode> = { upscale: <Sparkles size={20} />, convert: <RefreshCcw size={20} />, trim: <Scissors size={20} /> };

export function HistoryPage() {
  const t = useT();
  const version = useApp((s) => s.historyVersion);
  const toast = useApp((s) => s.toast);
  const setPage = useApp((s) => s.setPage);
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<Kind>("");
  const [platform, setPlatform] = useState("");
  const [view, setView] = useState<"grid" | "list">(() => {
    try {
      return (localStorage.getItem("medialab.historyView") as "grid" | "list") || "grid";
    } catch {
      return "grid";
    }
  });
  const [loading, setLoading] = useState(true);
  const [confirm, setConfirm] = useState<HistoryItem | null>(null);
  const [deleteFile, setDeleteFile] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  const load = async () => {
    const params = new URLSearchParams({ q, kind, platform });
    const r = await api.get<{ items: HistoryItem[] }>(`/api/history?${params}`);
    setItems(r.items);
    setLoading(false);
  };
  useEffect(() => {
    const h = window.setTimeout(load, 150);
    return () => window.clearTimeout(h);
  }, [q, kind, platform, version]); // eslint-disable-line react-hooks/exhaustive-deps

  const platforms = useMemo(() => {
    const m = new Map<string, string>();
    items.forEach((i) => i.platform && m.set(i.platform, i.kind !== "download" ? t(`queue.kindLabel.${i.kind}`) : i.meta?.platform_name || i.platform));
    return [...m.entries()];
  }, [items, t]);

  const open = (i: HistoryItem, reveal: boolean) =>
    api.post("/api/system/open", { path: i.file_path, reveal }).catch((e) => toast({ kind: "error", title: e.message, body: e.hint }));
  const again = async (i: HistoryItem) => {
    await api.post(`/api/history/${i.id}/again`);
    toast({ kind: "success", title: t("history.requeued"), action: { label: t("nav.queue"), run: () => setPage("queue") } });
  };
  const remove = async () => {
    if (!confirm) return;
    await api.del(`/api/history/${confirm.id}?delete_file=${deleteFile}`);
    setItems((xs) => xs.filter((x) => x.id !== confirm.id));
    setConfirm(null);
  };

  return (
    <div className="page-inner">
      <PageHead
        eyebrow={t("history.eyebrow", { n: items.length })}
        title={t("history.title")}
        right={
          <Button size="sm" variant="ghost" className="btn-danger" icon={<Trash2 size={13} />} disabled={!items.length} onClick={() => setConfirmClear(true)}>
            {t("history.clear")}
          </Button>
        }
      />

      <div className="row" style={{ marginBottom: 16, flexWrap: "wrap", gap: 10 }}>
        <div className="search">
          <Search size={15} />
          <input className="input" placeholder={t("history.search")} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <Segmented<Kind>
          value={kind}
          onChange={setKind}
          options={[
            { value: "", label: t("queue.filter.all") },
            { value: "download", label: t("queue.filter.download") },
            { value: "upscale", label: t("queue.filter.upscale") },
            { value: "convert", label: t("nav.convert") },
            { value: "trim", label: t("nav.trim") },
            { value: "studio", label: t("nav.studio") },
            { value: "subtitles", label: t("nav.subtitles") },
          ]}
        />
        {platforms.length > 1 && (
          <div className="row" style={{ gap: 6, flexWrap: "wrap" }}>
            {platforms.map(([key, name]) => (
              <button key={key} className={"chip " + (platform === key ? "on" : "")} onClick={() => setPlatform(platform === key ? "" : key)}>
                {name}
              </button>
            ))}
          </div>
        )}
        <div className="spacer" />
        <Segmented
          value={view}
          onChange={(v) => {
            setView(v);
            try {
              localStorage.setItem("medialab.historyView", v);
            } catch {
              /* ignore */
            }
          }}
          options={[
            { value: "grid", label: "", icon: <LayoutGrid size={14} /> },
            { value: "list", label: "", icon: <List size={14} /> },
          ]}
        />
      </div>

      {!loading && items.length === 0 ? (
        <Empty icon={<History size={26} />} title={t("history.emptyTitle")} body={t("history.emptyBody")} />
      ) : view === "grid" ? (
        <div className="history-grid">
          {items.map((i, k) => (
            <motion.div key={i.id} className="card h-card" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(k, 12) * 0.05, duration: 0.5, ease: [0.16, 1, 0.3, 1] }}>
              <div style={{ position: "relative" }}>
                <Thumb src={thumbUrl(i.thumbnail)} badge={i.duration ? duration(i.duration) : undefined} icon={KIND_ICON[i.kind]} />
                {!i.exists && <div className="missing">{t("history.missing")}</div>}
              </div>
              <div className="h-card-body">
                <div className="h-card-title ellipsis selectable" title={i.title}>{i.title || i.file_path}</div>
                <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                  <PlatformChip platform={i.platform} name={i.kind !== "download" ? t(`queue.kindLabel.${i.kind}`) : i.meta?.platform_name || i.platform} />
                  {i.resolution && <Badge mono>{i.resolution}</Badge>}
                  {i.format && <Badge mono style={{ maxWidth: 170, overflow: "hidden" }}>{i.format}</Badge>}
                </div>
                <div className="row faint mono" style={{ fontSize: 11 }}>
                  <span>{relative(i.created_at, t.lang)}</span>
                  <span className="spacer" />
                  <span className="tabular">{bytes(i.size)}</span>
                </div>
                <div className="faint ellipsis mono" style={{ fontSize: 10.5 }} title={i.folder}>{i.folder}</div>
              </div>
              <HistoryActions item={i} onOpen={open} onAgain={again} onDelete={(x) => { setDeleteFile(false); setConfirm(x); }} />
            </motion.div>
          ))}
        </div>
      ) : (
        <div className="card">
          {items.map((i) => (
            <div key={i.id} className="h-row">
              <Thumb src={thumbUrl(i.thumbnail)} icon={KIND_ICON[i.kind]} />
              <div style={{ minWidth: 0 }}>
                <div className="ellipsis" style={{ fontWeight: 600 }}>{i.title}</div>
                <div className="row faint" style={{ fontSize: 12, gap: 10 }}>
                  <PlatformChip platform={i.platform} name={i.kind !== "download" ? t(`queue.kindLabel.${i.kind}`) : i.meta?.platform_name || i.platform} />
                  <span>{relative(i.created_at, t.lang)}</span>
                  {i.resolution && <span>{i.resolution}</span>}
                  {i.format && <span>{i.format}</span>}
                  <span className="tabular">{bytes(i.size)}</span>
                  <span className="ellipsis">{i.folder}</span>
                  {!i.exists && <span style={{ color: "var(--warning)" }}>{t("history.missing")}</span>}
                </div>
              </div>
              <HistoryActions item={i} onOpen={open} onAgain={again} onDelete={(x) => { setDeleteFile(false); setConfirm(x); }} />
            </div>
          ))}
        </div>
      )}

      <Modal
        open={!!confirm}
        onClose={() => setConfirm(null)}
        eyebrow={t("history.title")}
        title={t("history.deleteTitle")}
        actions={
          <>
            <Button onClick={() => setConfirm(null)}>{t("common.cancel")}</Button>
            <Button variant="primary" onClick={remove}>{t("common.delete")}</Button>
          </>
        }
      >
        <p className="muted">{t("history.deleteBody")}</p>
        {confirm?.exists && (
          <div className="toggle-row" style={{ marginTop: 12 }}>
            <div className="toggle-row-text">
              <div className="toggle-row-title">{t("history.alsoFile")}</div>
              <div className="toggle-row-desc">{t("history.alsoFileDesc")}</div>
            </div>
            <Switch checked={deleteFile} onChange={setDeleteFile} />
          </div>
        )}
      </Modal>
      <Modal
        open={confirmClear}
        onClose={() => setConfirmClear(false)}
        title={t("history.clearTitle")}
        actions={
          <>
            <Button onClick={() => setConfirmClear(false)}>{t("common.cancel")}</Button>
            <Button
              variant="primary"
              onClick={async () => {
                await api.del("/api/history");
                setItems([]);
                setConfirmClear(false);
              }}
            >
              {t("history.clear")}
            </Button>
          </>
        }
      >
        <p className="muted">{t("history.clearBody")}</p>
      </Modal>
    </div>
  );
}

function HistoryActions({ item, onOpen, onAgain, onDelete }: { item: HistoryItem; onOpen: (i: HistoryItem, reveal: boolean) => void; onAgain: (i: HistoryItem) => void; onDelete: (i: HistoryItem) => void }) {
  const t = useT();
  const openInPlayer = useApp((s) => s.openInPlayer);
  return (
    <div className="h-actions">
      <Button size="sm" variant="ghost" iconOnly icon={<PlayCircle size={15} />} tip={t("player.play")} disabled={!item.exists || !item.file_path} onClick={() => openInPlayer([item.file_path!])} />
      <Button size="sm" variant="ghost" iconOnly icon={<File size={15} />} tip={t("common.openFile")} disabled={!item.exists} onClick={() => onOpen(item, false)} />
      <Button size="sm" variant="ghost" iconOnly icon={<FolderOpen size={15} />} tip={t("common.showInFolder")} disabled={!item.exists} onClick={() => onOpen(item, true)} />
      <Button size="sm" variant="ghost" iconOnly icon={<RotateCcw size={15} />} tip={item.kind === "download" ? t("history.downloadAgain") : t("history.upscaleAgain")} onClick={() => onAgain(item)} />
      <span className="spacer" />
      <Button size="sm" variant="ghost" iconOnly className="btn-danger" icon={<Trash2 size={15} />} tip={t("history.delete")} onClick={() => onDelete(item)} />
    </div>
  );
}
