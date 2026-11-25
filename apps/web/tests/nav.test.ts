import { describe, expect, it } from "vitest";
import { NAV_GROUPS, PALETTE_ORDER, SCREENS, paletteScreens, screenFromPath, visibleNav } from "@/lib/nav";
import { resolveTheme } from "@/lib/theme";

describe("navigation model", () => {
  it("covers every screen exactly once in the rail and the palette", () => {
    const ids = Object.keys(SCREENS).sort();
    expect(NAV_GROUPS.flatMap((g) => g.items).sort()).toEqual(ids);
    expect([...PALETTE_ORDER].sort()).toEqual(ids);
  });

  it("hides Phase 2 screens behind feature flags", () => {
    const off = { inbox: false, mcp: false };
    const navIds = visibleNav(off).flatMap((g) => g.items.map((s) => s.id));
    expect(navIds).not.toContain("inbox");
    expect(navIds).not.toContain("mcp");
    expect(paletteScreens(off).map((s) => s.id)).not.toContain("inbox");

    const on = { inbox: true, mcp: true };
    expect(visibleNav(on).flatMap((g) => g.items.map((s) => s.id))).toContain("mcp");
  });

  it("keeps the mera.js rail group order", () => {
    expect(visibleNav().map((g) => g.label)).toEqual(["Work", "Content", "Accounts", "System"]);
  });

  it("finds the screen from a workspace path", () => {
    expect(screenFromPath("/w/acme/approvals")).toBe("approvals");
    expect(screenFromPath("/w/acme/nope")).toBeUndefined();
    expect(screenFromPath("/")).toBeUndefined();
  });
});

describe("theme", () => {
  it("keeps the application in light mode", () => {
    expect(resolveTheme("system", "dark")).toBe("light");
    expect(resolveTheme("light", "dark")).toBe("light");
    expect(resolveTheme("dark", "light")).toBe("light");
  });
});
