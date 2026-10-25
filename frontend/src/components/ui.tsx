import clsx from "clsx";
import { AnimatePresence, motion } from "motion/react";
import { AlertTriangle, Check as CheckIcon, ChevronDown, Globe, ImageOff, Info, Loader2, OctagonAlert, X } from "lucide-react";
import { CSSProperties, ReactNode, useEffect, useRef, useState } from "react";
import type { ApiErrorBody, JobStatus } from "../api/types";
import { useT } from "../i18n";

// ------------------------------------------------------------------ Button
type BtnProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "default" | "primary" | "ghost" | "danger" | "outline" | "light";
  size?: "sm" | "md" | "lg" | "xl";
  icon?: ReactNode;
  loading?: boolean;
  iconOnly?: boolean;
  tip?: string;
  full?: boolean;
  active?: boolean;
};

export function Button({ variant = "default", size = "md", icon, loading, iconOnly, tip, full, active, className, children, disabled, ...rest }: BtnProps) {
  return (
    <button
      className={clsx(
        "btn",
        `btn-${variant}`,
        size !== "md" && `btn-${size}`,
        iconOnly && "btn-icon",
        full && "btn-full",
        active && "active-inv",
        className,
      )}
      disabled={disabled || loading}
      data-tip={tip}
      aria-label={iconOnly ? tip : undefined}
      {...rest}
    >
      {loading ? <Loader2 size={size === "sm" ? 14 : 16} className="spin" /> : icon}
      {children}
    </button>
  );
}

// ------------------------------------------------------------------ Page head (eyebrow + Syne title with grey echo)
export function PageHead({ eyebrow, title, echo, right, sub }: { eyebrow?: ReactNode; title: ReactNode; echo?: ReactNode; right?: ReactNode; sub?: ReactNode }) {
  return (
    <div className="page-head rise">
      <div>
        {eyebrow && <span className="eyebrow">{eyebrow}</span>}
        <h1 className="page-title">
          {title}
          {echo && (
            <>
              {" "}
              <span className="echo">{echo}</span>
            </>
          )}
        </h1>
        {sub && <p className="page-subtitle">{sub}</p>}
      </div>
      {right && <div className="row" style={{ gap: 10, flexWrap: "wrap", justifyContent: "flex-end" }}>{right}</div>}
    </div>
  );
}

/** HUD corner ticks (the se7en "frame" language). Parent must be position:relative. */
export function Ticks({ inset = 0, color }: { inset?: number; color?: string }) {
  const style = { ["--ti" as any]: `${inset}px`, ...(color ? { borderColor: color } : {}) } as CSSProperties;
  return (
    <span className="ticks" aria-hidden>
      <span style={style} />
      <span style={style} />
      <span style={style} />
      <span style={style} />
    </span>
  );
}

export function Badge({ tone, mono, children, icon, style }: { tone?: "accent" | "solid" | "inverse" | "success" | "warning" | "info"; mono?: boolean; children: ReactNode; icon?: ReactNode; style?: CSSProperties }) {
  return (
    <span className={clsx("chip", tone, mono && "mono-b")} style={style}>
      {icon}
      {children}
    </span>
  );
}

// ------------------------------------------------------------------ Select
export interface Option<T extends string | number = string> {
  value: T;
  label: ReactNode;
  hint?: ReactNode;
  disabled?: boolean;
  icon?: ReactNode;
}

