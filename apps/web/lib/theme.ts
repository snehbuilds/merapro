/*
 * Talonic currently ships with one visual theme. Keep the public theme API in
 * place so existing consumers remain compatible, but always resolve and apply
 * light mode regardless of an old saved preference or the operating system.
 */

export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "mera-theme";

/**
 * Runs inline in <head> before first paint, so there is no flash of the wrong
 * theme. Kept dependency-free and tiny; mirrors resolve() below.
 */
export const THEME_PREPAINT_SCRIPT = `(function(){try{localStorage.setItem(${JSON.stringify(
  THEME_STORAGE_KEY,
)},"light")}catch(e){}document.documentElement.setAttribute("data-theme","light")})();`;

export function resolveTheme(_pref: ThemePreference, _system: ResolvedTheme): ResolvedTheme {
  void _pref;
  void _system;
  return "light";
}

/* --- A tiny external store, read with useSyncExternalStore ----------------- */

export interface ThemeSnapshot {
  preference: ThemePreference;
  resolved: ResolvedTheme;
}

const SERVER_SNAPSHOT: ThemeSnapshot = { preference: "light", resolved: "light" };
let snapshot: ThemeSnapshot | null = null;
const listeners = new Set<() => void>();

function compute(): ThemeSnapshot {
  return SERVER_SNAPSHOT;
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
  return () => {
    listeners.delete(listener);
  };
}

export function setThemePreference(_preference: ThemePreference) {
  void _preference;
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
  } catch {
    /* storage blocked: still apply for this page view */
  }
  apply(SERVER_SNAPSHOT);
}
