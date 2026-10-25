import { AnimatePresence, motion } from "motion/react";
import {
  Droplets,
  Eye,
  Film,
  FolderOpen,
  Gauge,
  ImageUp,
  Layers,
  Maximize2,
  Palette,
  Plus,
  ScanFace,
  Scissors,
  Sparkles,
  Trash2,
  Waves,
  X,
  Zap,
} from "lucide-react";
import { ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, localMediaUrl, mediaUrl, thumbUrl } from "../api/client";
import type { Job, LocalFile, PreviewResult } from "../api/types";
import { CompareSlider } from "../components/CompareSlider";
import { PixelImage } from "../components/Pixel";
import { Badge, Button, Check, ErrorAlert, Field, Levels, Notice, PageHead, Segmented, Select, Ticks, ToggleRow } from "../components/ui";
import { useKey } from "../hooks/useShortcuts";
import { useT } from "../i18n";
import { bytes, duration, eta } from "../lib/format";
import { useApp } from "../store/app";

const CATEGORIES = ["general", "anime", "realistic", "face", "detail"] as const;
type ScaleMode = "2" | "4" | "8" | "custom";
type Tool = "upscale" | "face" | "background" | "colorize" | "interpolate";
const TOOL_SUFFIX: Record<Tool, string> = { upscale: "", face: "_restored", background: "_nobg", colorize: "_color", interpolate: "_smooth" };
const TOOL_ICON: Record<Tool, ReactNode> = {
  upscale: <Maximize2 size={13} />,
  face: <ScanFace size={13} />,
  background: <Scissors size={13} />,
  colorize: <Palette size={13} />,
  interpolate: <Gauge size={13} />,
};

function even(v: number) {
  return Math.max(2, Math.round(v / 2) * 2);
}
function targetSize(w: number, h: number, mode: ScaleMode, cw: number, ch: number, keep: boolean): [number, number] {
  if (mode !== "custom") return [even(w * Number(mode)), even(h * Number(mode))];
  if (keep) {
    const f = Math.min((cw || 1e9) / w, (ch || 1e9) / h);
    return [even(w * f), even(h * f)];
  }
  return [even(cw || w), even(ch || h)];
}

const WHOLE_PX = 1_000_000;