export function Select<T extends string | number>({
  value,
  options,
  onChange,
  placeholder,
  className,
  up,
  disabled,
}: {
  value: T | undefined;
  options: Option<T>[];
  onChange: (v: T) => void;
  placeholder?: string;
  className?: string;
  up?: boolean;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [hl, setHl] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const current = options.find((o) => o.value === value);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);

  useEffect(() => {
    if (open) setHl(Math.max(0, options.findIndex((o) => o.value === value)));
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const onKey = (e: React.KeyboardEvent) => {
    if (!open && (e.key === "Enter" || e.key === " " || e.key === "ArrowDown")) {
      e.preventDefault();
      setOpen(true);
      return;
    }
    if (!open) return;
    if (e.key === "Escape") setOpen(false);
    else if (e.key === "ArrowDown") {
      e.preventDefault();
      setHl((h) => Math.min(options.length - 1, h + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHl((h) => Math.max(0, h - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const o = options[hl];
      if (o && !o.disabled) {
        onChange(o.value);
        setOpen(false);
      }
    }
  };

  return (
    <div className={clsx("select", open && "open", className)} ref={ref} onKeyDown={onKey}>
      <button type="button" className="select-btn" onClick={() => !disabled && setOpen(!open)} disabled={disabled}>
        {current?.icon}
        <span className="ellipsis">{current ? current.label : <span className="faint">{placeholder}</span>}</span>
        {current?.hint && <span className="faint mono" style={{ fontSize: 11 }}>{current.hint}</span>}
        <ChevronDown size={16} className="chev" />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            className={clsx("select-menu", up && "up")}
            initial={{ opacity: 0, y: up ? 6 : -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: up ? 6 : -6 }}
            transition={{ duration: 0.16, ease: [0.16, 1, 0.3, 1] }}
          >
            {options.map((o, i) => (
              <button
                key={String(o.value)}
                type="button"
                className={clsx("select-opt", o.value === value && "selected", i === hl && "hl")}
                disabled={o.disabled}
                onMouseEnter={() => setHl(i)}
                onClick={() => {
                  onChange(o.value);
                  setOpen(false);
                }}
              >
                {o.icon}
                <span className="ellipsis">{o.label}</span>
                {o.hint && <span className="opt-hint">{o.hint}</span>}
                {o.value === value && <CheckIcon size={14} style={{ marginLeft: o.hint ? 6 : "auto", color: "var(--accent)", flexShrink: 0 }} />}
              </button>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ------------------------------------------------------------------ Segmented
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  full,
  size,
}: {
  value: T;
  options: { value: T; label: ReactNode; icon?: ReactNode; disabled?: boolean; tip?: string }[];
  onChange: (v: T) => void;
  full?: boolean;
  size?: "sm";
}) {
  return (
    <div className={clsx("seg", full && "full")} role="tablist">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="tab"
          aria-selected={o.value === value}
          className={clsx("seg-opt", o.value === value && "active")}
          style={size === "sm" ? { height: 26, fontSize: 10, padding: "0 12px" } : undefined}
          disabled={o.disabled}
          data-tip={o.tip}
          onClick={() => onChange(o.value)}
        >
          {o.icon}
          {o.label}
        </button>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------ Switch / toggles
export function Switch({ checked, onChange, disabled }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return <button type="button" role="switch" aria-checked={checked} className={clsx("switch", checked && "on")} disabled={disabled} onClick={() => onChange(!checked)} />;
}

export function ToggleRow({ title, desc, checked, onChange, disabled }: { title: ReactNode; desc?: ReactNode; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <div className="toggle-row" style={disabled ? { opacity: 0.55 } : undefined}>
      <div className="toggle-row-text">
        <div className="toggle-row-title">{title}</div>
        {desc && <div className="toggle-row-desc">{desc}</div>}
      </div>
      <Switch checked={checked} onChange={onChange} disabled={disabled} />
    </div>
  );
}

// ------------------------------------------------------------------ Levels (Off / Low / Medium / High)
export function Levels({ name, value, onChange, disabled, tip, icon }: { name: ReactNode; value: number; onChange: (v: number) => void; disabled?: boolean; tip?: string; icon?: ReactNode }) {
  const t = useT();
  const labels = [t("level.off"), t("level.low"), t("level.medium"), t("level.high")];
  return (
    <div className={clsx("levels", disabled && "disabled")} data-tip={tip}>
      <div className="levels-name">
        {icon}
        {name}
      </div>
      <div className="levels-track">
        {labels.map((l, i) => (
          <button
            key={i}
            type="button"
            className={clsx("levels-step", value > 0 && i > 0 && i < value && "on", value > 0 && i === value && "cur", value === 0 && i === 0 && "off-cur")}
            onClick={() => onChange(i)}
          >
            {l}
          </button>
        ))}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Field
export function Field({ label, hint, children }: { label?: ReactNode; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="field">
      {label && <span className="field-label">{label}</span>}
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  );
}

// ------------------------------------------------------------------ Sources & statuses
/** Sources are shown as mono domains, never as platform logos (design system rule). */
export function PlatformChip({ platform, name }: { platform?: string | null; name?: string | null }) {
  const label = (name || platform || "—").toString();
  return (
    <span className="source">
      <Globe size={12} />
      {label.toLowerCase()}
    </span>
  );
}

export function StatusBadge({ status }: { status: JobStatus }) {
  const t = useT();
  const live = status === "downloading" || status === "processing" || status === "analyzing";
  return (
    <span className={clsx("status", `status-${status}`)}>
      {live && <span className="pulse" />}
      {t(`status.${status}`)}
    </span>
  );
}

export function Progress({ value, state, large, showValue }: { value?: number | null; state?: "active" | "done" | "paused" | "failed" | "indeterminate" | "neutral"; large?: boolean; showValue?: boolean }) {
  const indeterminate = state === "indeterminate" || (state === "active" && (value === null || value === undefined));
  const bar = (
    <div className={clsx("progress", large && "progress-lg", indeterminate ? "indeterminate" : state)} role="progressbar" aria-valuenow={value ?? undefined}>
      <div className="progress-bar" style={{ width: `${Math.max(0, Math.min(100, value ?? 0))}%` }} />
    </div>
  );
  if (!showValue) return bar;
  return (
    <div className="progress-row">
      {bar}
      <span className="progress-value">{value != null ? `${Math.round(value)}%` : "—"}</span>
    </div>
  );
}

export function Check({ on, onClick }: { on: boolean; onClick?: () => void }) {
  return (
    <span className={clsx("check", on && "on")} onClick={onClick} role="checkbox" aria-checked={on}>
      {on && <CheckIcon size={13} strokeWidth={3} />}
    </span>
  );
}

export function Thumb({ src, badge, icon, className }: { src?: string; badge?: ReactNode; icon?: ReactNode; className?: string }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [src]);
  return (
    <div className={clsx("thumb", className)}>
      {src && !failed ? <img src={src} alt="" loading="lazy" draggable={false} onError={() => setFailed(true)} /> : <div className="thumb-fallback">{icon ?? <ImageOff size={20} />}</div>}
      {badge && <span className="thumb-badge">{badge}</span>}
    </div>
  );
}

export function Empty({ icon, title, body, action }: { icon: ReactNode; title: ReactNode; body?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty rise">
      <div className="empty-icon">{icon}</div>
      <div className="empty-title">{title}</div>
      {body && <div style={{ maxWidth: 440, margin: "0 auto" }}>{body}</div>}
      {action && <div style={{ marginTop: 20 }}>{action}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ Errors
export function ErrorAlert({ error, actions, compact }: { error: ApiErrorBody; actions?: ReactNode; compact?: boolean }) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const title = t(`errors.${error.code}`) !== `errors.${error.code}` ? t(`errors.${error.code}`) : error.message;
  const hintKey = `errors.${error.code}.hint`;
  const hint = t(hintKey) !== hintKey ? t(hintKey) : error.hint;
  return (
    <div className="alert alert-error" style={compact ? { padding: "10px 12px" } : undefined}>
      <OctagonAlert size={17} className="alert-icon" />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="alert-title">{title}</div>
        {hint && <div className="alert-body">{hint}</div>}
        <div className="row" style={{ marginTop: 10, flexWrap: "wrap" }}>
          {actions}
          {error.detail && (
            <Button size="sm" variant="ghost" onClick={() => setOpen(!open)}>
              {open ? t("common.hideDetails") : t("common.showDetails")}
            </Button>
          )}
          <span className="label" style={{ color: "var(--text-disabled)" }}>{error.code}</span>
        </div>
        {open && error.detail && <pre className="detail-pre">{error.detail}</pre>}
      </div>
    </div>
  );
}

export function Notice({ kind = "info", title, children }: { kind?: "info" | "warning"; title?: ReactNode; children?: ReactNode }) {
  return (
    <div className={clsx("alert", `alert-${kind}`)}>
      {kind === "warning" ? <AlertTriangle size={16} className="alert-icon" /> : <Info size={16} className="alert-icon" />}
      <div style={{ minWidth: 0 }}>
        {title && <div className="alert-title">{title}</div>}
        {children && <div className="alert-body">{children}</div>}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Dialog
export function Modal({ open, onClose, title, eyebrow, children, actions, width }: { open: boolean; onClose: () => void; title?: ReactNode; eyebrow?: ReactNode; children: ReactNode; actions?: ReactNode; width?: number }) {
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open, onClose]);
  return (
    <AnimatePresence>
      {open && (
        <motion.div className="modal-backdrop" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
          <motion.div
            className="modal"
            role="dialog"
            style={width ? { width: `min(${width}px, 100%)` } : undefined}
            initial={{ opacity: 0, y: 14 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 8 }}
            transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
          >
            <div className="row" style={{ alignItems: "flex-start", gap: 16 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                {eyebrow && <div className="eyebrow modal-eyebrow">{eyebrow}</div>}
                <div className="modal-title">{title}</div>
              </div>
              <Button iconOnly size="sm" icon={<X size={15} />} tip="Esc" onClick={onClose} />
            </div>
            <div className="muted">{children}</div>
            {actions && <div className="modal-actions">{actions}</div>}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
