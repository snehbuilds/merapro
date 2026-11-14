/* Autopilot modes (P3-M3): the copy and the threshold rule the API applies. */

export type AutopilotMode = "manual" | "assisted" | "full";

export const DEFAULT_THRESHOLD = 0.85;

export const AUTOPILOT_COPY: Record<AutopilotMode, { label: string; help: string }> = {
  manual: {
    label: "Manual",
    help: "Every post needs a person's approval of its exact text, per account.",
  },
  assisted: {
    label: "Assisted",
    help: "Drafts the Editor is confident about publish on their own; the rest wait for approval.",
  },
  full: {
    label: "Full Autopilot",
    help: "Every draft that passes the checks publishes on its own. People are told, and can stop it.",
  },
};

/** 0.50 to 0.99 inclusive, as the API's settings model requires; null otherwise. */
export function parseThreshold(raw: string): number | null {
  const t = raw.trim();
  if (!/^\d*\.?\d+$/.test(t)) return null;
  const n = Number(t);
  return n >= 0.5 && n <= 0.99 ? Math.round(n * 100) / 100 : null;
}
