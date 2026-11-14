import { describe, expect, it } from "vitest";
import {
  MIN_RELEVANCE,
  discoveryQuery,
  formatScore,
  formatWeight,
  parseDiscoveryQuery,
  pillarScopeLabel,
  relevanceTone,
  riskFlagLabel,
} from "@/lib/discovery";
import {
  SOURCE_TYPES,
  displayUrl,
  filterSources,
  formatInterval,
  initialSourceId,
  intervalChoices,
  parseKeywords,
  sourceTypeOption,
  sourcesSummary,
} from "@/lib/sources";
import { relativeAge } from "@/lib/time";

describe("sources view logic", () => {
  it("offers every API source type; RSS and MCP tools are available (P3-M6.6)", () => {
    expect(SOURCE_TYPES.map((t) => t.type).sort()).toEqual(
      ["email", "gnews", "google_news", "hackernews", "mcp", "newsapi", "reddit", "rss", "youtube"].sort(),
    );
    expect(SOURCE_TYPES.filter((t) => t.available).map((t) => t.type)).toEqual(["rss", "mcp"]);
    expect(sourceTypeOption(SOURCE_TYPES[0]!)).toBe("RSS / Atom");
    expect(sourceTypeOption(SOURCE_TYPES.find((t) => t.type === "youtube")!)).toBe("YouTube channel — Not yet available");
  });

  it("humanises fetch intervals as the mockup does", () => {
    expect(formatInterval(900)).toBe("15m");
    expect(formatInterval(1800)).toBe("30m");
    expect(formatInterval(3600)).toBe("1h");
    expect(formatInterval(5400)).toBe("1h 30m");
    expect(formatInterval(86400)).toBe("24h");
    expect(intervalChoices(5400)).toContain(5400);
    expect(intervalChoices()).toEqual([900, 1800, 3600, 7200, 21600, 43200, 86400]);
  });

  it("parses comma-separated keywords, dropping blanks and repeats", () => {
    expect(parseKeywords(" postgres, database ,, Postgres ,")).toEqual(["postgres", "database"]);
    expect(parseKeywords("")).toEqual([]);
  });

  it("shortens feed URLs for the mono line", () => {
    expect(displayUrl("https://www.postgresql.org/news.rss")).toBe("postgresql.org/news.rss");
    expect(displayUrl("http://lwn.net/headlines/rss/")).toBe("lwn.net/headlines/rss");
    expect(displayUrl(null)).toBe("");
  });

  const list = [
    { id: "a", status: "active" as const, group_ids: ["g1"] },
    { id: "b", status: "failing" as const, group_ids: ["g2"] },
    { id: "c", status: "paused" as const, group_ids: [] },
  ];

  it("filters like GET /sources?group_id=: unlinked sources feed every group", () => {
    expect(filterSources(list, { view: "failing" }).map((s) => s.id)).toEqual(["b"]);
    expect(filterSources(list, { view: "all", group: "g1" }).map((s) => s.id)).toEqual(["a", "c"]);
    expect(filterSources(list, { view: "all", group: "workspace" }).map((s) => s.id)).toEqual(["c"]);
    expect(filterSources(list, { view: "paused", group: "g2" }).map((s) => s.id)).toEqual(["c"]);
    expect(filterSources(list, { view: "failing", group: "g1" }).map((s) => s.id)).toEqual([]);
  });

  it("opens the requested source, else the first failing one", () => {
    expect(initialSourceId(list, "c")).toBe("c");
    expect(initialSourceId(list, "missing")).toBe("b");
    expect(initialSourceId([list[0]!])).toBe("a");
    expect(initialSourceId([])).toBeUndefined();
  });

  it("summarises the list for the topbar", () => {
    expect(sourcesSummary(list)).toBe("3 configured · 1 failing · 1 paused");
    expect(sourcesSummary([list[0]!])).toBe("1 configured");
  });
});

describe("discovery view logic", () => {
  const groups = [{ id: "g1", name: "Tech News" }, { id: "g2", name: "Company" }];

  it("reads the filters from the URL with safe fallbacks", () => {
    expect(parseDiscoveryQuery({}, groups)).toEqual({ groupId: "g1", window: "24h", min: false });
    expect(parseDiscoveryQuery({ group: "workspace", window: "7d", min: "0.5" }, groups)).toEqual({
      groupId: null,
      window: "7d",
      min: true,
    });
    expect(parseDiscoveryQuery({ group: "gone", window: "1y" }, groups)).toEqual({ groupId: "g1", window: "24h", min: false });
    expect(parseDiscoveryQuery(new URLSearchParams("group=g2"), groups).groupId).toBe("g2");
    expect(parseDiscoveryQuery({}, []).groupId).toBeNull();
  });

  it("round-trips its state through the query string", () => {
    const state = { groupId: "g2", window: "7d" as const, min: true };
    expect(discoveryQuery(state)).toBe(`?group=g2&window=7d&min=${MIN_RELEVANCE}`);
    expect(parseDiscoveryQuery(new URLSearchParams(discoveryQuery(state)), groups)).toEqual(state);
    expect(discoveryQuery({ groupId: null, window: "24h", min: false })).toBe("?group=workspace");
  });

  it("tones relevance at 0.7 and 0.5 and prints two decimals", () => {
    expect(relevanceTone(0.91)).toBe("ok");
    expect(relevanceTone(0.7)).toBe("ok");
    expect(relevanceTone(0.61)).toBe("warn");
    expect(relevanceTone(0.38)).toBe("neutral");
    expect(formatScore(0.9)).toBe("0.90");
    expect(formatWeight(1)).toBe("weight 1.0");
  });

  it("names risk flags and pillar scopes in words", () => {
    expect(riskFlagLabel("press_release")).toBe("Press release");
    expect(riskFlagLabel("low_credibility_source")).toBe("Low-credibility source");
    expect(riskFlagLabel("brand_new_flag")).toBe("Brand new flag");
    expect(pillarScopeLabel({ account_group_id: null }, groups)).toBe("Workspace");
    expect(pillarScopeLabel({ account_group_id: "g2" }, groups)).toBe("Company");
  });

  it("gives relative ages for article meta lines", () => {
    const now = "2026-11-08T12:00:00Z";
    expect(relativeAge("2026-11-08T11:59:40Z", now)).toBe("just now");
    expect(relativeAge("2026-11-08T11:15:00Z", now)).toBe("45m ago");
    expect(relativeAge("2026-11-08T10:00:00Z", now)).toBe("2h ago");
    expect(relativeAge("2026-11-05T12:00:00Z", now)).toBe("3d ago");
    expect(relativeAge("2026-11-08T12:05:00Z", now)).toBe("just now");
  });
});

describe("isWebUrl", () => {
  it("allows only http(s) article links", async () => {
    const { isWebUrl } = await import("@/components/discovery/trending-card");
    expect(isWebUrl("https://e.com/a")).toBe(true);
    expect(isWebUrl("http://e.com/a")).toBe(true);
    expect(isWebUrl("javascript:alert(1)")).toBe(false);
    expect(isWebUrl("data:text/html,x")).toBe(false);
    expect(isWebUrl("not a url")).toBe(false);
  });
});
