/*
 * Timestamps render in the workspace timezone with the zone shown
 * (docs/12 §3 F2). Formatting is deterministic for a given value + zone, so
 * server and client render the same string.
 */

export type TimeFormat = "time" | "date" | "datetime";

const OPTIONS: Record<TimeFormat, Intl.DateTimeFormatOptions> = {
  time: { hour: "2-digit", minute: "2-digit", hourCycle: "h23" },
  date: { day: "numeric", month: "short", year: "numeric" },
  datetime: { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" },
};

export function toDate(value: Date | string | number): Date {
  return value instanceof Date ? value : new Date(value);
}

/**
 * The short zone name for an instant in a zone, e.g. "PDT", "UTC", "GMT+1".
 * ICU builds differ between Node and browsers for zero offsets ("GMT" vs
 * "GMT+0", "UTC" vs "GMT"), so offset-style names are normalised: a zero
 * offset is "GMT" (or "UTC" for the UTC zone itself).
 */
export function zoneAbbreviation(value: Date | string | number, timeZone: string, locale = "en-US"): string {
  const parts = new Intl.DateTimeFormat(locale, { timeZone, timeZoneName: "short" }).formatToParts(toDate(value));
  const raw = parts.find((p) => p.type === "timeZoneName")?.value ?? timeZone;
  if (timeZone === "UTC" || timeZone === "Etc/UTC") return "UTC";
  const offset = /^(?:GMT|UTC)(?:([+-])(\d{1,2})(?::(\d{2}))?)?$/.exec(raw);
  if (!offset) return raw;
  const [, sign, h, m] = offset;
  if (!sign || (Number(h) === 0 && !Number(m ?? 0))) return "GMT";
  return `GMT${sign}${Number(h)}${m && m !== "00" ? `:${m}` : ""}`;
}

export function formatInZone(
  value: Date | string | number,
  timeZone: string,
  format: TimeFormat = "time",
  locale = "en-GB",
): string {
  return new Intl.DateTimeFormat(locale, { ...OPTIONS[format], timeZone }).format(toDate(value));
}

/**
 * How long ago an instant was, for feeds and article lists ("2h ago"). The
 * exact instant goes in a <time> title (RelativeTime in components/ui/time.tsx).
 * Future instants (clock skew) read as "just now".
 */
export function relativeAge(value: Date | string | number, now: Date | string | number = Date.now()): string {
  const ms = toDate(now).getTime() - toDate(value).getTime();
  const min = Math.floor(ms / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}