export function UpscalePage() {
  const t = useT();
  const caps = useApp((s) => s.capabilities);
  const loadCaps = useApp((s) => s.loadCapabilities);
  const settings = useApp((s) => s.settings);
  const hw = useApp((s) => s.hardware);
  const shell = useApp((s) => s.info?.shell);
  const toast = useApp((s) => s.toast);
  const setPage = useApp((s) => s.setPage);
  const goSettings = useApp((s) => s.goSettings);
  const pendingFiles = useApp((s) => s.pendingFiles);
  const consumeFiles = useApp((s) => s.consumeFiles);
  const openInPlayer = useApp((s) => s.openInPlayer);

  const [files, setFiles] = useState<LocalFile[]>([]);
  const [active, setActive] = useState(0);
  const [category, setCategory] = useState<string>("general");
  const [model, setModel] = useState<string>(settings?.ai.default_model ?? "realesrgan-x4plus");
  const [scaleMode, setScaleMode] = useState<ScaleMode>(String(settings?.ai.default_scale ?? 4) as ScaleMode);
  const [cw, setCw] = useState(3840);
  const [ch, setCh] = useState(2160);
  const [keep, setKeep] = useState(true);
  const [denoise, setDenoise] = useState(0);
  const [sharpen, setSharpen] = useState(0);
  const [detail, setDetail] = useState(0);
  const [color, setColor] = useState(0);
  const [face, setFace] = useState(false);
  const [tool, setTool] = useState<Tool>("upscale");
  const [alsoUpscale, setAlsoUpscale] = useState(false);
  const [faceModel, setFaceModel] = useState("gfpgan");
  const [faceStrength, setFaceStrength] = useState(80);
  const [faceFidelity, setFaceFidelity] = useState(60);
  const [bgMode, setBgMode] = useState("transparent");
  const [bgColor, setBgColor] = useState("#ffffff");
  const [bgFeather, setBgFeather] = useState(0);
  const [colSat, setColSat] = useState(100);
  const [colSize, setColSize] = useState("256");
  const [interp, setInterp] = useState("2");
  const [temporal, setTemporal] = useState(settings?.ai.temporal_stabilization ?? true);
  const [imageFormat, setImageFormat] = useState(settings?.ai.image_format ?? "png");
  const [container, setContainer] = useState("mp4");
  const [encoder, setEncoder] = useState(settings?.ai.video_encoder ?? "auto");
  const [vq, setVq] = useState(settings?.ai.video_quality ?? 18);
  const [outputDir, setOutputDir] = useState("");
  const [region, setRegion] = useState<{ x: number; y: number } | null>(null);
  const [zone, setZone] = useState<number | null>(null); // side of the compared area, in source pixels
  const [at, setAt] = useState<number | null>(null);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<any>(null);
  const [starting, setStarting] = useState(false);
  const [over, setOver] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const [stageSize, setStageSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setStageSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    return () => ro.disconnect();
  });

  useEffect(() => {
    if (!caps) loadCaps().catch(() => undefined);
  }, [caps, loadCaps]);

  const file = files[active];
  const models = caps?.models ?? [];
  const catModels = models.filter((m) => m.categories.includes(category));
  const currentModel = models.find((m) => m.id === model);
  const enh = caps?.enhancements ?? {};
  const ncnnOk = caps?.backends.find((b) => b.id === "ncnn")?.available;

  // Keep the model coherent with the category.
  useEffect(() => {
    if (!caps) return;
    if (currentModel && currentModel.categories.includes(category)) return;
    const first = catModels.find((m) => m.usable) ?? catModels[0];
    if (first) setModel(first.id);
  }, [category, caps]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---------------------------------------------------------------- import
  const addPaths = useCallback(
    async (paths: string[]) => {
      if (!paths.length) return;
      const r = await api.post<{ files: LocalFile[] }>("/api/upscale/probe", { paths });
      const ok = r.files.filter((f) => f.ok);
      const bad = r.files.filter((f) => !f.ok);
      if (bad.length) toast({ kind: "warning", title: t("upscale.skipped", { n: bad.length }), body: bad.map((b) => `${b.name}: ${b.error?.message}`).join("\n") });
      setFiles((prev) => {
        const known = new Set(prev.map((p) => p.path));
        const next = [...prev, ...ok.filter((f) => !known.has(f.path))];
        if (!prev.length && next.length) setActive(0);
        return next;
      });
    },
    [toast, t],
  );

  useEffect(() => {
    if (!pendingFiles) return;
    const p = consumeFiles("upscale");
    if (p) addPaths(p);
  }, [pendingFiles]); // eslint-disable-line react-hooks/exhaustive-deps

  const pickFiles = async () => {
    if (!shell) {
      fileInput.current?.click();
      return;
    }
    try {
      const r = await api.post<{ paths: string[] }>("/api/system/pick-files", { kind: "media" });
      addPaths(r.paths);
    } catch (e: any) {
      toast({ kind: "error", title: t("files.pickFailed"), body: e?.message });
    }
  };
  const uploadFiles = async (list: FileList | null) => {
    if (!list?.length) return;
    const form = new FormData();
    Array.from(list).forEach((f) => form.append("files", f));
    const r = await api.upload<{ paths: string[] }>("/api/upload", form);
    addPaths(r.paths);
  };

  // Paste file paths (e.g. Explorer's "Copy as path") to import them.
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const target = e.target as HTMLElement;
      if (target?.tagName === "INPUT" || target?.tagName === "TEXTAREA") return;
      const text = e.clipboardData?.getData("text") ?? "";
      const paths = Array.from(text.matchAll(/"?([A-Za-z]:\\[^"\r\n<>|*?]+\.\w{2,5})"?/g), (m) => m[1].trim());
      if (paths.length) {
        e.preventDefault();
        addPaths(paths);
      }
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [addPaths]);

  useKey("ctrl+o", (e) => {
    e.preventDefault();
    pickFiles();
  });

  useEffect(() => {
    setPreview(null);
    setPreviewError(null);
    setRegion(null);
    setZone(null);
    setAt(null);
  }, [active, files.length]);

  // ---------------------------------------------------------------- options
  const isVideo = file?.kind === "video";
  const upscaling = tool === "upscale" || alsoUpscale;
  const [outW, outH] = file?.width && file?.height ? (upscaling ? targetSize(file.width, file.height, scaleMode, cw, ch, keep) : [file.width, file.height]) : [0, 0];
  useEffect(() => {
    if (tool === "interpolate" && !isVideo) setTool("upscale");
    if (tool === "face") setCategory((c) => (c === "anime" ? "face" : c));
  }, [tool, isVideo]);

  const options = useMemo(
    () => ({
      model,
      ...(!upscaling ? { scale: 1 } : scaleMode === "custom" ? { width: cw, height: ch, keep_aspect: keep } : { scale: Number(scaleMode) }),
      denoise,
      sharpen,
      detail,
      color,
      face: tool === "face" || (tool === "upscale" && face),
      face_model: faceModel,
      face_strength: faceStrength / 100,
      face_fidelity: faceFidelity / 100,
      remove_bg: tool === "background",
      bg_mode: bgMode,
      bg_color: bgColor,
      bg_feather: bgFeather,
      colorize: tool === "colorize",
      colorize_saturation: colSat / 100,
      colorize_size: Number(colSize),
      interpolate: tool === "interpolate" ? interp : undefined,
      ...(TOOL_SUFFIX[tool] ? { suffix: TOOL_SUFFIX[tool] } : {}),
      temporal,
      hdr: "tonemap",
      image_format: imageFormat,
      container,
      encoder,
      quality: vq,
      ...(outputDir ? { output_dir: outputDir } : {}),
    }),
    [model, scaleMode, cw, ch, keep, denoise, sharpen, detail, color, face, temporal, imageFormat, container, encoder, vq, outputDir, tool, upscaling, faceModel, faceStrength, faceFidelity, bgMode, bgColor, bgFeather, colSat, colSize, interp],
  );

  // Pictures up to 1 MP are compared whole (same rule as the backend); bigger ones start on a ~1 MP area
  // that the mouse wheel resizes, up to the whole picture.
  const zoneSide = file?.width && file?.height ? Math.min(zone ?? (file.width * file.height <= WHOLE_PX ? Infinity : 1024), Math.max(file.width, file.height)) : 0;
  const regionBox = useMemo(() => {
    if (!file?.width || !file?.height) return null;
    const rw = Math.min(1, zoneSide / file.width);
    const rh = Math.min(1, zoneSide / file.height);
    const cx = region?.x ?? 0.5;
    const cy = region?.y ?? 0.5;
    return { x: Math.max(0, Math.min(1 - rw, cx - rw / 2)), y: Math.max(0, Math.min(1 - rh, cy - rh / 2)), w: rw, h: rh };
  }, [file, region, zoneSide]);
  const partial = !!regionBox && (regionBox.w < 1 || regionBox.h < 1);

  useEffect(() => {
    const el = stageRef.current;
    const fw = file?.width;
    const fh = file?.height;
    if (!el || !fw || !fh) return;
    const max = Math.max(fw, fh);
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      setZone((z) => {
        const cur = Math.min(z ?? (fw * fh <= WHOLE_PX ? max : 1024), max);
        return Math.round(Math.max(128, Math.min(max, cur * (e.deltaY > 0 ? 1.15 : 1 / 1.15))));
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [file, preview]);

  const runPreview = async () => {
    if (!file) return;
    setPreviewing(true);
    setPreviewError(null);
    try {
      const r = await api.post<PreviewResult>("/api/upscale/preview", {
        path: file.path,
        options,
        region: regionBox, // always explicit: {0,0,1,1} = the whole picture
        at,
      });
      setPreview(r);
    } catch (e) {
      setPreviewError(e);
    } finally {
      setPreviewing(false);
    }
  };
  useKey("ctrl+p", (e) => {
    e.preventDefault();
    runPreview();
  });

  const start = async (all: boolean) => {
    const paths = all ? files.map((f) => f.path) : file ? [file.path] : [];
    if (!paths.length) return;
    setStarting(true);
    try {
      const r = await api.post<{ jobs: Job[] }>("/api/upscale/jobs", { paths, options });
      toast({ kind: "success", title: t("upscale.queued", { n: r.jobs.length }), action: { label: t("download.viewQueue"), run: () => setPage("queue") } });
    } catch (e: any) {
      toast({ kind: "error", title: e.message, body: e.hint });
    } finally {
      setStarting(false);
    }
  };

  const pickOutput = async () => {
    const r = await api.post<{ path: string | null }>("/api/system/pick-folder", { initial: outputDir || settings?.ai.output_folder || "" });
    if (!r.path) return;
    setOutputDir(r.path);
    useApp.getState().saveSettings({ ai: { output_folder: r.path } }).catch(() => undefined); // remembered
  };

  const gpu = hw?.gpus?.[0];
  const encoders = caps?.video.encoders ?? [];

  // ---------------------------------------------------------------- render
  // Region picking on the stage: map clicks to image coordinates (image is "contained").
  const imgRect = (W: number, H: number) => {
    if (!file?.width || !file?.height || !W || !H) return { x: 0, y: 0, w: W, h: H };
    const ia = file.width / file.height;
    const ca = W / H;
    if (ia > ca) {
      const h = W / ia;
      return { x: 0, y: (H - h) / 2, w: W, h };
    }
    const w = H * ia;
    return { x: (W - w) / 2, y: 0, w, h: H };
  };
  const ir = imgRect(stageSize.w, stageSize.h);
  const onStageClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!file) return pickFiles();
    const r = e.currentTarget.getBoundingClientRect();
    const px = (e.clientX - r.left - ir.x) / ir.w;
    const py = (e.clientY - r.top - ir.y) / ir.h;
    if (px >= 0 && px <= 1 && py >= 0 && py <= 1) setRegion({ x: px, y: py });
  };
  const factorLabel = !upscaling ? t(`tool.${tool}`) : scaleMode === "custom" ? `${outW}×${outH}` : `${scaleMode}×`;
  const toolReady: Record<Tool, boolean> = {
    upscale: true,
    face: !!enh.face?.available,
    background: !!enh.background?.available,
    colorize: !!enh.colorize?.available,
    interpolate: !!enh.interpolate?.available,
  };

  const toolOptions = () => (
    <div className="col" style={{ gap: 12 }}>
      {tool === "face" && (
        <>
          <Field label={t("tool.face.model")} hint={faceModel === "codeformer" ? t("tool.face.codeformerHint") : t("tool.face.gfpganHint")}>
            <Segmented value={faceModel} onChange={setFaceModel} full options={[{ value: "gfpgan", label: "GFPGAN" }, { value: "codeformer", label: "CodeFormer", disabled: enh.face?.models?.codeformer === false }]} />
          </Field>
          <Field label={`${t("tool.face.strength")} · ${faceStrength}%`} hint={t("tool.face.strengthHint")}>
            <input type="range" min={20} max={100} value={faceStrength} onChange={(e) => setFaceStrength(Number(e.target.value))} />
          </Field>
          {faceModel === "codeformer" && (
            <Field label={`${t("tool.face.fidelity")} · ${faceFidelity}%`} hint={t("tool.face.fidelityHint")}>
              <input type="range" min={0} max={100} value={faceFidelity} onChange={(e) => setFaceFidelity(Number(e.target.value))} />
            </Field>
          )}
        </>
      )}
      {tool === "background" && (
        <>
          <Field label={t("tool.bg.mode")}>
            <Segmented value={bgMode} onChange={setBgMode} full size="sm" options={["transparent", "color", "blur", "mask"].map((m) => ({ value: m, label: t(`tool.bg.${m}`) }))} />
          </Field>
          {bgMode === "color" && (
            <div className="row" style={{ gap: 8 }}>
              <input type="color" className="color-input" value={bgColor} onChange={(e) => setBgColor(e.target.value)} />
              {["#ffffff", "#000000", "#F22B2B", "#00b140", "#1f6feb"].map((c) => (
                <button key={c} className={`swatch ${bgColor.toLowerCase() === c.toLowerCase() ? "on" : ""}`} style={{ background: c }} onClick={() => setBgColor(c)} />
              ))}
            </div>
          )}
          <Field label={`${t("tool.bg.feather")} · ${bgFeather}px`}>
            <input type="range" min={0} max={8} value={bgFeather} onChange={(e) => setBgFeather(Number(e.target.value))} />
          </Field>
        </>
      )}
      {tool === "colorize" && (
        <>
          <Field label={t("tool.col.style")} hint={t("tool.col.styleHint")}>
            <Segmented value={colSize} onChange={setColSize} full options={[{ value: "256", label: t("tool.col.vivid") }, { value: "384", label: t("tool.col.balanced") }, { value: "512", label: t("tool.col.subtle") }]} />
          </Field>
          <Field label={`${t("tool.col.saturation")} · ${colSat}%`}>
            <input type="range" min={40} max={180} value={colSat} onChange={(e) => setColSat(Number(e.target.value))} />
          </Field>
        </>
      )}
      {tool === "interpolate" && (
        <Field label={t("tool.int.target")} hint={file?.fps ? t("tool.int.hint", { fps: Math.round(file.fps) }) : undefined}>
          <Segmented value={interp} onChange={setInterp} full options={[{ value: "2", label: "×2" }, { value: "3", label: "×3" }, { value: "4", label: "×4" }, { value: "60", label: "60 fps" }, { value: "120", label: "120 fps" }]} />
        </Field>
      )}
    </div>
  );

  return (
    <div className="page-inner wide">
      <PageHead
        eyebrow={t("upscale.eyebrow")}
        title={t(`tool.${tool}.title`)}
        right={
          <Segmented<Tool>
            value={tool}
            onChange={setTool}
            options={(["upscale", "face", "background", "colorize", "interpolate"] as Tool[]).map((x) => ({
              value: x,
              label: t(`tool.${x}`),
              icon: TOOL_ICON[x],
              disabled: x === "interpolate" && !!file && !isVideo,
              tip: t(`tool.${x}.tip`),
            }))}
          />
        }
      />
      {caps && !toolReady[tool] && (
        <div style={{ marginBottom: 16 }}>
          <Notice kind="warning" title={t("tool.missingTitle")}>
            <div className="row" style={{ marginTop: 6, flexWrap: "wrap" }}>
              <span>{t("tool.missingBody")}</span>
              <Button size="sm" variant="primary" onClick={() => goSettings("components")}>{t("components.install")}</Button>
            </div>
          </Notice>
        </div>
      )}

      {caps && !ncnnOk && tool === "upscale" && (
        <div style={{ marginBottom: 16 }}>
          <Notice kind="warning" title={t("upscale.noAiTitle")}>
            <div className="row" style={{ marginTop: 6 }}>
              <span>{caps.backends.find((b) => b.id === "ncnn")?.info}</span>
              <Button size="sm" variant="primary" onClick={() => goSettings("components")}>{t("components.install")}</Button>
            </div>
          </Notice>
        </div>
      )}

      <div className="upscale-layout">
        {/* ---------------------------------------------- stage */}
        <div className="upscale-main rise" style={{ animationDelay: "60ms" }}>
          <div
            onDragOver={(e) => {
              e.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={() => setOver(false)}
            style={{ position: "relative" }}
          >
            {preview ? (
              <CompareSlider
                original={mediaUrl(preview.original)}
                result={mediaUrl(preview.result)}
                width={preview.width}
                height={preview.height}
                afterLabel={`${t("upscale.upscaleTag")} ${factorLabel}`}
                busy={previewing ? <PreviewBusy t={t} /> : undefined}
              />
            ) : (
              <div className="compare" ref={stageRef} style={{ cursor: file ? "crosshair" : "pointer" }} onClick={onStageClick}>
                {file ? (
                  <>
                    <PixelImage
                      key={file.path}
                      src={file.kind === "image" ? localMediaUrl(file.path) : thumbUrl(file.thumbnail)}
                      fit="contain"
                      block={1}
                      animateFrom={28}
                      duration={1300}
                      style={{ position: "absolute", inset: 0, aspectRatio: "auto" }}
                    />
                    {regionBox && partial && (
                      <div
                        className="region-box"
                        style={{ left: ir.x + regionBox.x * ir.w, top: ir.y + regionBox.y * ir.h, width: regionBox.w * ir.w, height: regionBox.h * ir.h }}
                      />
                    )}
                    <span className="compare-label left">{t("compare.original")}</span>
                    <span className="label" style={{ position: "absolute", left: 16, bottom: 14, color: "var(--fog-2)" }}>
                      {t(partial ? "upscale.regionHint" : "upscale.wholeHint")}
                    </span>
                    <span className="label" style={{ position: "absolute", right: 16, bottom: 14, color: "var(--fog-1)" }}>
                      {file.width}×{file.height}
                    </span>
                    {previewing && (
                      <div className="compare-busy">
                        <PreviewBusy t={t} />
                      </div>
                    )}
                  </>
                ) : (
                  <div className="compare-empty">
                    <div>
                      <div className="empty-icon" style={{ borderColor: "var(--accent)", color: "var(--accent)" }}>
                        <ImageUp size={24} />
                      </div>
                      <div className="empty-title">{t("upscale.emptyTitle")}</div>
                      <div className="faint" style={{ maxWidth: 420 }}>{t("upscale.emptyBody")}</div>
                      <Button
                        variant="primary"
                        style={{ marginTop: 18 }}
                        icon={<Plus size={16} />}
                        onClick={(e) => {
                          e.stopPropagation();
                          pickFiles();
                        }}
                      >
                        {t("upscale.addFiles")}
                      </Button>
                      <div className="label" style={{ marginTop: 14 }}>{t("upscale.dropHint")}</div>
                    </div>
                  </div>
                )}
                <Ticks inset={14} />
              </div>
            )}
            {over && (
              <div
                style={{
                  position: "absolute",
                  inset: 0,
                  borderRadius: "var(--radius-lg)",
                  border: "1px dashed var(--accent)",
                  background: "rgba(242,43,43,.12)",
                  display: "grid",
                  placeItems: "center",
                  pointerEvents: "none",
                }}
              >
                <span className="label" style={{ color: "var(--fog-1)" }}>{t("upscale.dropHere")}</span>
              </div>
            )}
            <input ref={fileInput} type="file" multiple accept="image/*,video/*" hidden onChange={(e) => uploadFiles(e.target.files)} />
          </div>

          {isVideo && file?.duration && (
            <div className="card row" style={{ gap: 14, padding: "12px 16px" }}>
              <Film size={15} className="faint" />
              <span className="label" style={{ whiteSpace: "nowrap" }}>{t("upscale.previewFrame")}</span>
              <input type="range" min={0} max={file.duration} step={0.1} value={at ?? file.duration * 0.1} onChange={(e) => setAt(Number(e.target.value))} />
              <span className="mono" style={{ minWidth: 56, textAlign: "right" }}>{duration(at ?? file.duration * 0.1)}</span>
            </div>
          )}

          {previewError && (
            <ErrorAlert error={previewError} actions={previewError.extra?.component ? <Button size="sm" onClick={() => goSettings("components")}>{t("components.install")}</Button> : undefined} />
          )}

          {preview && (
            <div className="card row" style={{ flexWrap: "wrap", gap: 18, padding: "12px 16px" }}>
              <span className="label">{t("upscale.plan")}</span>
              <span className="mono" style={{ color: "var(--text-primary)" }}>{preview.plan.replace(/(face|colorize|background|rife)/g, (m) => t(`tool.${m === "rife" ? "interpolate" : m}`))}</span>
              <span className="mono faint">{t("upscale.previewTime", { s: preview.elapsed.toFixed(1) })}</span>
              <span className="mono faint">
                {t("upscale.estimate")} <span style={{ color: "var(--accent-text)" }}>{eta(preview.estimate_seconds)}</span>
              </span>
              <span className="spacer" />
              <Button size="sm" variant="ghost" icon={<X size={13} />} onClick={() => setPreview(null)}>{t("upscale.changeRegion")}</Button>
            </div>
          )}

          {files.length > 0 && (
            <div>
              <div className="row" style={{ marginBottom: 10 }}>
                <span className="label">{t("upscale.files", { n: files.length })}</span>
                <span className="spacer" />
                <Button size="sm" variant="ghost" icon={<Plus size={13} />} onClick={pickFiles}>{t("upscale.addFiles")}</Button>
                <Button
                  size="sm"
                  variant="ghost"
                  iconOnly
                  icon={<Trash2 size={13} />}
                  tip={t("upscale.clearFiles")}
                  onClick={() => {
                    setFiles([]);
                    setPreview(null);
                  }}
                />
              </div>
              <div className="file-strip">
                <AnimatePresence initial={false}>
                  {files.map((f, i) => (
                    <motion.div key={f.path} layout initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className={`file-item ${i === active ? "active" : ""}`} onClick={() => setActive(i)}>
                      <div className="file-thumb">
                        {f.thumbnail && <img src={thumbUrl(f.thumbnail)} alt="" />}
                        <span className="file-kind">{f.kind === "video" ? "VID" : "IMG"}</span>
                      </div>
                      <div style={{ minWidth: 0, flex: 1 }}>
                        <div className="ellipsis" style={{ fontWeight: 600, fontSize: 13 }}>{f.name}</div>
                        <div className="mono faint" style={{ fontSize: 10.5 }}>
                          {f.width}×{f.height}
                          {f.duration ? ` · ${duration(f.duration)}` : ""} · {bytes(f.size)}
                        </div>
                      </div>
                      <div onClick={(e) => { e.stopPropagation(); openInPlayer([f.path]); }} className="pbtn" data-tip={t("player.play")}>
                        <Eye size={13} />
                      </div>
                      <Button
                        size="sm"
                        variant="ghost"
                        iconOnly
                        className="remove"
                        icon={<X size={13} />}
                        onClick={(e) => {
                          e.stopPropagation();
                          setFiles((xs) => xs.filter((x) => x.path !== f.path));
                          setActive((a) => Math.max(0, Math.min(a, files.length - 2)));
                        }}
                      />
                    </motion.div>
                  ))}
                </AnimatePresence>
              </div>
            </div>
          )}
        </div>

        {/* ---------------------------------------------- settings */}
        <div className="side-panel rise" style={{ animationDelay: "120ms" }}>
          <div className="card card-pad col" style={{ gap: 16 }}>
            <div className="col" style={{ gap: 6 }}>
              <span className="label">{t("upscale.file")}</span>
              <span style={{ font: "700 17px/1.2 var(--font-display)", wordBreak: "break-all" }}>{file?.name ?? "—"}</span>
            </div>
            <div className="spec-grid">
              <span>{t("upscale.input")}</span>
              <span>{file ? `${file.width}×${file.height}` : "—"}</span>
              <span>{t("upscale.outputRes")}</span>
              <span style={{ color: "var(--accent-text)" }}>{file ? `${outW}×${outH}` : "—"}</span>
              {isVideo && (
                <>
                  <span>{t("fact.duration")}</span>
                  <span>
                    {duration(file?.duration)} · {file?.fps ? `${Math.round(file.fps)} fps` : "—"}
                  </span>
                  <span>{t("upscale.frames")}</span>
                  <span>{file?.frames ?? "—"}</span>
                  <span>HDR</span>
                  <span>{file?.is_hdr ? t("upscale.hdrTonemap") : "SDR"}</span>
                </>
              )}
              <span>GPU</span>
              <span className="ellipsis">
                {gpu ? `${gpu.name?.replace("NVIDIA GeForce ", "")} · ${((gpu.vram_used_mb ?? 0) / 1024).toFixed(1)}/${((gpu.vram_total_mb ?? 0) / 1024).toFixed(0)} GB · ${gpu.temperature ?? "—"}°C` : "CPU"}
              </span>
            </div>
            {tool !== "upscale" && (
              <ToggleRow title={t("tool.alsoUpscale")} desc={t("tool.alsoUpscaleDesc")} checked={alsoUpscale} onChange={setAlsoUpscale} />
            )}
            {upscaling && (
              <Segmented<ScaleMode>
                value={scaleMode}
                onChange={setScaleMode}
                full
                options={[
                  { value: "2", label: "2×" },
                  { value: "4", label: "4×" },
                  { value: "8", label: "8×" },
                  { value: "custom", label: t("upscale.custom") },
                ]}
              />
            )}
            {upscaling && scaleMode === "custom" && (
              <div className="col" style={{ gap: 8 }}>
                <div className="row">
                  <input className="input input-sm" type="number" value={cw} onChange={(e) => setCw(Number(e.target.value))} />
                  <span className="faint">×</span>
                  <input className="input input-sm" type="number" value={ch} onChange={(e) => setCh(Number(e.target.value))} />
                </div>
                <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
                  {[
                    ["1440p", 2560, 1440],
                    ["4K", 3840, 2160],
                    ["8K", 7680, 4320],
                  ].map(([l, w, h]) => (
                    <button
                      key={l as string}
                      className={`chip ${cw === w && ch === h ? "on" : ""}`}
                      onClick={() => {
                        setCw(w as number);
                        setCh(h as number);
                      }}
                    >
                      {l}
                    </button>
                  ))}
                  <span className="spacer" />
                  <label className="check-row" style={{ fontSize: 12.5 }} onClick={() => setKeep(!keep)}>
                    <Check on={keep} /> {t("upscale.keepAspect")}
                  </label>
                </div>
              </div>
            )}
            {tool !== "upscale" && toolOptions()}
            {upscaling && <div className="divider" style={{ margin: 0 }} />}
            {upscaling && <span className="label">{t("upscale.model")}</span>}
            {upscaling && <div className="row" style={{ flexWrap: "wrap", gap: 4 }}>
              {CATEGORIES.map((c) => {
                const info = caps?.categories.find((x) => x.id === c);
                const has = (info?.installed.length ?? 0) > 0;
                return (
                  <button
                    key={c}
                    className={`chip ${category === c ? "on" : ""}`}
                    style={{ opacity: has || category === c ? 1 : 0.5 }}
                    onClick={() => setCategory(c)}
                    data-tip={has ? undefined : t("upscale.noModelInCategory")}
                  >
                    {t(`category.${c}`)}
                  </button>
                );
              })}
            </div>}
            {upscaling && <div className="col" style={{ gap: 6 }}>
              {catModels.length === 0 && <Notice>{t(`category.${category}.empty`)}</Notice>}
              {catModels.map((m) => (
                <button key={m.id} className={`model-card ${model === m.id ? "active" : ""}`} disabled={!m.usable} onClick={() => setModel(m.id)}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="row" style={{ gap: 6 }}>
                      <span className="model-name ellipsis">{m.name}</span>
                      <span className="spacer" />
                      {m.speed && <Badge>{t(`speed.${m.speed}`)}</Badge>}
                      {m.video_suited && <Badge>{t("upscale.videoTag")}</Badge>}
                      <Badge mono>×{m.scales.join("/")}</Badge>
                    </div>
                    <div className="faint" style={{ fontSize: 12, marginTop: 5, lineHeight: 1.4 }}>{t.or(`model.${m.id}.desc`, m.description)}</div>
                    {!m.installed && (
                      <div className="label" style={{ color: "var(--warning)", marginTop: 6 }}>
                        {t("upscale.notInstalled")}
                      </div>
                    )}
                    {m.noncommercial && <div className="label" style={{ marginTop: 6 }}>{t("upscale.noncommercial", { license: m.license })}</div>}
                  </div>
                </button>
              ))}
            </div>}
            {upscaling && currentModel?.notes && <div className="field-hint">{t.or(`model.${currentModel.id}.notes`, currentModel.notes)}</div>}
          </div>

          <div className="card card-pad col" style={{ gap: 10 }}>
            <span className="label" style={{ marginBottom: 4 }}>{t("upscale.enhance")}</span>
            <Levels name={t("enh.denoise")} icon={<Droplets size={13} />} value={denoise} onChange={setDenoise} />
            <Levels name={t("enh.sharpen")} icon={<Zap size={13} />} value={sharpen} onChange={setSharpen} />
            <Levels name={t("enh.detail")} icon={<Waves size={13} />} value={detail} onChange={setDetail} tip={t.or("enh.detail.note", enh.detail?.note)} />
            <Levels name={t("enh.color")} icon={<Palette size={13} />} value={color} onChange={setColor} tip={t.or("enh.color.note", enh.color?.note)} />
            <div className="divider" style={{ margin: "4px 0" }} />
            {tool === "upscale" && (
              <ToggleRow
                title={
                  <span className="row" style={{ gap: 7 }}>
                    <ScanFace size={14} /> {t("enh.face")}
                  </span>
                }
                desc={enh.face?.available ? t("enh.faceDesc") : t.or("enh.face.missing", enh.face?.note)}
                checked={face && !!enh.face?.available}
                onChange={setFace}
                disabled={!enh.face?.available}
              />
            )}
            {isVideo && <ToggleRow title={t("enh.temporal")} desc={t.or("enh.temporal.note", enh.temporal?.note)} checked={temporal} onChange={setTemporal} disabled={!enh.temporal?.available} />}

          </div>

          <div className="card card-pad col" style={{ gap: 14 }}>
            <span className="label">{t("upscale.output")}</span>
            {!isVideo ? (
              <Segmented
                value={imageFormat}
                onChange={setImageFormat}
                full
                options={["png", "jpg", "webp"].map((f) => ({ value: f, label: f.toUpperCase(), disabled: f === "jpg" && tool === "background" && ["transparent", "mask"].includes(bgMode) }))}
              />
            ) : tool === "background" && bgMode === "transparent" ? (
              <div className="field-hint">{t("tool.bg.webmNote")}</div>
            ) : (
              <>
                <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: 10, alignItems: "end" }}>
                  <Segmented value={container} onChange={setContainer} options={[{ value: "mp4", label: "MP4" }, { value: "mkv", label: "MKV" }]} />
                  <Select
                    value={encoder}
                    onChange={setEncoder}
                    up
                    options={[
                      { value: "auto", label: t("codec.autoShort") },
                      ...encoders.map((e) => ({ value: e, label: e.replace("_nvenc", " NVENC").replace("lib", ""), hint: e.includes("nvenc") ? "GPU" : "CPU" })),
                    ]}
                  />
                </div>
                <Field label={`${t("upscale.videoQuality")} · CQ ${vq}`} hint={t("upscale.videoQualityHint")}>
                  <input type="range" min={12} max={30} value={vq} onChange={(e) => setVq(Number(e.target.value))} />
                </Field>
              </>
            )}
            <button className="folder-row" onClick={pickOutput}>
              <FolderOpen size={15} style={{ color: "var(--text-tertiary)", flexShrink: 0 }} />
              <span style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="label" style={{ fontSize: 9 }}>{t("download.saveTo")}</span>
                <span className="ellipsis mono" style={{ color: "var(--text-primary)" }}>{outputDir || settings?.ai.output_folder || t("upscale.nextToSource")}</span>
              </span>
            </button>
            <Button variant="outline" full icon={<Eye size={15} />} loading={previewing} disabled={!file} onClick={runPreview}>
              {t("upscale.preview")} <span className="kbd">Ctrl P</span>
            </Button>
            <Button variant="primary" size="xl" full icon={<Sparkles size={18} />} loading={starting} disabled={!file || !toolReady[tool]} onClick={() => start(false)}>
              {t(`tool.${tool}.start`)}
            </Button>
            {files.length > 1 && (
              <Button full icon={<Layers size={15} />} disabled={starting} onClick={() => start(true)}>
                {t("upscale.processAll", { n: files.length })}
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function PreviewBusy({ t }: { t: ReturnType<typeof useT> }) {
  return (
    <div className="col" style={{ alignItems: "center", gap: 12 }}>
      <span className="cursor-block" style={{ width: 14, height: 14 }} />
      <span className="label" style={{ color: "var(--fog-1)" }}>{t("upscale.previewing")}</span>
    </div>
  );
}
