import type { LevelSettings, PostingTime, ResolvedOut } from "@mera/sdk";
import type { ResolutionRule, SettingLevel } from "@/components/mera/resolved-setting-row";

/*
 * Settings that resolve across post → account → group → workspace → system
 * (docs/03 §2.2, ADR-0012), and how the UI words them. Most resolve to the
 * most specific level that sets them; autopilot mode and required approvers
 * resolve to the strictest.
 */

export const SETTING_LABEL: Readonly<Record<string, string>> = {
  brand_voice_id: "Brand voice",
  posting_times: "Posting slots",
  content_pillar_ids: "Content pillars",
  daily_post_cap: "Daily cap",
  quiet_hours: "Quiet hours",
  autopilot_mode: "Autopilot mode",
  required_approvers: "Approvers",
};

/** Display order for resolved-settings tables. */
export const SETTING_ORDER = [
  "brand_voice_id",
  "posting_times",
  "content_pillar_ids",
  "daily_post_cap",
  "quiet_hours",
  "autopilot_mode",
  "required_approvers",
];

export const AUTOPILOT_LABEL: Readonly<Record<string, string>> = {
  manual: "Manual",
  assisted: "Assisted",
  full: "Full autopilot",
};

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;
export const DAY_NAMES = DAYS;

/** [0,1,2,3,4] → "Mon–Fri"; [0,2,4] → "Mon, Wed, Fri". Runs of three or more collapse. */
export function formatDays(days: readonly number[]): string {
  const sorted = [...new Set(days)].filter((d) => d >= 0 && d <= 6).sort((a, b) => a - b);
  const parts: string[] = [];
  for (let i = 0; i < sorted.length; ) {
    let j = i;
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j]! + 1) j++;
    const run = j - i + 1;
    if (run >= 3) parts.push(`${DAYS[sorted[i]!]}–${DAYS[sorted[j]!]}`);
    else for (let k = i; k <= j; k++) parts.push(DAYS[sorted[k]!]!);
    i = j + 1;
  }
  return parts.join(", ");
}

/**
 * [{day:0..4, 09:00}, {day:0..4, 14:00}] → "Mon–Fri 09:00, 14:00". Days that
 * share the same set of times are written together; segments join with " · ".
 */
export function formatPostingTimes(times: readonly PostingTime[] | null | undefined): string {
  if (!times || times.length === 0) return "None";
  const byDay = new Map<number, string[]>();
  for (const t of times) byDay.set(t.day, [...(byDay.get(t.day) ?? []), t.time]);
  const byTimes = new Map<string, number[]>();
  for (const [day, list] of [...byDay].sort((a, b) => a[0] - b[0])) {
    const key = [...new Set(list)].sort().join(", ");
    byTimes.set(key, [...(byTimes.get(key) ?? []), day]);
  }
  return [...byTimes].map(([t, days]) => `${formatDays(days)} ${t}`).join(" · ");
}

function isPostingTimes(value: unknown): value is PostingTime[] {
  return Array.isArray(value) && value.every((v) => v && typeof v === "object" && "day" in v && "time" in v);
}

/** One resolved value in words. */
export function formatSettingValue(key: string, value: unknown): string {
  if (value === null || value === undefined) {
    return key === "quiet_hours" ? "None" : "Not set";
  }
  switch (key) {
    case "daily_post_cap":
      return `${String(value)} per account per day`;
    case "required_approvers": {
      const n = Number(value);
      return n === 1 ? "Any 1 approver" : `${n} approvers`;
    }
    case "autopilot_mode":
      return AUTOPILOT_LABEL[String(value)] ?? String(value);
    case "quiet_hours": {
      const q = value as { start?: string; end?: string };
      return q.start && q.end ? `${q.start}–${q.end}` : "None";
    }
    case "posting_times":
      return isPostingTimes(value) ? formatPostingTimes(value) : "Not set";
    case "content_pillar_ids":
      return Array.isArray(value) ? `${value.length} ${value.length === 1 ? "pillar" : "pillars"}` : "Not set";
    case "brand_voice_id":
      return "Set";
    default:
      return typeof value === "object" ? JSON.stringify(value) : String(value);
  }
}

const LEVELS: readonly SettingLevel[] = ["post", "account", "group", "workspace", "system"];

export function settingLevel(raw: string): SettingLevel {
  return (LEVELS as readonly string[]).includes(raw) ? (raw as SettingLevel) : "system";
}

/** The API says `strictest`; the component vocabulary is `strictest_wins`. */
export function resolutionRule(raw: string): ResolutionRule {
  return raw === "strictest" || raw === "strictest_wins" ? "strictest_wins" : "most_specific";
}

export interface ResolvedRow {
  key: string;
  label: string;
  value: string;
  source: SettingLevel;
  rule: ResolutionRule;
}

export function resolvedRows(resolved: readonly ResolvedOut[]): ResolvedRow[] {
  const rank = (k: string) => {
    const i = SETTING_ORDER.indexOf(k);
    return i === -1 ? SETTING_ORDER.length : i;
  };
  return [...resolved]
    .sort((a, b) => rank(a.key) - rank(b.key))
    .map((r) => ({
      key: r.key,
      label: SETTING_LABEL[r.key] ?? r.key,
      value: formatSettingValue(r.key, r.value),
      source: settingLevel(r.source_level),
      rule: resolutionRule(r.rule),
    }));
}

/** Stored LevelSettings from an untyped jsonb `settings` object. */
export function readLevelSettings(raw: unknown): LevelSettings {
  if (!raw || typeof raw !== "object") return {};
  const r = raw as Record<string, unknown>;
  const out: LevelSettings = {};
  if (typeof r.daily_post_cap === "number") out.daily_post_cap = r.daily_post_cap;
  if (typeof r.required_approvers === "number") out.required_approvers = r.required_approvers;
  if (r.autopilot_mode === "manual" || r.autopilot_mode === "assisted" || r.autopilot_mode === "full")
    out.autopilot_mode = r.autopilot_mode;
  const q = r.quiet_hours as { start?: unknown; end?: unknown } | null | undefined;
  if (q && typeof q.start === "string" && typeof q.end === "string") out.quiet_hours = { start: q.start, end: q.end };
  return out;
}

/** "HH:MM", 00:00–23:59, as the API validates it. */
export const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

/** A whole number in [min, max] from a form field; "" is null (inherit). */
export function parseBoundedInt(value: string, min: number, max: number): number | null | "invalid" {
  const t = value.trim();
  if (t === "") return null;
  if (!/^\d+$/.test(t)) return "invalid";
  const n = Number(t);
  return n < min || n > max ? "invalid" : n;
}
