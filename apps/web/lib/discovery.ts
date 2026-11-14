import type { TagProps } from "@/components/ui/tag";
import type { Pillar } from "@/lib/api/models";

/*
 * Content Discovery view logic (discovery.html): the filter state and its
 * URL form, score tags, risk-flag labels and the error copy for content
 * API calls. Scores are arithmetic (pillar similarity × source weight ×
 * freshness decay), so the UI shows them as numbers, never as a verdict.
 */

export type DiscoveryWindow = "24h" | "7d";

export const WINDOW_HOURS: Readonly<Record<DiscoveryWindow, number>> = { "24h": 24, "7d": 168 };
export const WINDOW_LABEL: Readonly<Record<DiscoveryWindow, string>> = { "24h": "Last 24h", "7d": "Last 7 days" };
export const WINDOWS: readonly DiscoveryWindow[] = ["24h", "7d"];

/** The "Relevance > 0.5" chip. */
export const MIN_RELEVANCE = 0.5;
export const TRENDING_LIMIT = 30;

export interface DiscoveryState {
  /** The group the list is ranked for; null = whole workspace (maximum across groups). */
  groupId: string | null;
  window: DiscoveryWindow;
  /** Only items with relevance ≥ MIN_RELEVANCE. */
  min: boolean;
}

type Query = Record<string, string | string[] | undefined> | URLSearchParams;

function param(query: Query, key: string): string | undefined {
  if (query instanceof URLSearchParams) return query.get(key) ?? undefined;
  const v = query[key];
  return typeof v === "string" ? v : undefined;
}

/**
 * Read ?group=&window=&min= (reload keeps the choice). An unknown group falls
 * back to the first group; with no groups the list is workspace-wide.
 */
export function parseDiscoveryQuery(query: Query, groups: readonly { id: string }[]): DiscoveryState {
  const g = param(query, "group");
  const groupId =
    g === "workspace" ? null : g && groups.some((x) => x.id === g) ? g : (groups[0]?.id ?? null);
  const w = param(query, "window");
  return { groupId, window: w === "7d" ? "7d" : "24h", min: param(query, "min") === String(MIN_RELEVANCE) };
}

/** The query string for a state: "?group=<id>&window=7d&min=0.5". Defaults are left out except the group. */
export function discoveryQuery(state: DiscoveryState): string {
  const q = new URLSearchParams();
  q.set("group", state.groupId ?? "workspace");
  if (state.window !== "24h") q.set("window", state.window);
  if (state.min) q.set("min", String(MIN_RELEVANCE));
  return `?${q.toString()}`;
}

/** Two decimals, as the mockup prints scores: 0.9 → "0.90". */
export function formatScore(value: number): string {
  return value.toFixed(2);
}

/** Relevance tag tone: ok ≥ 0.7, warn ≥ 0.5, plain below. */
export function relevanceTone(relevance: number): NonNullable<TagProps["tone"]> {
  if (relevance >= 0.7) return "ok";
  if (relevance >= 0.5) return "warn";
  return "neutral";
}

/** Research risk flags (backend RISK_FLAGS) in words. */
export const RISK_FLAG_LABEL: Readonly<Record<string, string>> = {
  paywalled: "Paywalled",
  opinion: "Opinion",
  press_release: "Press release",
  possibly_outdated: "Possibly outdated",
  low_credibility_source: "Low-credibility source",
};

export function riskFlagLabel(flag: string): string {
  const known = RISK_FLAG_LABEL[flag];
  if (known) return known;
  const words = flag.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** "Workspace" for a workspace-wide pillar, else the group's name. */
export function pillarScopeLabel(pillar: Pick<Pillar, "account_group_id">, groups: readonly { id: string; name: string }[]): string {
  if (pillar.account_group_id === null) return "Workspace";
  return groups.find((g) => g.id === pillar.account_group_id)?.name ?? "Another group";
}

/** One-decimal mono weight as in the mockup: 1.4 → "weight 1.4", 1 → "weight 1.0". */
export function formatWeight(weight: number): string {
  return `weight ${weight.toFixed(1)}`;
}

/** Fields the pillar form shows inline. */
export const PILLAR_FORM_FIELDS = ["name", "description", "keywords", "weight", "group_id"] as const;

/** Copy for content API errors that carry no field. */
export const DISCOVERY_ERROR_TEXT: Readonly<Record<string, string>> = {
  forbidden: "You don't have permission to do that here. Editors and up dismiss items and edit pillars.",
  workspace_archived: "This workspace is archived, so nothing here can change.",
  not_found: "That item or pillar no longer exists. Reload to see the current list.",
  pillar_limit_reached: "This workspace has reached its pillar limit. Remove one you no longer use first.",
  unknown_group: "That group no longer exists. Reload the page.",
};
