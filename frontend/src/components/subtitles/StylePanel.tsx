import clsx from "clsx";
import { AlignCenter, AlignLeft, AlignRight } from "lucide-react";
import { ReactNode } from "react";
import type { SubAnimation, SubCatalog, SubStyle } from "../../api/types";
import { useT } from "../../i18n";
import { hexToRgba } from "../../lib/subtitles";
import { Field, Segmented, Select, Switch } from "../ui";

const PRESET_ORDER = ["netflix", "youtube", "cinematic", "tiktok", "anime", "documentary", "minimal", "clean", "bold"];

export function StylePanel({ style, catalog, onChange }: { style: SubStyle; catalog: SubCatalog; onChange: (s: SubStyle) => void }) {
  const t = useT();
  const set = (patch: Partial<SubStyle>) => onChange({ ...style, ...patch });
  const fonts = catalog.fonts;
  const weights = fonts.find((f) => f.family === style.font)?.weights ?? [400, 700];
  const custom = JSON.stringify({ ...catalog.presets[style.preset], preset: "" }) !== JSON.stringify({ ...style, preset: "" });

  return (
    <div className="style-panel">
      <div className="label" style={{ marginBottom: 8 }}>
        {t("subs.presets")} {custom && <span className="faint" style={{ textTransform: "none", letterSpacing: 0 }}>· {t("subs.customized")}</span>}
      </div>
      <div className="preset-grid">
        {PRESET_ORDER.filter((p) => catalog.presets[p]).map((p) => {
          const ps = catalog.presets[p];
          return (
            <button key={p} className={clsx("preset-card", style.preset === p && "on")} onClick={() => onChange({ ...catalog.presets[p] })}>
              <span className="preset-demo">
                <span
                  style={{
                    fontFamily: `"${ps.font}", sans-serif`,
                    fontWeight: ps.weight,
                    fontStyle: ps.italic ? "italic" : "normal",
                    textTransform: ps.uppercase ? "uppercase" : "none",
                    color: ps.color,
                    fontSize: 13,
                    letterSpacing: ps.letter_spacing * 13,
                    WebkitTextStroke: ps.background === "box" || !ps.outline ? undefined : `${Math.max(1, ps.outline * 26)}px ${ps.outline_color}`,
                    paintOrder: "stroke fill",
                    background: ps.background === "box" ? hexToRgba(ps.bg_color, ps.bg_opacity) : undefined,
                    padding: ps.background === "box" ? "0 4px" : undefined,
                  }}
                >
                  {t("subs.demo")}
                </span>
              </span>
              <span className="preset-name">{t(`subs.preset.${p}`)}</span>
            </button>
          );
        })}
      </div>

      <Group title={t("subs.text")}>
        <div className="options-grid">
          <Field label={t("subs.font")}>
            <Select value={style.font} onChange={(v) => set({ font: v, weight: nearest(fonts.find((f) => f.family === v)?.weights ?? [400], style.weight) })} options={fonts.map((f) => ({ value: f.family, label: f.family }))} />
          </Field>
          <Field label={t("subs.weight")}>
            <Select value={String(style.weight)} onChange={(v) => set({ weight: Number(v) })} options={weights.map((w) => ({ value: String(w), label: t(`subs.w${w}`) }))} />
          </Field>
        </div>
        <Slider label={t("subs.size")} value={style.size} min={2.5} max={10} step={0.1} fmt={(v) => `${v.toFixed(1)} %`} onChange={(v) => set({ size: v })} />
        <div className="row" style={{ gap: 18 }}>
          <label className="row" style={{ gap: 8 }}><Switch checked={style.italic} onChange={(v) => set({ italic: v })} /> {t("subs.italic")}</label>
          <label className="row" style={{ gap: 8 }}><Switch checked={style.uppercase} onChange={(v) => set({ uppercase: v })} /> {t("subs.uppercase")}</label>
        </div>
        <ColorRow label={t("subs.color")} color={style.color} onColor={(c) => set({ color: c })} value={style.opacity} onValue={(v) => set({ opacity: v })} valueLabel={t("subs.opacity")} />
      </Group>

      <Group title={t("subs.outlineShadow")}>
        <ColorRow label={t("subs.outline")} color={style.outline_color} onColor={(c) => set({ outline_color: c })} value={style.outline} max={0.25} onValue={(v) => set({ outline: v })} valueLabel={t("subs.thickness")} fmt={(v) => (v * 100).toFixed(0)} disabled={style.background === "box"} />
        <ColorRow label={t("subs.shadow")} color={style.shadow_color} onColor={(c) => set({ shadow_color: c })} value={style.shadow} max={0.2} onValue={(v) => set({ shadow: v })} valueLabel={t("subs.distance")} fmt={(v) => (v * 100).toFixed(0)} />
        <Slider label={t("subs.shadowOpacity")} value={style.shadow_opacity} min={0} max={1} step={0.05} fmt={(v) => `${Math.round(v * 100)} %`} onChange={(v) => set({ shadow_opacity: v })} />
      </Group>

      <Group title={t("subs.background")}>
        <Segmented value={style.background} onChange={(v) => set({ background: v })} full size="sm" options={[{ value: "none", label: t("subs.bgNone") }, { value: "box", label: t("subs.bgBox") }]} />
        {style.background === "box" && (
          <>
            <ColorRow label={t("subs.bgColor")} color={style.bg_color} onColor={(c) => set({ bg_color: c })} value={style.bg_opacity} onValue={(v) => set({ bg_opacity: v })} valueLabel={t("subs.opacity")} />
            <Slider label={t("subs.padding")} value={style.bg_padding} min={0.05} max={0.6} step={0.01} fmt={(v) => (v * 100).toFixed(0)} onChange={(v) => set({ bg_padding: v })} />
          </>
        )}
      </Group>

      <Group title={t("subs.layout")}>
        <div className="options-grid">
          <Field label={t("subs.vertical")}>
            <Segmented value={style.vertical} onChange={(v) => set({ vertical: v })} full size="sm" options={[{ value: "bottom", label: t("subs.bottom") }, { value: "top", label: t("subs.top") }]} />
          </Field>
          <Field label={t("subs.align")}>
            <Segmented value={style.align} onChange={(v) => set({ align: v })} full size="sm" options={[
              { value: "left", label: <AlignLeft size={13} />, tip: t("subs.left") },
              { value: "center", label: <AlignCenter size={13} />, tip: t("subs.center") },
              { value: "right", label: <AlignRight size={13} />, tip: t("subs.right") },
            ]} />
          </Field>
        </div>
        <Slider label={t("subs.position")} value={style.position} min={0} max={45} step={0.5} fmt={(v) => `${v.toFixed(1)} %`} onChange={(v) => set({ position: v })} />
        <Slider label={t("subs.maxWidth")} value={style.max_width} min={40} max={100} step={1} fmt={(v) => `${v} %`} onChange={(v) => set({ max_width: v })} />
        <Slider label={t("subs.letterSpacing")} value={style.letter_spacing} min={-0.05} max={0.25} step={0.005} fmt={(v) => (v * 100).toFixed(1)} onChange={(v) => set({ letter_spacing: v })} />
        <Slider label={t("subs.lineHeight")} value={style.line_height} min={0.9} max={1.7} step={0.02} fmt={(v) => v.toFixed(2)} onChange={(v) => set({ line_height: v })} />
      </Group>

      <Group title={t("subs.animation")}>
        <Select<SubAnimation> value={style.animation} onChange={(v) => set({ animation: v })} options={catalog.animations.map((a) => ({ value: a, label: t(`subs.anim.${a}`) }))} />
        <div className="field-hint">{t(`subs.anim.${style.animation}.hint`)}</div>
        {(style.animation === "highlight" || style.animation === "karaoke") && (
          <div className="row" style={{ gap: 10 }}>
            <span className="label" style={{ fontSize: 9 }}>{t("subs.highlight")}</span>
            <input type="color" className="color-input" value={style.highlight_color} onChange={(e) => set({ highlight_color: e.target.value.toUpperCase() })} />
          </div>
        )}
        {style.animation !== "none" && (
          <Slider label={t("subs.animSpeed")} value={style.anim_speed} min={0.5} max={2} step={0.1} fmt={(v) => `×${v.toFixed(1)}`} onChange={(v) => set({ anim_speed: v })} />
        )}
      </Group>
    </div>
  );
}

