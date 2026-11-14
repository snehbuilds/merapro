import { describe, expect, it } from "vitest";
import {
  formatIndex,
  formatMetric,
  formatMinutes,
  indexTone,
  exportHref,
  normalise,
  NOT_REPORTED,
} from "@/lib/analytics";
import {
  addDays,
  byDay,
  canMove,
  localDate,
  localTime,
  moveToDay,
  publishStateLabel,
  weekDays,
  weekRangeLabel,
  weekStart,
  zonedToUtc,
} from "@/lib/calendar";

describe("calendar time zones", () => {
  it("converts a wall time in the workspace zone to the right instant", () => {
    expect(zonedToUtc("2026-11-10", "09:00", "Asia/Kolkata").toISOString()).toBe("2026-11-10T03:30:00.000Z");
    expect(zonedToUtc("2026-11-10", "09:00", "America/Los_Angeles").toISOString()).toBe("2026-11-10T17:00:00.000Z");
    // Across a DST change (US, 2026-03-08): 09:00 PDT is 16:00 UTC.
    expect(zonedToUtc("2026-03-09", "09:00", "America/Los_Angeles").toISOString()).toBe("2026-03-09T16:00:00.000Z");
  });

  it("reads local dates and times", () => {
    expect(localDate("2026-11-10T20:00:00Z", "Asia/Kolkata")).toBe("2026-11-11");
    expect(localTime("2026-11-10T20:00:00Z", "Asia/Kolkata")).toBe("01:30");
  });

  it("builds Monday-first weeks", () => {
    expect(weekStart("2026-11-12")).toBe("2026-11-09");
    expect(weekStart("2026-11-15")).toBe("2026-11-09");
    expect(weekDays("2026-11-09")).toHaveLength(7);
    expect(addDays("2026-12-30", 3)).toBe("2027-01-02");
    expect(weekRangeLabel("2026-11-09")).toBe("9–15 Nov");
    expect(weekRangeLabel("2026-11-30")).toBe("30 Nov–6 Dec");
  });

  it("buckets by local day and keeps the time of day when moved", () => {
    const items = [
      { id: "b", scheduled_for: "2026-11-10T04:00:00Z" },
      { id: "a", scheduled_for: "2026-11-10T03:30:00Z" },
      { id: "c", scheduled_for: "2026-11-10T20:00:00Z" },
    ];
    const days = byDay(items, "Asia/Kolkata");
    expect(days.get("2026-11-10")?.map((i) => i.id)).toEqual(["a", "b"]);
    expect(days.get("2026-11-11")?.map((i) => i.id)).toEqual(["c"]);
    expect(moveToDay("2026-11-10T03:30:00Z", "2026-11-12", "Asia/Kolkata").toISOString()).toBe("2026-11-12T03:30:00.000Z");
    expect(canMove("queued") && canMove("failed")).toBe(true);
    expect(canMove("published")).toBe(false);
  });

  it("labels publish states", () => {
    expect(publishStateLabel("failed", 2, 5)).toBe("Retrying 2/5");
    expect(publishStateLabel("dead_lettered")).toBe("Failed");
  });
});

describe("analytics formatting", () => {
  it("never renders a missing metric as zero", () => {
    expect(formatMetric(null)).toBe(NOT_REPORTED);
    expect(formatMetric(undefined)).toBe(NOT_REPORTED);
    expect(formatMetric(0)).toBe("0");
    expect(formatMetric(12_345)).toBe("12.3k");
    expect(formatMetric(0.0456, "engagement_rate")).toBe("4.6%");
  });

  it("formats comparison and turnaround", () => {
    expect(formatIndex(null)).toBe("No baseline yet");
    expect(formatIndex(1.234)).toBe("1.23×");
    expect(indexTone(1.3)).toBe("ok");
    expect(indexTone(0.5)).toBe("warn");
    expect(formatMinutes(42)).toBe("42 min");
    expect(formatMinutes(150)).toBe("2.5 h");
    expect(formatMinutes(null)).toBe("—");
  });

  it("normalises bars and keeps gaps", () => {
    expect(normalise([{ date: "a", value: 5 }, { date: "b", value: null }, { date: "c", value: 10 }])).toEqual([0.5, null, 1]);
    expect(exportHref("ws", { from: "x", group_id: null })).toBe("/api/mera/workspaces/ws/analytics/export.csv?from=x");
  });
});
