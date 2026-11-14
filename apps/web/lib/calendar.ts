/*
 * Calendar and publishing vocabulary (M8, calendar.html). Pure: tested without a DOM.
 *
 * Every time on the calendar is shown in the workspace time zone with the zone named
 * (docs/12 F2). Moving a post converts the wall time the person picked *in that zone*
 * back to an instant, so a person in another zone moves it to the time they see.
 */

import type { ScheduledPostState } from "@/lib/status";

export const PUBLISH_STATE_LABEL: Readonly<Record<string, string>> = {
  queued: "Scheduled",
  publishing: "Publishing",
  published: "Published",
  failed: "Retrying",
  dead_lettered: "Failed",
  cancelled: "Cancelled",
};

export function publishStateLabel(state: string, attempt = 0, max = 5): string {
  if (state === "failed") return `Retrying ${attempt}/${max}`;
  return PUBLISH_STATE_LABEL[state] ?? state;
}

export function asScheduledState(state: string): ScheduledPostState {
  return (Object.keys(PUBLISH_STATE_LABEL).includes(state) ? state : "queued") as ScheduledPostState;
}

function ymd(date: string): [number, number, number] {
  const [y = 1970, m = 1, d = 1] = date.split("-").map(Number);
  return [y, m, d];
}

function hm(time: string): [number, number] {
  const [h = 0, m = 0] = time.split(":").map(Number);
  return [h, m];
}

/** YYYY-MM-DD of an instant in a zone. */
export function localDate(instant: Date | string, tz: string): string {
  const d = typeof instant === "string" ? new Date(instant) : instant;
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

/** HH:MM of an instant in a zone (24-hour). */
export function localTime(instant: Date | string, tz: string): string {
  const d = typeof instant === "string" ? new Date(instant) : instant;
  return new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(d);
}

/** The zone's offset from UTC, in minutes, at an instant. */
export function offsetMinutes(instant: Date, tz: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(instant);
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? "0");
  const asUtc = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour") % 24, get("minute"), get("second"));
  return Math.round((asUtc - instant.getTime()) / 60000);
}

/** The instant at wall time ``date`` ``time`` in ``tz`` (handles DST by re-checking). */
export function zonedToUtc(date: string, time: string, tz: string): Date {
  const [y, m, d] = ymd(date);
  const [hh, mm] = hm(time);
  const guess = new Date(Date.UTC(y, m - 1, d, hh, mm));
  const first = new Date(guess.getTime() - offsetMinutes(guess, tz) * 60000);
  const second = new Date(guess.getTime() - offsetMinutes(first, tz) * 60000);
  return second;
}

export function addDays(date: string, n: number): string {
  const [y, m, d] = ymd(date);
  const t = new Date(Date.UTC(y, m - 1, d + n));
  return t.toISOString().slice(0, 10);
}

/** Monday of the week containing ``date`` (ISO weeks start Monday). */
export function weekStart(date: string): string {
  const [y, m, d] = ymd(date);
  const dow = new Date(Date.UTC(y, m - 1, d)).getUTCDay(); // 0 = Sunday
  return addDays(date, dow === 0 ? -6 : 1 - dow);
}

export function weekDays(start: string): string[] {
  return Array.from({ length: 7 }, (_, i) => addDays(start, i));
}

export function dayLabel(date: string): string {
  const [y, m, d] = ymd(date);
  return new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", weekday: "short", day: "numeric", month: "short" }).format(
    new Date(Date.UTC(y, m - 1, d)),
  );
}

export interface Placed {
  scheduled_for: string;
}

/** Bucket items by their local date in the workspace zone, each day sorted by time. */
export function byDay<T extends Placed>(items: readonly T[], tz: string): Map<string, T[]> {
  const out = new Map<string, T[]>();
  for (const it of [...items].sort((a, b) => a.scheduled_for.localeCompare(b.scheduled_for))) {
    const key = localDate(it.scheduled_for, tz);
    const list = out.get(key) ?? [];
    list.push(it);
    out.set(key, list);
  }
  return out;
}

/** Moving a post to another day keeps its time of day (the drag target is a day). */
export function moveToDay(scheduledFor: string, targetDate: string, tz: string): Date {
  return zonedToUtc(targetDate, localTime(scheduledFor, tz), tz);
}

export function canMove(state: string): boolean {
  return state === "queued" || state === "failed";
}

/** "+3 more" style windows: the week range label in the topbar. */
export function weekRangeLabel(start: string): string {
  const end = addDays(start, 6);
  const fmt = (s: string, withMonth: boolean) => {
    const [y, m, d] = ymd(s);
    return new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", day: "numeric", ...(withMonth ? { month: "short" } : {}) }).format(
      new Date(Date.UTC(y, m - 1, d)),
    );
  };
  return `${fmt(start, start.slice(5, 7) !== end.slice(5, 7))}–${fmt(end, true)}`;
}

/** A window around now: ``back`` days before to ``ahead`` days after, as ISO strings. */
export function aroundNow(back: number, ahead: number, now: Date = new Date()): { from: string; to: string } {
  const t = now.getTime();
  return { from: new Date(t - back * 86_400_000).toISOString(), to: new Date(t + ahead * 86_400_000).toISOString() };
}
