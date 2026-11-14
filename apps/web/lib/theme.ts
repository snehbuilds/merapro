/*
 * Theme: system / light / dark, persisted in localStorage["mera-theme"] (the
 * same key mera.js uses), applied as data-theme on <html>. The stored value is
 * the user's *preference*; data-theme is always the *resolved* light or dark.
 */

export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "mera-theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";

/**
 * Runs inline in <head> before first paint, so there is no flash of the wrong
 * theme. Kept dependency-free and tiny; mirrors resolve() below.
 */
export const THEME_PREPAINT_SCRIPT = `(function(){try{var p=localStorage.getItem(${JSON.stringify(
  THEME_STORAGE_KEY,
)});var d=p==="dark"||((p!=="light")&&window.matchMedia(${JSON.stringify(
  DARK_QUERY,
)}).matches);document.documentElement.setAttribute("data-theme",d?"dark":"light")}catch(e){}})();`;

function isPreference(v: unknown): v is ThemePreference {
  return v === "system" || v === "light" || v === "dark";
}

function readPreference(): ThemePreference {
  try {
    const v = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isPreference(v) ? v : "system";
  } catch {
    return "system";
  }
}

function systemTheme(): ResolvedTheme {
  return window.matchMedia(DARK_QUERY).matches ? "dark" : "light";
}

export function resolveTheme(pref: ThemePreference, system: ResolvedTheme): ResolvedTheme {
  return pref === "system" ? system : pref;
}

/* --- A tiny external store, read with useSyncExternalStore ----------------- */

export interface ThemeSnapshot {
  preference: ThemePreference;
  resolved: ResolvedTheme;
}

const SERVER_SNAPSHOT: ThemeSnapshot = { preference: "system", resolved: "light" };
let snapshot: ThemeSnapshot | null = null;
const listeners = new Set<() => void>();

function compute(): ThemeSnapshot {
  const preference = readPreference();
  return { preference, resolved: resolveTheme(preference, systemTheme()) };
}

function apply(next: ThemeSnapshot) {
  snapshot = next;
  document.documentElement.setAttribute("data-theme", next.resolved);
  listeners.forEach((l) => l());
}

export function getThemeSnapshot(): ThemeSnapshot {
  if (!snapshot) snapshot = compute();
  return snapshot;
}

export function getServerThemeSnapshot(): ThemeSnapshot {
  return SERVER_SNAPSHOT;
}

export function subscribeTheme(listener: () => void): () => void {
  listeners.add(listener);
  const mq = window.matchMedia(DARK_QUERY);
  const onSystem = () => {
    if (getThemeSnapshot().preference === "system") apply(compute());
  };
  const onStorage = (e: StorageEvent) => {
    if (e.key === THEME_STORAGE_KEY) apply(compute());
  };
  mq.addEventListener("change", onSystem);
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    mq.removeEventListener("change", onSystem);
    window.removeEventListener("storage", onStorage);
  };
}

export function setThemePreference(preference: ThemePreference) {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    /* storage blocked: still apply for this page view */
  }
  apply({ preference, resolved: resolveTheme(preference, systemTheme()) });
}
