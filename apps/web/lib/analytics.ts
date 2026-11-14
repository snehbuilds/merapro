/*
 * Analytics formatting (M10). A metric the platform does not report is `null` from the
 * API and is rendered "Not reported", never 0 (docs/11 M10 acceptance). Pure.
 */

export const METRIC_LABEL: Readonly<Record<string, string>> = {
  posts: "Posts",
  impressions: "Impressions",
  reach: "Reach",
  likes: "Likes",
  comments: "Comments",
  shares: "Shares",
  saves: "Saves",
  clicks: "Clicks",
  engagement: "Engagement",
  engagement_rate: "Engagement rate",
};

export const NOT_REPORTED = "Not reported";

export function formatMetric(value: number | null | undefined, key = ""): string {
  if (value === null || value === undefined) return NOT_REPORTED;
  if (key === "engagement_rate") return `${(value * 100).toFixed(1)}%`;
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 10_000) return `${(value / 1000).toFixed(1)}k`;
  return Math.round(value).toLocaleString("en-US");
}

/** "1.24×" against an account's own baseline; null means no baseline yet. */
export function formatIndex(index: number | null | undefined): string {
  if (index === null || index === undefined) return "No baseline yet";
  return `${index.toFixed(2)}×`;
}

export function indexTone(index: number | null | undefined): "ok" | "warn" | "neutral" {
  if (index === null || index === undefined) return "neutral";
  if (index >= 1.1) return "ok";
  if (index <= 0.9) return "warn";
  return "neutral";
}

export function formatMinutes(m: number | null | undefined): string {
  if (m === null || m === undefined) return "—";
  if (m < 60) return `${Math.round(m)} min`;
  const h = m / 60;
  return h < 48 ? `${h.toFixed(1)} h` : `${(h / 24).toFixed(1)} d`;
}

export interface Point {
  date: string;
  value: number | null;
}

/** Bar heights (0..1) for a series; nulls stay null (drawn as a gap, labelled). */
export function normalise(points: readonly Point[]): (number | null)[] {
  const max = Math.max(0, ...points.map((p) => p.value ?? 0));
  return points.map((p) => (p.value === null ? null : max === 0 ? 0 : p.value / max));
}

export type Scope = "workspace" | "group" | "account";

export function windowFrom(days: number, now = new Date()): string {
  return new Date(now.getTime() - days * 86_400_000).toISOString();
}

export function exportHref(workspaceId: string, query: Record<string, string | null | undefined>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) if (v) q.set(k, v);
  const s = q.toString();
  return `/api/mera/workspaces/${workspaceId}/analytics/export.csv${s ? `?${s}` : ""}`;
}
