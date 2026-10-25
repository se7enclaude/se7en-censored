import { AnimatePresence, motion } from "motion/react";
import { AlertTriangle, CheckCircle2, Info, X, XCircle } from "lucide-react";
import { useApp } from "../store/app";
import { Button } from "./ui";

const ICONS = { success: CheckCircle2, error: XCircle, info: Info, warning: AlertTriangle };

export function Toasts() {
  const toasts = useApp((s) => s.toasts);
  const dismiss = useApp((s) => s.dismiss);
  return (
    <div className="toasts" aria-live="polite">
      <AnimatePresence initial={false}>
        {toasts.map((t) => {
          const Icon = ICONS[t.kind];
          const timeout = t.timeout ?? (t.kind === "error" ? 9000 : 5000);
          return (
            <motion.div
              key={t.id}
              layout
              className={`toast toast-${t.kind}`}
              initial={{ opacity: 0, x: 40, scale: 0.96 }}
              animate={{ opacity: 1, x: 0, scale: 1 }}
              exit={{ opacity: 0, x: 40, scale: 0.96, transition: { duration: 0.18 } }}
              transition={{ type: "spring", stiffness: 420, damping: 32 }}
            >
              <div className="toast-icon">
                <Icon size={17} />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="toast-title">{t.title}</div>
                {t.body && <div className="toast-body ellipsis">{t.body}</div>}
                {t.action && (
                  <Button
                    size="sm"
                    style={{ marginTop: 8 }}
                    onClick={() => {
                      t.action!.run();
                      dismiss(t.id);
                    }}
                  >
                    {t.action.label}
                  </Button>
                )}
              </div>
              <Button variant="ghost" size="sm" iconOnly icon={<X size={14} />} onClick={() => dismiss(t.id)} />
              {timeout > 0 && (
                <motion.div
                  className="toast-timer"
                  initial={{ width: "100%" }}
                  animate={{ width: "0%" }}
                  transition={{ duration: timeout / 1000, ease: "linear" }}
                />
              )}
            </motion.div>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
