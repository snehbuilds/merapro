import { describe, expect, it } from "vitest";
import { capabilityTags } from "@/components/mera/capability-tags";
import { formatOffset } from "@/components/mera/stagger-timeline";
import { expiryStatus, formatRemaining } from "@/lib/expiry";
import { countGraphemes } from "@/lib/graphemes";
import { groupColor, groupColorIndex, groupHue } from "@/lib/group-color";
import { formatInZone, zoneAbbreviation } from "@/lib/time";

describe("countGraphemes", () => {
  it("counts user-perceived characters, not code units", () => {
    expect(countGraphemes("")).toBe(0);
    expect(countGraphemes("abc")).toBe(3);
    expect(countGraphemes("👍")).toBe(1); // surrogate pair: length 2
    expect(countGraphemes("👍🏽")).toBe(1); // emoji + skin tone modifier
    expect(countGraphemes("👩‍💻")).toBe(1); // ZWJ sequence
    expect(countGraphemes("🇳🇿")).toBe(1); // regional indicator pair
    expect(countGraphemes("é")).toBe(1); // e + combining acute
    expect(countGraphemes("Z͑ͫ̓")).toBe(1); // stacked combining marks
    expect(countGraphemes("café 👍🏽!")).toBe(7);
  });
});

describe("groupColor", () => {
  it("uses the mockup seeds for the first three groups", () => {
    expect(groupColor(0)).toBe("var(--g-1)");
    expect(groupColor(1)).toBe("var(--g-2)");
    expect(groupColor(2)).toBe("var(--g-3)");
  });

  it("generates light/dark-safe colours beyond the seeds", () => {
    const c = groupColor(3);
    expect(c).toMatch(/^light-dark\(oklch\(0\.52 0\.1 [\d.]+\), oklch\(0\.76 0\.1 [\d.]+\)\)$/);
    const hues = [3, 4, 5, 6, 7, 8].map(groupHue);
    expect(new Set(hues).size).toBe(hues.length);
  });

  it("is stable for an id", () => {
    expect(groupColorIndex("grp_01JTECH")).toBe(groupColorIndex("grp_01JTECH"));
    expect(groupColor("grp_01JTECH")).toBe(groupColor("grp_01JTECH"));
  });
});

describe("expiry", () => {
  const now = "2026-11-08T21:18:00Z";
  it("is warn above two hours, danger within, neutral when past", () => {
    expect(expiryStatus("2026-11-09T09:00:00Z", now)).toMatchObject({ urgency: "warn", label: "Expires in 11h 42m" });
    expect(expiryStatus("2026-11-08T23:12:00Z", now)).toMatchObject({ urgency: "danger", label: "Expires in 1h 54m" });
    expect(expiryStatus("2026-11-08T21:00:00Z", now)).toMatchObject({ urgency: "expired", tone: "neutral" });
    expect(formatRemaining(54 * 60000)).toBe("54m");
  });
});

describe("time in zone", () => {
  it("formats in the workspace zone with the abbreviation", () => {
    const t = "2026-11-09T17:00:00Z";
    expect(formatInZone(t, "America/Los_Angeles")).toBe("09:00");
    expect(zoneAbbreviation(t, "America/Los_Angeles")).toBe("PST");
    expect(formatInZone(t, "UTC")).toBe("17:00");
    expect(zoneAbbreviation(t, "UTC")).toBe("UTC");
    // Zero offsets are normalised across ICU builds ("GMT+0" vs "GMT").
    expect(zoneAbbreviation(t, "Europe/London")).toBe("GMT");
    expect(zoneAbbreviation("2026-07-01T12:00:00Z", "Europe/London")).toMatch(/^(BST|GMT\+1)$/);
  });
});

describe("stagger offsets and capabilities", () => {
  it("formats offsets as in the composer", () => {
    expect(formatOffset(0)).toBe("+0");
    expect(formatOffset(4 * 60000)).toBe("+4m");
    expect(formatOffset(75 * 60000)).toBe("+1h 15m");
  });

  it("orders capability tags supported, unsupported, partial", () => {
    const tags = capabilityTags({ supports_threads: false, supports_publish: true, analytics: "counts" });
    expect(tags.map((t) => t.label)).toEqual(["Publish", "No threads", "Counts only, no impressions"]);
  });
});
