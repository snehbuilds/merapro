// @vitest-environment jsdom
import type { GroupOut } from "@mera/sdk";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ShellProvider } from "@/components/shell/shell-provider";
import type { ContentItem, ContentStats, Pillar, Source, SourceHealth } from "@/lib/api/models";

const router = { replace: vi.fn(), refresh: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router, usePathname: () => "/w/acme/sources" }));

/* The SDK operations the screens call, as mocks returning the SDK's { data, error, response } shape. */
const sdk = {
  createSource: vi.fn(),
  updateSource: vi.fn(),
  deleteSource: vi.fn(),
  setSourceGroups: vi.fn(),
  fetchSource: vi.fn(),
  sourceHealth: vi.fn(),
  testFeed: vi.fn(),
  trending: vi.fn(),
  contentStats: vi.fn(),
  listPillars: vi.fn(),
  createPillar: vi.fn(),
  updatePillar: vi.fn(),
  deletePillar: vi.fn(),
  dismissContentItem: vi.fn(),
};
vi.mock("@mera/sdk", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@mera/sdk")>();
  const mocked = Object.fromEntries(Object.keys(sdk).map((k) => [k, (...a: unknown[]) => sdk[k as keyof typeof sdk](...a)]));
  return { ...actual, ...mocked };
});

window.matchMedia ??= ((query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => false,
})) as typeof window.matchMedia;

const { SourcesScreen } = await import("@/components/sources/sources-screen");
const { DiscoveryScreen } = await import("@/components/discovery/discovery-screen");

function ok<T>(data: T, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) });
}
function fail(status: number, code: string, message: string, field?: string) {
  return Promise.resolve({
    error: { error: { code, message, field_errors: field ? [{ field, code, message }] : [] } },
    response: new Response(null, { status }),
  });
}

const ws = { id: "ws-1", slug: "acme", name: "Acme", timezone: "UTC", archived: false };
const shellWs = { ...ws, mark: "AC", meta: "Editor · UTC", role: "editor" };
const user = { id: "u1", name: "Alex", email: "a@b.c", initials: "AL", emailVerified: true };

function shell(children: ReactNode) {
  return (
    <ShellProvider workspace={shellWs} workspaces={[shellWs]} user={user}>
      {children}
    </ShellProvider>
  );
}

function group(id: string, name: string): GroupOut {
  return {
    id,
    name,
    description: null,
    color: null,
    default_brand_voice_id: null,
    posting_schedule: null,
    content_pillar_ids: [],
    stagger_mode: "random_spread",
    stagger_min_seconds: 180,
    stagger_max_seconds: 900,
    enabled: true,
    settings: {},
    member_count: 0,
    members: [],
    created_at: "2026-11-01T00:00:00Z",
  };
}
const groups = [group("grp-1", "Tech News"), group("grp-2", "Company Official")];

function source(id: string, over: Partial<Source> = {}): Source {
  return {
    id,
    source_type: "rss",
    name: id,
    url: `https://${id}.example.com/feed.xml`,
    include_keywords: [],
    exclude_keywords: [],
    language: null,
    fetch_interval_seconds: 3600,
    weight: 1,
    status: "active",
    group_ids: [],
    consecutive_failures: 0,
    last_error: null,
    last_fetched_at: null,
    last_success_at: "2026-11-07T23:00:00Z",
    next_fetch_at: "2026-11-08T11:30:00Z",
    items_7d: 14,
    created_at: "2026-11-01T00:00:00Z",
    ...over,
  };
}

const pg = source("src-pg", { name: "PostgreSQL News", url: "https://www.postgresql.org/news.rss", group_ids: ["grp-1"] });
const lwn = source("src-lwn", {
  name: "LWN.net",
  url: "https://lwn.net/headlines/rss",
  status: "failing",
  group_ids: ["grp-1"],
  consecutive_failures: 12,
  last_error: "http_status: HTTP 403",
  items_7d: 0,
});
const paused = source("src-blog", { name: "Acme Blog", status: "paused", fetch_interval_seconds: 21600, items_7d: 2 });

