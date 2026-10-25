import clsx from "clsx";
import { motion } from "motion/react";
import { AudioLines, Copy, FolderOpen, FolderPlus, Image as ImageIcon, Play, ScanSearch, Square, Trash2, Video, X } from "lucide-react";
import { ReactNode, useEffect, useMemo, useState } from "react";
import { api, thumbUrl } from "../api/client";
import type { DupGroup, TaskState } from "../api/types";
import { Badge, Button, Check, Empty, ErrorAlert, Field, Modal, Notice, PageHead, Progress, Segmented } from "../components/ui";
import { useT } from "../i18n";
import { bytes, duration, relative } from "../lib/format";
import { useApp } from "../store/app";

interface ScanResult {
  groups: DupGroup[];
  scanned: number;
  folders: string[];
  wasted: number;
}

const KIND_ICON: Record<string, ReactNode> = { video: <Video size={13} />, audio: <AudioLines size={13} />, image: <ImageIcon size={13} /> };

export function DuplicatesPage() {
  const t = useT();
  const settings = useApp((s) => s.settings);
  const save = useApp((s) => s.saveSettings);
  const tasks = useApp((s) => s.tasks);
  const toast = useApp((s) => s.toast);
  const openInPlayer = useApp((s) => s.openInPlayer);
  const defaults = useMemo(() => {
    const f = settings?.tools?.duplicate_folders ?? [];
    if (f.length) return f;
    return [settings?.download.default_folder, settings?.ai.output_folder].filter(Boolean) as string[];
  }, [settings]);
  const [folders, setFolders] = useState<string[]>(defaults);
  const [kinds, setKinds] = useState<string[]>(["video", "audio", "image"]);
  const [similar, setSimilar] = useState(true);
  const [sensitivity, setSensitivity] = useState("6");
  const [taskId, setTaskId] = useState<string | null>(null);
  const [result, setResult] = useState<ScanResult | null>(null);
  const [error, setError] = useState<any>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirm, setConfirm] = useState(false);
  const [trashing, setTrashing] = useState(false);

  useEffect(() => setFolders(defaults), [defaults.join("|")]); // eslint-disable-line react-hooks/exhaustive-deps

  const task: TaskState | undefined = taskId ? tasks[taskId] : undefined;
  const running = task?.state === "running";

  // safety net if a progress event is missed (sleep, reconnection)
  useEffect(() => {
    if (!running || !taskId) return;
    const iv = window.setInterval(async () => {
      try {
        const r = await api.get<TaskState>(`/api/tasks/${taskId}`);
        if (r.state !== "running") useApp.setState({ tasks: { ...useApp.getState().tasks, [r.id]: { ...r, result: undefined } } });
      } catch {
        /* ignore */
      }
    }, 2000);
    return () => window.clearInterval(iv);
  }, [running, taskId]);

  useEffect(() => {
    if (!taskId || !task || task.state === "running") return;
    api.get<TaskState<ScanResult>>(`/api/tasks/${taskId}`).then((r) => {
      if (r.state === "done" && r.result) {
        setResult(r.result);
        // exact copies: preselect everything but the suggested file; similar: the user decides
        setSelected(new Set(r.result.groups.filter((g) => g.type === "exact").flatMap((g) => g.files.filter((f) => !f.keep).map((f) => f.path))));
      } else if (r.state === "failed") setError(r.error);
    });
  }, [task?.state, taskId]); // eslint-disable-line react-hooks/exhaustive-deps

  const addFolder = async () => {
    const r = await api.post<{ path: string | null }>("/api/system/pick-folder", { initial: folders[0] ?? "" });
    if (r.path && !folders.includes(r.path)) {
      const next = [...folders, r.path];
      setFolders(next);
      save({ tools: { duplicate_folders: next } }).catch(() => undefined);
    }
  };
  const removeFolder = (f: string) => {
    const next = folders.filter((x) => x !== f);
    setFolders(next);
    save({ tools: { duplicate_folders: next } }).catch(() => undefined);
  };

  const scan = async () => {
    setError(null);
    setResult(null);
    try {
      const r = await api.post<TaskState>("/api/duplicates/scan", { folders, options: { kinds, similar, threshold: Number(sensitivity) } });
      // an event may already have reported the end (instant scans): never overwrite it
      const known = useApp.getState().tasks;
      useApp.setState({ tasks: { ...known, [r.id]: known[r.id] ?? r } });
      setTaskId(r.id);
    } catch (e) {
      setError(e);
    }
  };

  const selSize = useMemo(() => {
    let n = 0;
    result?.groups.forEach((g) => g.files.forEach((f) => selected.has(f.path) && (n += f.size)));
    return n;
  }, [selected, result]);

  const toggle = (p: string) => {
    const s = new Set(selected);
    if (s.has(p)) s.delete(p);
    else s.add(p);
    setSelected(s);
  };

  const trash = async () => {
    setTrashing(true);
    try {
      const r = await api.post<{ trashed: string[]; failed: string[] }>("/api/files/trash", { paths: [...selected] });
      const gone = new Set(r.trashed);
      setResult((res) =>
        res ? { ...res, groups: res.groups.map((g) => ({ ...g, files: g.files.filter((f) => !gone.has(f.path)) })).filter((g) => g.files.length > 1) } : res,
      );
      setSelected(new Set());
      toast({ kind: r.failed.length ? "warning" : "success", title: t("dupes.trashed", { n: r.trashed.length }), body: r.failed.length ? t("dupes.trashFailed", { n: r.failed.length }) : undefined });
    } finally {
      setTrashing(false);
      setConfirm(false);
    }
  };

  const groups = result?.groups ?? [];
  const exactCount = groups.filter((g) => g.type === "exact").length;

  return (
    <div className="page-inner">
      <PageHead eyebrow={t("dupes.eyebrow")} title={t("dupes.title")} sub={t("dupes.sub")} />

      <div className="card card-pad col rise" style={{ gap: 14, marginBottom: 18 }}>
        <span className="label">{t("dupes.folders")}</span>
        <div className="col" style={{ gap: 6 }}>
          {folders.length === 0 && <span className="faint">{t("dupes.noFolder")}</span>}
          {folders.map((f) => (
            <div key={f} className="res-flow" style={{ justifyContent: "space-between" }}>
              <span className="row" style={{ gap: 8, minWidth: 0 }}><FolderOpen size={14} className="faint" /><span className="mono ellipsis selectable">{f}</span></span>
              <Button size="sm" variant="ghost" iconOnly icon={<X size={13} />} onClick={() => removeFolder(f)} />
            </div>
          ))}
        </div>
        <div className="row" style={{ gap: 14, flexWrap: "wrap", alignItems: "flex-end" }}>
          <Button size="sm" icon={<FolderPlus size={14} />} onClick={addFolder}>{t("dupes.addFolder")}</Button>
          <div className="row" style={{ gap: 12 }}>
            {(["video", "audio", "image"] as const).map((k) => (
              <label key={k} className="check-row" style={{ fontSize: 13 }} onClick={() => setKinds(kinds.includes(k) ? kinds.filter((x) => x !== k) : [...kinds, k])}>
                <Check on={kinds.includes(k)} /> {t(`kind.${k}`)}
              </label>
            ))}
          </div>
          <span className="spacer" />
          <Field label={t("dupes.mode")}>
            <Segmented value={similar ? "similar" : "exact"} onChange={(v) => setSimilar(v === "similar")} options={[{ value: "exact", label: t("dupes.exactOnly") }, { value: "similar", label: t("dupes.withSimilar") }]} />
          </Field>
          {similar && (
            <Field label={t("dupes.sensitivity")}>
              <Segmented value={sensitivity} onChange={setSensitivity} options={[{ value: "3", label: t("dupes.strict") }, { value: "6", label: t("dupes.normal") }, { value: "10", label: t("dupes.loose") }]} />
            </Field>
          )}
          {running ? (
            <Button variant="outline" icon={<Square size={14} />} onClick={() => taskId && api.post(`/api/tasks/${taskId}/cancel`)}>{t("common.cancel")}</Button>
          ) : (
            <Button variant="primary" size="lg" icon={<ScanSearch size={16} />} disabled={!folders.length || !kinds.length} onClick={scan}>{t("dupes.scan")}</Button>
          )}
        </div>
        {running && (
          <div className="col" style={{ gap: 6 }}>
            <Progress value={task?.progress?.percent ?? null} state="active" />
            <span className="faint mono" style={{ fontSize: 11 }}>{t(`dupes.stage.${task?.progress?.label ?? "listing"}`)} · {t("dupes.found", { n: task?.progress?.found ?? 0 })}</span>
          </div>
        )}
        {error && <ErrorAlert error={error} />}
      </div>

      {result && (
        <>
          <div className="stat-row rise" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
            <div className="card stat"><div className="stat-value">{result.scanned}</div><div className="stat-label">{t("dupes.scanned")}</div></div>
            <div className="card stat"><div className="stat-value">{exactCount}</div><div className="stat-label">{t("dupes.exactGroups")}</div></div>
            <div className="card stat"><div className="stat-value">{groups.length - exactCount}</div><div className="stat-label">{t("dupes.similarGroups")}</div></div>
            <div className="card stat"><div className="stat-value" style={{ color: "var(--accent-text)" }}>{bytes(result.wasted)}</div><div className="stat-label">{t("dupes.recoverable")}</div></div>
          </div>
          {groups.length === 0 ? (
            <Empty icon={<Copy size={26} />} title={t("dupes.noneTitle")} body={t("dupes.noneBody")} />
          ) : (
            <>
              <Notice>{t("dupes.howto")}</Notice>
              <div className="col" style={{ gap: 12, marginTop: 14, paddingBottom: 90 }}>
                {groups.map((g, gi) => (
                  <motion.div key={g.files.map((f) => f.path).join("|")} className="card dup-group" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(gi, 10) * 0.04 }}>
                    <div className="row" style={{ padding: "10px 14px", borderBottom: "1px solid var(--border-subtle)", gap: 8 }}>
                      <Badge tone={g.type === "exact" ? "accent" : "info"}>{g.type === "exact" ? t("dupes.exact") : t("dupes.similar")}</Badge>
                      <Badge icon={KIND_ICON[g.kind]}>{t(`kind.${g.kind}`)}</Badge>
                      <span className="faint mono" style={{ fontSize: 11 }}>{t("dupes.files", { n: g.files.length })}</span>
                      <span className="spacer" />
                      {g.wasted > 0 && <span className="mono" style={{ fontSize: 11, color: "var(--accent-text)" }}>{t("dupes.wasted", { size: bytes(g.wasted) })}</span>}
                    </div>
                    {g.files.map((f) => (
                      <div key={f.path} className={clsx("dup-file", selected.has(f.path) && "sel")} onClick={() => toggle(f.path)}>
                        <Check on={selected.has(f.path)} />
                        <div className="pl-thumb" style={{ width: 64, height: 40 }}>{f.thumbnail ? <img src={thumbUrl(f.thumbnail)} alt="" /> : KIND_ICON[f.kind]}</div>
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <div className="row" style={{ gap: 8 }}>
                            <span className="ellipsis" style={{ fontWeight: 600, fontSize: 13 }}>{f.name}</span>
                            {f.keep && <Badge tone="success">{t("dupes.keep")}</Badge>}
                          </div>
                          <div className="mono faint ellipsis" style={{ fontSize: 10.5 }} title={f.folder}>
                            {[f.width && f.height ? `${f.width}×${f.height}` : null, f.duration ? duration(f.duration) : null, bytes(f.size), relative(f.mtime, t.lang), f.folder].filter(Boolean).join(" · ")}
                          </div>
                        </div>
                        <Button size="sm" variant="ghost" iconOnly icon={<Play size={13} />} tip={t("player.play")} onClick={(e) => { e.stopPropagation(); openInPlayer(g.files.map((x) => x.path), g.files.indexOf(f)); }} />
                        <Button size="sm" variant="ghost" iconOnly icon={<FolderOpen size={13} />} tip={t("common.showInFolder")} onClick={(e) => { e.stopPropagation(); api.post("/api/system/open", { path: f.path, reveal: true }); }} />
                      </div>
                    ))}
                  </motion.div>
                ))}
              </div>
              <div className="dup-bar">
                <span className="label">{t("dupes.selected", { n: selected.size })} · <span style={{ color: "var(--accent-text)" }}>{bytes(selSize)}</span></span>
                <span className="spacer" />
                <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>{t("dupes.unselect")}</Button>
                <Button variant="primary" icon={<Trash2 size={15} />} disabled={!selected.size} onClick={() => setConfirm(true)}>{t("dupes.trash")}</Button>
              </div>
            </>
          )}
        </>
      )}

      <Modal
        open={confirm}
        onClose={() => setConfirm(false)}
        eyebrow={t("dupes.eyebrow")}
        title={t("dupes.confirmTitle", { n: selected.size })}
        actions={<><Button onClick={() => setConfirm(false)}>{t("common.cancel")}</Button><Button variant="primary" loading={trashing} onClick={trash}>{t("dupes.trash")}</Button></>}
      >
        <p>{t("dupes.confirmBody", { size: bytes(selSize) })}</p>
      </Modal>
    </div>
  );
}
