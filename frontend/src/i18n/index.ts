import { useApp } from "../store/app";
import { en } from "./en";
import { fr } from "./fr";

export type Dict = Record<string, string>;
export const dictionaries: Record<string, Dict> = { en, fr };

export function resolveLang(pref?: string): "en" | "fr" {
  if (pref === "en" || pref === "fr") return pref;
  const nav = (navigator.language || "en").toLowerCase();
  return nav.startsWith("fr") ? "fr" : "en";
}

export function translate(lang: string, key: string, vars?: Record<string, string | number>): string {
  let s = dictionaries[lang]?.[key] ?? en[key] ?? key;
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(String(v));
  return s;
}

export function useT() {
  const pref = useApp((s) => s.settings?.general.language);
  const lang = resolveLang(pref);
  const t = (key: string, vars?: Record<string, string | number>) => translate(lang, key, vars);
  /** Translate text that normally comes from the backend; keeps the backend text when no key exists. */
  const or = (key: string, fallback?: string | null) => {
    const v = translate(lang, key);
    return v === key ? fallback ?? "" : v;
  };
  return Object.assign(t, { lang, or });
}