function health(over: Partial<SourceHealth> = {}): SourceHealth {
  return {
    source_id: "src-lwn",
    status: "failing",
    consecutive_failures: 12,
    failing_since: "2026-11-08T06:00:00Z",
    last_error: "http_status: HTTP 403",
    last_http_status: 403,
    last_fetched_at: "2026-11-08T10:30:00Z",
    last_success_at: "2026-11-07T23:00:00Z",
    next_fetch_at: "2026-11-08T11:30:00Z",
    retrying: false,
    items_7d: 0,
    filtered_7d: 3,
    last_yield: 0,
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  sdk.sourceHealth.mockImplementation(({ path }: { path: { source_id: string } }) =>
    ok(path.source_id === "src-lwn" ? health() : health({ source_id: path.source_id, status: "active", consecutive_failures: 0, failing_since: null, last_http_status: 200, last_error: null, retrying: true })),
  );
});
afterEach(cleanup);

function renderSources(props: Partial<Parameters<typeof SourcesScreen>[0]> = {}) {
  return render(shell(<SourcesScreen workspace={ws} sources={[pg, lwn, paused]} groups={groups} canManage {...props} />));
}

function rowFor(name: string) {
  const table = screen.getByRole("table");
  return within(table).getByRole("button", { name: `Details for ${name}` }).closest("tr")!;
}