function nearest(ws: number[], w: number): number {
  return ws.reduce((a, b) => (Math.abs(b - w) < Math.abs(a - w) ? b : a), ws[0] ?? 400);
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="style-group">
      <div className="label">{title}</div>
      {children}
    </div>
  );
}

function Slider({ label, value, min, max, step, fmt, onChange }: { label: string; value: number; min: number; max: number; step: number; fmt: (v: number) => string; onChange: (v: number) => void }) {
  return (
    <label className="style-slider">
      <span>{label}</span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
      <span className="mono tabular">{fmt(value)}</span>
    </label>
  );
}

function ColorRow({ label, color, onColor, value, onValue, valueLabel, max = 1, fmt, disabled }: { label: string; color: string; onColor: (c: string) => void; value: number; onValue: (v: number) => void; valueLabel: string; max?: number; fmt?: (v: number) => string; disabled?: boolean }) {
  return (
    <div className={clsx("style-color", disabled && "disabled")}>
      <span>{label}</span>
      <input type="color" className="color-input" value={color} disabled={disabled} onChange={(e) => onColor(e.target.value.toUpperCase())} />
      <input type="range" min={0} max={max} step={max / 100} value={value} disabled={disabled} onChange={(e) => onValue(Number(e.target.value))} title={valueLabel} />
      <span className="mono tabular">{fmt ? fmt(value) : `${Math.round(value * 100)} %`}</span>
    </div>
  );
}
