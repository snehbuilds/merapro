import type { Source, SourceKind } from "@/lib/api/models";

/*
 * Sources view logic (sources.html, docs/12 §4): the type catalogue (RSS /
 * Atom is the only fetcher in Phase 1), intervals, keyword parsing, filters
 * and the error copy for source API calls.
 */

export interface SourceTypeSpec {
  type: SourceKind;
  /** Option label in the Type select. */
  label: string;
  /** Short label in the table's Type column. */
  short: string;
  /** Has a fetcher. Everything but RSS is Phase 2+ (docs/12 §4, M4.2). */
  available: boolean;
}

/** Every value of the API's source_type enum, in the mockup's order. */
export const SOURCE_TYPES: readonly SourceTypeSpec[] = [
  { type: "rss", label: "RSS / Atom", short: "RSS", available: true },
  { type: "hackernews", label: "Hacker News", short: "Hacker News", available: false },
  { type: "reddit", label: "Reddit", short: "Reddit", available: false },
  { type: "google_news", label: "Google News query", short: "Google News", available: false },
  { type: "youtube", label: "YouTube channel", short: "YouTube", available: false },
  { type: "newsapi", label: "News API", short: "News API", available: false },
  { type: "gnews", label: "GNews", short: "GNews", available: false },
  { type: "email", label: "Email ingest", short: "Email", available: false },
  { type: "mcp", label: "MCP server tool", short: "MCP", available: true },
];

export function sourceTypeLabel(type: SourceKind): string {
  return SOURCE_TYPES.find((t) => t.type === type)?.short ?? type;
}

/** The Type select's option text: unavailable types say so. */
export function sourceTypeOption(spec: SourceTypeSpec): string {
  return spec.available ? spec.label : `${spec.label} — Not yet available`;
}

/** API bounds for fetch_interval_seconds: 15 minutes to 24 hours; default 1 hour. */
export const INTERVAL_MIN_SECONDS = 900;
export const INTERVAL_MAX_SECONDS = 86400;
export const DEFAULT_INTERVAL_SECONDS = 3600;
export const INTERVAL_OPTIONS: readonly number[] = [900, 1800, 3600, 7200, 21600, 43200, 86400];

/** 900 → "15m", 3600 → "1h", 5400 → "1h 30m", 86400 → "24h". */
export function formatInterval(seconds: number): string {
  const totalMin = Math.round(seconds / 60);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h === 0) return `${m}m`;
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}

/** The interval choices for a form, including a current value that isn't a preset. */
export function intervalChoices(current?: number): number[] {
  const list = [...INTERVAL_OPTIONS];
  if (current !== undefined && !list.includes(current)) list.push(current);
  return list.sort((a, b) => a - b);
}

/** "postgres, database ,, " → ["postgres", "database"]. Duplicates (case-insensitive) are dropped. */
export function parseKeywords(value: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of value.split(",")) {
    const k = raw.trim();
    if (!k || seen.has(k.toLowerCase())) continue;
    seen.add(k.toLowerCase());
    out.push(k);
  }
  return out;
}

export function joinKeywords(keywords: readonly string[]): string {
  return keywords.join(", ");
}

/** "https://www.postgresql.org/news.rss" → "postgresql.org/news.rss" for the mono line. */
export function displayUrl(url: string | null): string {
  if (!url) return "";
  return url.replace(/^[a-z][a-z0-9+.-]*:\/\//i, "").replace(/^www\./i, "").replace(/\/$/, "");
}

/** "9 configured · 1 failing" (the topbar sub). */
export function sourcesSummary(sources: readonly Pick<Source, "status">[]): string {
  const failing = sources.filter((s) => s.status === "failing").length;
  const paused = sources.filter((s) => s.status === "paused").length;
  const parts = [`${sources.length} configured`];
  if (failing) parts.push(`${failing} failing`);
  if (paused) parts.push(`${paused} paused`);
  return parts.join(" · ");
}

export interface SourceFilter {
  view: "all" | "failing" | "paused";
  /** A group id: sources that feed it (linked to it, or to no group at all). "workspace": unlinked sources only. */
  group?: string;
}

export const ALL_SOURCES: SourceFilter = { view: "all" };

/** Mirrors GET /sources?group_id=: a source feeds a group if linked to it, or to nothing. */
export function feedsGroup(source: Pick<Source, "group_ids">, groupId: string): boolean {
  return source.group_ids.length === 0 || source.group_ids.includes(groupId);
}

export function filterSources<T extends Pick<Source, "status" | "group_ids">>(sources: readonly T[], filter: SourceFilter): T[] {
  return sources.filter((s) => {
    if (filter.view !== "all" && s.status !== filter.view) return false;
    if (filter.group === "workspace") return s.group_ids.length === 0;
    if (filter.group) return feedsGroup(s, filter.group);
    return true;
  });
}

/** The source to show in the detail panel first: the requested one, else the first failing, else the first. */
export function initialSourceId(sources: readonly Pick<Source, "id" | "status">[], requested?: string): string | undefined {
  if (requested && sources.some((s) => s.id === requested)) return requested;
  return (sources.find((s) => s.status === "failing") ?? sources[0])?.id;
}

/** Fields the add / edit forms show inline; any other field error goes to the form notice. */
export const SOURCE_FORM_FIELDS = [
  "source_type",
  "url",
  "name",
  "include_keywords",
  "exclude_keywords",
  "fetch_interval_seconds",
  "weight",
  "group_ids",
] as const;
export type SourceFormField = (typeof SOURCE_FORM_FIELDS)[number];

/**
 * Copy for source API errors that carry no field (field errors such as
 * duplicate_source, url_not_allowed and invalid_url show the API's message
 * next to the field).
 */
export const SOURCE_ERROR_TEXT: Readonly<Record<string, string>> = {
  forbidden:
    "You can't change this source. Editors and up configure sources, and a group-scoped editor only those that feed their groups.",
  workspace_archived: "This workspace is archived, so its sources can't change.",
  source_type_unavailable: "Only RSS / Atom feeds can be added for now.",
  source_limit_reached: "This workspace has reached its source limit. Remove a source you no longer use first.",
  source_paused: "This source is paused. Resume it before fetching.",
  not_found: "That source no longer exists. Reload to see the current list.",
};

/** "Queued — runs within a minute." after Fetch now / Retry. */
export const FETCH_QUEUED_TEXT = "Queued — runs within a minute.";