describe("SourcesScreen", () => {
  it("lists sources worst first with feeds, status, interval and an honest Drafted column", async () => {
    renderSources();
    expect(screen.getByText("3 configured · 1 failing · 1 paused")).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell")[0]!.querySelector("button")!.textContent)).toEqual([
      "LWN.net",
      "Acme Blog",
      "PostgreSQL News",
    ]);
    const pgRow = rowFor("PostgreSQL News");
    expect(within(pgRow).getByText("postgresql.org/news.rss")).toBeInTheDocument();
    expect(within(pgRow).getByText("RSS")).toBeInTheDocument();
    expect(within(pgRow).getByText("Tech News")).toBeInTheDocument();
    expect(within(pgRow).getByText("Active")).toBeInTheDocument();
    expect(within(pgRow).getByText("1h")).toBeInTheDocument();
    expect(within(pgRow).getByText("14")).toBeInTheDocument();
    expect(within(pgRow).getByText("Not tracked until drafting arrives")).toBeInTheDocument();
    const blogRow = rowFor("Acme Blog");
    expect(within(blogRow).getByText("Workspace")).toBeInTheDocument();
    expect(within(blogRow).getByText("6h")).toBeInTheDocument();
    expect(within(blogRow).getByText("Paused")).toBeInTheDocument();
    // Await the health check so its state settles inside act().
    await screen.findByText(/Retries have stopped/);
  });

  it("selects the failing source and explains its health", async () => {
    renderSources();
    const detail = screen.getByRole("region", { name: "LWN.net" });
    expect(sdk.sourceHealth).toHaveBeenCalledWith(expect.objectContaining({ path: { workspace_id: "ws-1", source_id: "src-lwn" } }));
    expect(await within(detail).findByText(/Retries have stopped after 48 hours/)).toBeInTheDocument();
    const notice = detail.querySelector("[data-slot=notice]")!;
    expect(notice).toHaveTextContent("HTTP 403 since 8 Nov, 06:00 UTC.");
    expect(notice).toHaveTextContent("12 consecutive failures.");
    expect(within(detail).getByText("Retries stopped")).toBeInTheDocument();
    expect(within(detail).getByRole("button", { name: "Retry now" })).toBeInTheDocument();
    expect(within(detail).getByRole("button", { name: "Pause source" })).toBeInTheDocument();
    expect(within(detail).queryByRole("button", { name: /credentials/i })).toBeNull();
  });

  it("filters by status and group", async () => {
    renderSources();
    await screen.findByText(/Retries have stopped/);
    await userEvent.click(screen.getByRole("button", { name: /^Failing/ }));
    expect(screen.getByRole("button", { name: /^Failing/ })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getAllByRole("row")).toHaveLength(2);
    await userEvent.click(screen.getByRole("button", { name: "All" }));
    // A workspace-wide source feeds every group, so it shows under each group filter.
    await userEvent.click(within(screen.getByRole("toolbar")).getByRole("button", { name: "Company Official" }));
    const names = screen.getAllByRole("row").slice(1).map((r) => r.querySelector("button")!.textContent);
    expect(names).toEqual(["Acme Blog"]);
  });

  it("retries a failing source and says the fetch is queued", async () => {
    sdk.fetchSource.mockReturnValue(ok({ queued_at: "2026-11-08T11:00:00Z", detail: "The fetch runs within a minute." }, 202));
    renderSources();
    await screen.findByText(/Retries have stopped/);
    await userEvent.click(screen.getByRole("button", { name: "Retry LWN.net" }));
    expect(sdk.fetchSource).toHaveBeenCalledWith(expect.objectContaining({ path: { workspace_id: "ws-1", source_id: "src-lwn" } }));
    expect(await screen.findByText("LWN.net: Queued — runs within a minute.")).toBeInTheDocument();
    expect(screen.getByText("LWN.net: Queued — runs within a minute.").closest("[role=status]")).not.toBeNull();
  });

  it("resumes a paused source and pauses another from the More menu", async () => {
    sdk.updateSource.mockImplementation(({ path, body }: { path: { source_id: string }; body: { status: string } }) =>
      ok({ ...(path.source_id === paused.id ? paused : pg), status: body.status }),
    );
    renderSources();
    await screen.findByText(/Retries have stopped/);
    await userEvent.click(screen.getByRole("button", { name: "Resume Acme Blog" }));
    expect(sdk.updateSource).toHaveBeenCalledWith(
      expect.objectContaining({ path: { workspace_id: "ws-1", source_id: "src-blog" }, body: { status: "active" } }),
    );
    expect(await screen.findByText("Resumed Acme Blog. The next fetch runs within a minute.")).toBeInTheDocument();
    expect(within(rowFor("Acme Blog")).getByText("Active")).toBeInTheDocument();
    expect(router.refresh).toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "More actions for PostgreSQL News" }));
    await userEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(sdk.updateSource).toHaveBeenLastCalledWith(expect.objectContaining({ body: { status: "paused" } }));
    expect(await screen.findByText("Paused PostgreSQL News.")).toBeInTheDocument();
  });

  it("shows the mapped message when a group-scoped editor is refused", async () => {
    sdk.fetchSource.mockReturnValue(fail(403, "forbidden", "Forbidden."));
    renderSources();
    await screen.findByText(/Retries have stopped/);
    await userEvent.click(screen.getByRole("button", { name: "More actions for PostgreSQL News" }));
    await userEvent.click(screen.getByRole("button", { name: "Fetch now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/only those that feed their groups/);
  });

  it("deletes only after confirming", async () => {
    sdk.deleteSource.mockReturnValue(ok(undefined, 204));
    renderSources();
    await screen.findByText(/Retries have stopped/);
    await userEvent.click(screen.getByRole("button", { name: "More actions for PostgreSQL News" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete…" }));
    expect(sdk.deleteSource).not.toHaveBeenCalled();
    await userEvent.click(await screen.findByRole("button", { name: "Delete PostgreSQL News" }));
    expect(sdk.deleteSource).toHaveBeenCalledWith(expect.objectContaining({ path: { workspace_id: "ws-1", source_id: "src-pg" } }));
    expect(await screen.findByText("Deleted PostgreSQL News.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Details for PostgreSQL News" })).toBeNull();
  });

  it("lists every source type; RSS / Atom and MCP tools are selectable", () => {
    renderSources();
    const select = within(screen.getByRole("form", { name: "Add a source" })).getByLabelText("Type");
    const options = within(select).getAllByRole("option");
    expect(options).toHaveLength(9);
    expect(within(select).getByRole("option", { name: "RSS / Atom" })).toBeEnabled();
    expect(within(select).getByRole("option", { name: "Reddit — Not yet available" })).toBeDisabled();
    expect(within(select).getByRole("option", { name: "MCP server tool" })).toBeEnabled();
    expect(options.filter((o) => !(o as HTMLOptionElement).disabled)).toHaveLength(2);
  });

  it("tests a feed, takes its title as the name, and adds the source", async () => {
    sdk.testFeed.mockReturnValue(
      ok({
        ok: true,
        title: "Kafka Blog",
        item_count: 12,
        items: [{ title: "Kafka 4.0 removes ZooKeeper", url: "https://kafka.example/1", published_at: null }],
        error: null,
        error_code: null,
      }),
    );
    const created = source("src-new", { name: "Kafka Blog", url: "https://kafka.example/feed", group_ids: ["grp-2"] });
    sdk.createSource.mockReturnValue(ok(created, 201));
    renderSources();
    const form = screen.getByRole("form", { name: "Add a source" });
    await userEvent.type(within(form).getByLabelText("Feed URL"), "https://kafka.example/feed");
    await userEvent.click(within(form).getByRole("button", { name: "Test feed" }));
    expect(sdk.testFeed).toHaveBeenCalledWith(expect.objectContaining({ body: { url: "https://kafka.example/feed" } }));
    expect(await within(form).findByText("Kafka Blog · 12 items.")).toBeInTheDocument();
    expect(within(form).getByText("Kafka 4.0 removes ZooKeeper")).toBeInTheDocument();
    expect(within(form).getByLabelText("Name")).toHaveValue("Kafka Blog");

    await userEvent.type(within(form).getByLabelText("Exclude"), "webinar, sponsored,");
    const picker = within(form).getByRole("group", { name: "Feeds which groups" });
    await userEvent.click(within(picker).getByRole("button", { name: "Company Official" }));
    expect(within(picker).getByRole("button", { name: "Company Official" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(within(form).getByRole("button", { name: "Add source" }));

    expect(sdk.createSource).toHaveBeenCalledWith(
      expect.objectContaining({
        path: { workspace_id: "ws-1" },
        body: {
          source_type: "rss",
          name: "Kafka Blog",
          url: "https://kafka.example/feed",
          include_keywords: [],
          exclude_keywords: ["webinar", "sponsored"],
          fetch_interval_seconds: 3600,
          group_ids: ["grp-2"],
        },
      }),
    );
    expect(await screen.findByText("Added Kafka Blog. Its first fetch runs within a minute.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Details for Kafka Blog" })).toHaveAttribute("aria-pressed", "true");
    expect(within(form).getByLabelText("Feed URL")).toHaveValue("");
  });

  it("shows a failed feed test without saving anything", async () => {
    sdk.testFeed.mockReturnValue(ok({ ok: false, title: null, item_count: 0, items: [], error: "Not an RSS or Atom feed.", error_code: "not_a_feed" }));
    renderSources();
    const form = screen.getByRole("form", { name: "Add a source" });
    await userEvent.type(within(form).getByLabelText("Feed URL"), "https://example.com/");
    await userEvent.click(within(form).getByRole("button", { name: "Test feed" }));
    expect(await within(form).findByText("Not an RSS or Atom feed.")).toBeInTheDocument();
    expect(sdk.createSource).not.toHaveBeenCalled();
  });

  it("puts URL errors next to the field and other refusals in the form", async () => {
    renderSources();
    const form = screen.getByRole("form", { name: "Add a source" });
    await userEvent.click(within(form).getByRole("button", { name: "Add source" }));
    expect(within(form).getByText("Enter the feed's URL.")).toBeInTheDocument();
    expect(sdk.createSource).not.toHaveBeenCalled();

    await userEvent.type(within(form).getByLabelText("Feed URL"), "http://localhost/feed");
    await userEvent.type(within(form).getByLabelText("Name"), "Local");
    sdk.createSource.mockReturnValueOnce(fail(422, "url_not_allowed", "This URL cannot be fetched: private address.", "url"));
    await userEvent.click(within(form).getByRole("button", { name: "Add source" }));
    expect(await within(form).findByText("This URL cannot be fetched: private address.")).toBeInTheDocument();
    expect(within(form).getByLabelText("Feed URL")).toHaveAttribute("aria-invalid", "true");

    sdk.createSource.mockReturnValueOnce(fail(409, "duplicate_source", "This workspace already has a source with that URL.", "url"));
    await userEvent.click(within(form).getByRole("button", { name: "Add source" }));
    expect(await within(form).findByText("This workspace already has a source with that URL.")).toBeInTheDocument();

    sdk.createSource.mockReturnValueOnce(fail(409, "source_limit_reached", "A workspace can have at most 100 sources."));
    await userEvent.click(within(form).getByRole("button", { name: "Add source" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("This workspace has reached its source limit.");
  });

  it("is read-only for viewers", async () => {
    renderSources({ canManage: false });
    await screen.findByText(/Retries have stopped/);
    expect(screen.queryByRole("form", { name: "Add a source" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Retry/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /More actions/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Pause source" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Add source" })).toBeNull();
  });

  it("has no axe violations", async () => {
    const { container } = renderSources();
    await screen.findByText(/Retries have stopped/);
    expect((await axe.run(container)).violations).toEqual([]);
  });

  it("explains an empty workspace honestly", () => {
    renderSources({ sources: [] });
    expect(screen.getByText("No sources yet")).toBeInTheDocument();
    expect(screen.getByText("0 configured")).toBeInTheDocument();
  });
});

/* ---- Discovery ---- */

const HOUR = 3600_000;
const ago = (h: number) => new Date(Date.now() - h * HOUR).toISOString();

function item(id: string, over: Partial<ContentItem> = {}): ContentItem {
  return {
    id,
    title: id,
    url: `https://news.example/${id}`,
    author: null,
    language: "en",
    og_image_url: null,
    source_id: "src-pg",
    source_name: "postgresql.org",
    published_at: ago(2),
    fetched_at: ago(2),
    summary: "A summary.",
    topics: [],
    relevance: 0.91,
    freshness: 0.96,
    engagement_potential: 0.82,
    engagement_rationale: null,
    risk_flags: [],
    best_pillar: "Databases",
    coverage: 1,
    duplicates: [],
    dismissed: false,
    scored_group_id: "grp-1",
    state: "ready",
    state_reason: null,
    ...over,
  };
}

const pgRelease = item("it-pg", {
  title: "PostgreSQL 18 Released",
  coverage: 3,
  duplicates: [{ id: "it-dup", title: "Postgres 18 is out — what's new", source_name: "Google News", similarity: 0.94, fetched_at: ago(3) }],
});
const k8s = item("it-k8s", {
  title: "Why we moved off Kubernetes",
  relevance: 0.61,
  best_pillar: "Platform engineering",
  risk_flags: ["opinion", "press_release"],
  summary: null,
  state_reason: "summary_unavailable",
  source_name: "LWN.net",
});

const stats: ContentStats = { days: 7, ingested: 266, after_dedupe: 189, above_threshold: 31, filtered: 12, threshold: 0.5, drafted: null, published: null };

function pillar(id: string, over: Partial<Pillar> = {}): Pillar {
  return {
    id,
    name: id,
    description: null,
    keywords: [],
    weight: 1,
    account_group_id: "grp-1",
    embedded: true,
    created_at: "2026-11-01T00:00:00Z",
    updated_at: "2026-11-01T00:00:00Z",
    ...over,
  };
}
const pillars = [
  pillar("pl-db", { name: "Databases", weight: 1.4 }),
  pillar("pl-oss", { name: "Open source", weight: 0.8, account_group_id: null }),
  pillar("pl-new", { name: "Observability", embedded: false }),
];

function renderDiscovery(props: Partial<Parameters<typeof DiscoveryScreen>[0]> = {}) {
  return render(
    shell(
      <DiscoveryScreen
        workspace={ws}
        groups={groups}
        initialState={{ groupId: "grp-1", window: "24h", min: false }}
        items={[pgRelease, k8s]}
        stats={stats}
        pillars={pillars}
        canManage
        {...props}
      />,
    ),
  );
}

function card(title: string) {
  return screen.getByRole("article", { name: new RegExp(title.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")) });
}

describe("DiscoveryScreen", () => {
  beforeEach(() => {
    window.history.replaceState(null, "", "/w/acme/discovery");
    sdk.trending.mockReturnValue(ok({ data: [pgRelease] }));
    sdk.contentStats.mockReturnValue(ok(stats));
    sdk.listPillars.mockReturnValue(ok({ data: pillars }));
  });

  it("prints each item's scores, flags and coverage as the pipeline gave them", () => {
    renderDiscovery();
    expect(screen.getByText("Trending for Tech News")).toBeInTheDocument();
    const pgCard = card("PostgreSQL 18 Released");
    expect(within(pgCard).getByText("0.91 relevance")).toBeInTheDocument();
    expect(within(pgCard).getByText("Databases")).toBeInTheDocument();
    expect(within(pgCard).getByText("Covered by 3 sources")).toBeInTheDocument();
    expect(within(pgCard).getByText("Freshness 0.96")).toBeInTheDocument();
    expect(within(pgCard).getByText("Engagement potential 0.82")).toBeInTheDocument();
    expect(within(pgCard).getByText("2h ago")).toBeInTheDocument();
    const link = within(pgCard).getByRole("link", { name: /PostgreSQL 18 Released/ });
    expect(link).toHaveAttribute("href", "https://news.example/it-pg");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");

    const k8sCard = card("Why we moved off Kubernetes");
    expect(within(k8sCard).getByText("0.61 relevance")).toHaveAttribute("data-slot", "tag");
    expect(within(k8sCard).getByText("Opinion")).toBeInTheDocument();
    expect(within(k8sCard).getByText("Press release")).toBeInTheDocument();
    expect(within(k8sCard).getByText(/Summary unavailable/)).toBeInTheDocument();
    expect(within(k8sCard).queryByText(/Covered by/)).toBeNull();
  });

  it("shows a duplicate as a collapsed card pointing at its original", () => {
    renderDiscovery();
    const dup = screen.getByRole("article", { name: "Postgres 18 is out — what's new" });
    expect(dup).toHaveTextContent(
      "Collapsed into PostgreSQL 18 Released — semantic similarity 0.94. Kept rather than discarded, because coverage across sources is itself a relevance signal.",
    );
    expect(within(dup).getByRole("link", { name: "PostgreSQL 18 Released" })).toHaveAttribute("href", "#item-it-pg");
    expect(within(dup).getByText("Deduplicated")).toBeInTheDocument();
    expect(within(dup).getByText("Google News")).toBeInTheDocument();
  });

  it("keeps Draft for group disabled for viewers and says why", () => {
    renderDiscovery({ canManage: false });
    const draft = within(card("PostgreSQL 18 Released")).getByRole("button", { name: "Draft for group" });
    expect(draft).toBeDisabled();
    expect(draft).toHaveAccessibleDescription("Editors and up draft posts from these items.");
    expect(screen.queryByRole("link", { name: "Draft for group" })).toBeNull();
  });

  it("re-ranks for another group, the whole workspace, a wider window and a threshold, keeping each in the URL", async () => {
    renderDiscovery();
    const toolbar = screen.getByRole("toolbar", { name: "Rank and filter" });
    await userEvent.click(within(toolbar).getByRole("button", { name: "Company Official" }));
    expect(sdk.trending).toHaveBeenLastCalledWith(
      expect.objectContaining({ query: { group_id: "grp-2", hours: 24, min_relevance: 0, limit: 30 } }),
    );
    expect(sdk.contentStats).toHaveBeenLastCalledWith(expect.objectContaining({ query: { group_id: "grp-2", days: 7 } }));
    expect(sdk.listPillars).toHaveBeenLastCalledWith(expect.objectContaining({ query: { group_id: "grp-2" } }));
    expect(window.location.search).toBe("?group=grp-2");
    expect(within(toolbar).getByRole("button", { name: "Company Official" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Trending for Company Official")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("article", { name: "Why we moved off Kubernetes" })).toBeNull());

    await userEvent.click(within(toolbar).getByRole("button", { name: "Whole workspace" }));
    expect(sdk.trending).toHaveBeenLastCalledWith(expect.objectContaining({ query: expect.objectContaining({ group_id: null }) }));
    expect(window.location.search).toBe("?group=workspace");
    expect(await screen.findByText("Trending across the workspace")).toBeInTheDocument();

    const statsCalls = sdk.contentStats.mock.calls.length;
    await userEvent.click(within(toolbar).getByRole("button", { name: "Last 7 days" }));
    expect(sdk.trending).toHaveBeenLastCalledWith(expect.objectContaining({ query: expect.objectContaining({ hours: 168 }) }));
    expect(sdk.contentStats.mock.calls.length).toBe(statsCalls);
    await userEvent.click(within(toolbar).getByRole("button", { name: "Relevance > 0.5" }));
    expect(sdk.trending).toHaveBeenLastCalledWith(
      expect.objectContaining({ query: { group_id: null, hours: 168, min_relevance: 0.5, limit: 30 } }),
    );
    expect(window.location.search).toBe("?group=workspace&window=7d&min=0.5");
    expect(within(toolbar).getByRole("button", { name: "Relevance > 0.5" })).toHaveAttribute("aria-pressed", "true");
  });

  it("dismisses from the group's view and can undo", async () => {
    sdk.dismissContentItem.mockImplementation(({ body }: { body: { undo?: boolean } }) => ok({ ...pgRelease, dismissed: !body.undo }));
    renderDiscovery();
    await userEvent.click(screen.getByRole("button", { name: "Dismiss PostgreSQL 18 Released" }));
    expect(sdk.dismissContentItem).toHaveBeenCalledWith(
      expect.objectContaining({ path: { workspace_id: "ws-1", item_id: "it-pg" }, body: { group_id: "grp-1" } }),
    );
    expect(await screen.findByText(/Dismissed “PostgreSQL 18 Released”/)).toBeInTheDocument();
    expect(screen.queryByRole("article", { name: /PostgreSQL 18 Released/ })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Undo dismiss of PostgreSQL 18 Released" }));
    expect(sdk.dismissContentItem).toHaveBeenLastCalledWith(expect.objectContaining({ body: { undo: true, group_id: "grp-1" } }));
    expect(await screen.findByRole("article", { name: /PostgreSQL 18 Released/ })).toBeInTheDocument();
    expect(screen.getByText("Restored “PostgreSQL 18 Released”.")).toBeInTheDocument();
  });

  it("shows a refused dismiss as an alert and keeps the card", async () => {
    sdk.dismissContentItem.mockReturnValue(fail(403, "forbidden", "Forbidden."));
    renderDiscovery();
    await userEvent.click(screen.getByRole("button", { name: "Dismiss PostgreSQL 18 Released" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Editors and up dismiss items/);
    expect(card("PostgreSQL 18 Released")).toBeInTheDocument();
  });

  it("lists the group's pillars with weights, scope and scoring state", () => {
    renderDiscovery();
    const panel = screen.getByRole("region", { name: "Pillars · Tech News" });
    expect(within(panel).getByText("weight 1.4")).toBeInTheDocument();
    expect(within(panel).getByText("weight 0.8")).toBeInTheDocument();
    expect(within(panel).getByText("Workspace")).toBeInTheDocument();
    expect(within(panel).getByText(/Not scoring yet/)).toBeInTheDocument();
    expect(within(panel).getByText(/bounded to 15% movement per run/)).toBeInTheDocument();
  });

  it("adds a pillar for the group, then reloads the rankings", async () => {
    sdk.createPillar.mockReturnValue(ok(pillar("pl-k", { name: "Kubernetes", weight: 1.2 }), 201));
    renderDiscovery();
    const panel = screen.getByRole("region", { name: "Pillars · Tech News" });
    await userEvent.click(within(panel).getByRole("button", { name: "Add pillar" }));
    const form = within(panel).getByRole("form", { name: "Add a pillar" });
    await userEvent.type(within(form).getByLabelText("Pillar name"), "Kubernetes");
    await userEvent.type(within(form).getByLabelText("Keywords"), "k8s, kubernetes");
    await userEvent.clear(within(form).getByLabelText("Weight"));
    await userEvent.type(within(form).getByLabelText("Weight"), "1.2");
    expect(within(form).getByLabelText("Applies to")).toHaveValue("grp-1");
    await userEvent.click(within(form).getByRole("button", { name: "Add pillar" }));
    expect(sdk.createPillar).toHaveBeenCalledWith(
      expect.objectContaining({
        body: { name: "Kubernetes", description: null, keywords: ["k8s", "kubernetes"], weight: 1.2, group_id: "grp-1" },
      }),
    );
    expect(await screen.findByText("Added pillar Kubernetes. Rankings updated.")).toBeInTheDocument();
    expect(sdk.trending).toHaveBeenCalledWith(expect.objectContaining({ query: expect.objectContaining({ group_id: "grp-1" }) }));
    expect(sdk.listPillars).toHaveBeenCalled();
  });

  it("edits a pillar's weight only and shows a refusal from the API", async () => {
    sdk.updatePillar.mockReturnValueOnce(fail(403, "forbidden", "Forbidden."));
    renderDiscovery();
    const panel = screen.getByRole("region", { name: "Pillars · Tech News" });
    await userEvent.click(within(panel).getByRole("button", { name: "Edit pillar Open source" }));
    const form = within(panel).getByRole("form", { name: "Edit pillar Open source" });
    expect(within(form).queryByLabelText("Applies to")).toBeNull();
    await userEvent.clear(within(form).getByLabelText("Weight"));
    await userEvent.type(within(form).getByLabelText("Weight"), "2");
    await userEvent.click(within(form).getByRole("button", { name: "Save pillar" }));
    expect(sdk.updatePillar).toHaveBeenCalledWith(
      expect.objectContaining({ path: { workspace_id: "ws-1", pillar_id: "pl-oss" }, body: { weight: 2 } }),
    );
    expect(await within(form).findByRole("alert")).toHaveTextContent(/edit pillars/);

    await userEvent.clear(within(form).getByLabelText("Weight"));
    await userEvent.type(within(form).getByLabelText("Weight"), "9");
    await userEvent.click(within(form).getByRole("button", { name: "Save pillar" }));
    expect(within(form).getByText("Weight must be between 0 and 4.")).toBeInTheDocument();
    expect(sdk.updatePillar).toHaveBeenCalledTimes(1);
  });

  it("deletes a pillar after confirming", async () => {
    sdk.deletePillar.mockReturnValue(ok(undefined, 204));
    renderDiscovery();
    const panel = screen.getByRole("region", { name: "Pillars · Tech News" });
    await userEvent.click(within(panel).getByRole("button", { name: "Delete pillar Observability" }));
    expect(sdk.deletePillar).not.toHaveBeenCalled();
    await userEvent.click(within(panel).getByRole("button", { name: "Delete pillar" }));
    expect(sdk.deletePillar).toHaveBeenCalledWith(expect.objectContaining({ path: { workspace_id: "ws-1", pillar_id: "pl-new" } }));
    expect(await screen.findByText("Deleted pillar Observability. Rankings updated.")).toBeInTheDocument();
  });

  it("is honest about what it can't show yet", () => {
    renderDiscovery();
    const last7 = screen.getByRole("region", { name: "Last 7 days" });
    expect(within(last7).getByText("266")).toBeInTheDocument();
    expect(within(last7).getByText("Above threshold (0.50)")).toBeInTheDocument();
    expect(within(last7).getAllByText("Not available yet")).toHaveLength(2);
    expect(within(last7).getByText(/drafting \(M5\) and publishing \(M8\)/)).toBeInTheDocument();
    const best = screen.getByRole("region", { name: "Best sources" });
    expect(within(best).getByText("No published posts yet")).toBeInTheDocument();
    expect(within(best).queryByText(/%/)).toBeNull();
  });

  it("explains an empty list by what is missing", () => {
    const { unmount } = renderDiscovery({ items: [], stats: { ...stats, ingested: 0, after_dedupe: 0, above_threshold: 0 } });
    expect(screen.getByText("Nothing ingested yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to Sources" })).toHaveAttribute("href", "/w/acme/sources");
    unmount();

    renderDiscovery({ items: [], pillars: [] });
    expect(screen.getByText("No pillars for Tech News")).toBeInTheDocument();
    cleanup();

    renderDiscovery({ items: [], initialState: { groupId: "grp-1", window: "24h", min: true } });
    expect(screen.getByText("Nothing trending here")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show every score" })).toBeInTheDocument();
  });

  it("gives viewers no dismiss or pillar controls", () => {
    renderDiscovery({ canManage: false });
    expect(screen.queryByRole("button", { name: /^Dismiss/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add pillar" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Edit pillar/ })).toBeNull();
  });

  it("has no axe violations", async () => {
    const { container } = renderDiscovery();
    expect((await axe.run(container)).violations).toEqual([]);
  });
});
