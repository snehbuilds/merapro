// @vitest-environment jsdom
import type { AccountOut, GroupOut } from "@mera/sdk";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ShellProvider } from "@/components/shell/shell-provider";
import type { ContentItem, GroupMember, PostSet, Variant, Version } from "@/lib/api/models";
import { VERSION_CONFLICT_TEXT } from "@/lib/composer";

const router = { replace: vi.fn(), refresh: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router, usePathname: () => "/w/acme/composer" }));

const sdk = {
  createPostSet: vi.fn(),
  getPostSet: vi.fn(),
  archivePostSet: vi.fn(),
  generatePostSet: vi.fn(),
  createVersion: vi.fn(),
  rewriteVariant: vi.fn(),
  regenerateVariant: vi.fn(),
  updateVariant: vi.fn(),
  suggestHashtags: vi.fn(),
  listPlatforms: vi.fn(),
  uploadMedia: vi.fn(),
  mediaFromContentItem: vi.fn(),
  setVariantMedia: vi.fn(),
  requestApproval: vi.fn(),
  trending: vi.fn(),
  contentStats: vi.fn(),
  listPillars: vi.fn(),
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

const { ComposerScreen } = await import("@/components/composer/composer-screen");
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

/* ---- fixtures ---- */

function account(id: string, nickname: string, platform: AccountOut["platform"], over: Partial<AccountOut> = {}): AccountOut {
  return {
    id,
    nickname,
    platform,
    account_type: "personal",
    avatar_url: null,
    connected_at: "2026-11-01T00:00:00Z",
    default_brand_voice_id: null,
    default_posting_times: null,
    display_name: nickname,
    granted_scopes: [],
    group_ids: [],
    handle: `@${id}`,
    health: {},
    last_health_check_at: null,
    platform_account_id: id,
    settings: {},
    status: "active",
    status_reason: null,
    token_expires_at: null,
    ...over,
  };
}

const accounts = [
  account("a-li", "Work LinkedIn", "linkedin_member"),
  account("a-page", "Acme Page", "linkedin_organization", { account_type: "organization" }),
  account("a-xmain", "X @main", "x"),
  account("a-xtech", "X @tech", "x"),
  account("a-fb", "Acme FB", "facebook", { status: "needs_reauth" }),
];

function member(a: AccountOut, position: number): GroupMember {
  return { social_account_id: a.id, platform: a.platform, nickname: a.nickname, handle: a.handle, status: a.status, position, enabled_by_default: true };
}

function group(id: string, name: string, members: GroupMember[]): GroupOut {
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
    member_count: members.length,
    members,
    created_at: "2026-11-01T00:00:00Z",
  };
}

const [li, page, xmain, xtech, fb] = accounts as [AccountOut, AccountOut, AccountOut, AccountOut, AccountOut];
const groups = [
  group("grp-tech", "Tech News", [member(li, 0), member(page, 1), member(xmain, 2), member(xtech, 3), member(fb, 4)]),
  group("grp-corp", "Company Official", [member(page, 0)]),
  group("grp-launch", "Product Launch", [member(page, 0), member(xmain, 1)]),
];

function version(id: string, over: Partial<Version> = {}): Version {
  return {
    id,
    version_no: 1,
    body: "Postgres 18 ships async I/O, and the numbers on read-heavy workloads are worth a look.",
    thread_parts: null,
    hashtags: ["postgres"],
    link_url: "https://www.postgresql.org/about/news/",
    first_comment: null,
    content_hash: "abc",
    confidence_score: 0.84,
    compliance_flags: [],
    validation: [],
    agent_notes: {},
    hashtag_suggestions: [],
    media: [],
    source: "agent",
    revision_instruction: null,
    char_count: 98,
    char_limit: 3000,
    created_at: "2026-11-08T09:00:00Z",
    ...over,
  };
}

function variant(id: string, a: AccountOut, position: number, over: Partial<Variant> = {}): Variant {
  return {
    id,
    social_account_id: a.id,
    nickname: a.nickname,
    handle: a.handle,
    account_type: a.account_type,
    platform: a.platform,
    group_name: "Tech News",
    via_account_group_id: "grp-tech",
    enabled: true,
    state: "drafting",
    voice_name: "Personal",
    brand_voice_id: null,
    position,
    stagger_offset_seconds: position * 240,
    version_count: 1,
    current_version: version(`ver-${id}`),
    guardrails: [
      { guardrail_key: "banned_words", outcome: "pass", score: null, details: { found: [] } },
      { guardrail_key: "pii", outcome: "pass", score: null, details: { found: [] } },
    ],
    ...over,
  };
}

function postSet(over: Partial<PostSet> = {}): PostSet {
  return {
    id: "ps-1",
    title: "Postgres 18 ships async I/O",
    topic_brief: null,
    state: "drafting",
    origin: "manual",
    content: { id: "it-pg", title: "PostgreSQL 18 Released", url: "https://www.postgresql.org/about/news/", source_name: "postgresql.org" },
    composite_hash: null,
    generation_status: "idle",
    generation_error: null,
    last_run_id: "run-1",
    target_account_group_ids: ["grp-tech"],
    target_social_account_ids: [],
    created_at: "2026-11-08T09:00:00Z",
    updated_at: "2026-11-08T09:01:00Z",
    variants: [
      // Deliberately out of order: the list follows `position`.
      variant("v-xmain", xmain, 2, {
        current_version: version("ver-v-xmain", { body: "1/ Postgres 18 landed async I/O.", char_limit: 280, char_count: 40, confidence_score: 0.77 }),
      }),
      variant("v-li", li, 0, {
        current_version: version("ver-v-li", {
          agent_notes: {
            angle: "What it means for read replicas",
            hook_type: "statistic",
            self_critique: "The second paragraph is a little long.",
            voice_name: "Personal",
            voice_source: "account",
            voice_match: 0.88,
          },
          compliance_flags: [
            {
              category: "factual",
              severity: "warning",
              excerpt: "meaningful gains",
              explanation: "The release notes don't quantify the gains.",
              suggested_fix: "Say 'gains on sequential scans' without implying a number.",
            },
          ],
        }),
      }),
      variant("v-page", page, 1, { voice_name: "Acme Corporate", current_version: version("ver-v-page", { body: "Postgres 18 is out.", confidence_score: 0.91 }) }),
      variant("v-xtech", xtech, 3, { current_version: version("ver-v-xtech", { body: "Should we be sizing read replicas differently?", char_limit: 280 }) }),
      variant("v-fb", fb, 4, { enabled: false, state: "skipped", current_version: null, version_count: 0 }),
    ],
    set_guardrails: [{ guardrail_key: "cross_account_duplicate", outcome: "pass", score: 0.41, details: { pairs: 1 } }],
    skipped: [],
    ...over,
  };
}

const item: ContentItem = {
  id: "it-pg",
  title: "PostgreSQL 18 Released",
  url: "https://www.postgresql.org/about/news/",
  author: null,
  language: "en",
  og_image_url: null,
  source_id: "src-pg",
  source_name: "postgresql.org",
  published_at: "2026-11-08T07:00:00Z",
  fetched_at: "2026-11-08T07:00:00Z",
  summary: "Async I/O lands.",
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
  scored_group_id: "grp-tech",
  state: "ready",
  state_reason: null,
};

type ComposerProps = Parameters<typeof ComposerScreen>[0];

function renderComposer(props: Partial<ComposerProps> = {}) {
  return render(
    shell(
      <ComposerScreen
        workspace={ws}
        groups={groups}
        accounts={accounts}
        voices={[]}
        recent={[]}
        postSet={null}
        item={null}
        canManage
        pollMs={20}
        {...props}
      />,
    ),
  );
}

const PLATFORMS = [
  {
    platform: "linkedin_member",
    display_name: "LinkedIn",
    phase: 1,
    requires_media: false,
    max_media: 20,
    media_aspects: [
      { key: "1.91x1", width: 1200, height: 628 },
      { key: "1x1", width: 1080, height: 1080 },
    ],
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  window.sessionStorage.clear();
  sdk.listPlatforms.mockReturnValue(ok({ data: PLATFORMS }));
});
afterEach(cleanup);

/* ---- New post ---- */

describe("Composer: new post", () => {
  it("offers groups with member counts and predicts the deduplicated targets", async () => {
    const u = userEvent.setup();
    renderComposer();
    expect(screen.getByText("New post")).toBeInTheDocument();
    const targets = screen.getByRole("group", { name: "Targets" });
    const tech = within(targets).getByRole("button", { name: /Tech News/ });
    expect(tech).toHaveAttribute("aria-pressed", "false");
    expect(tech).toHaveTextContent("5");
    await u.click(tech);
    expect(tech).toHaveAttribute("aria-pressed", "true");
    // Acme FB needs reauth, so the API would skip it: four, not five.
    expect(screen.getByText(/Drafts one post for each of 4 accounts/)).toBeInTheDocument();
    expect(screen.getByText("Acme Page is also in Company Official and Product Launch — adding those groups still gives it one post.")).toBeInTheDocument();
    await u.click(within(targets).getByRole("button", { name: /Product Launch/ }));
    expect(screen.getByText(/Drafts one post for each of 4 accounts/)).toBeInTheDocument();
  });

  it("says what is missing before calling the API", async () => {
    const u = userEvent.setup();
    renderComposer();
    await u.click(screen.getByRole("button", { name: "Create and draft" }));
    expect(screen.getByText("Pick at least one group or account.")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Topic brief" })).toHaveAccessibleDescription(
      /Start from a content item or write a topic brief\./,
    );
    expect(sdk.createPostSet).not.toHaveBeenCalled();
  });

  it("creates from a prefilled content item, remembers what was skipped and opens the set", async () => {
    const u = userEvent.setup();
    sdk.createPostSet.mockReturnValue(ok(postSet({ id: "ps-new", skipped: [{ social_account_id: "a-fb", reason: "inactive" }] }), 201));
    renderComposer({ item });
    expect(screen.getByText("From Discovery")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /PostgreSQL 18 Released/ })).toHaveAttribute("href", item.url);
    expect(screen.queryByRole("textbox", { name: "Topic brief" })).toBeNull();
    await u.click(within(screen.getByRole("group", { name: "Targets" })).getByRole("button", { name: /Tech News/ }));
    await u.click(screen.getByRole("button", { name: "Create and draft" }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/w/acme/composer?set=ps-new"));
    const call = sdk.createPostSet.mock.calls[0]![0];
    expect(call.body).toEqual({
      content_item_id: "it-pg",
      title: null,
      targets: { account_group_ids: ["grp-tech"], social_account_ids: [] },
      generate: true,
      template_id: null, // P2-M1.4
    });
    expect(call.headers["Idempotency-Key"]).toBeTruthy();
    expect(JSON.parse(window.sessionStorage.getItem("mera:skipped:ps-new")!)).toEqual([{ social_account_id: "a-fb", reason: "inactive" }]);
  });

  it("creates from a topic brief with individually added accounts", async () => {
    const u = userEvent.setup();
    sdk.createPostSet.mockReturnValue(ok(postSet({ id: "ps-2" }), 201));
    renderComposer();
    const add = screen.getByRole("button", { name: /Add an account/ });
    await u.click(add);
    expect(add).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("checkbox", { name: /Acme FB/ })).toBeDisabled();
    await u.click(screen.getByRole("checkbox", { name: /X @tech/ }));
    await u.type(screen.getByRole("textbox", { name: "Topic brief" }), "Why we sized replicas down");
    await u.click(screen.getByRole("button", { name: "Create and draft" }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/w/acme/composer?set=ps-2"));
    expect(sdk.createPostSet.mock.calls[0]![0].body).toMatchObject({
      topic: "Why we sized replicas down",
      targets: { account_group_ids: [], social_account_ids: ["a-xtech"] },
    });
  });

  it("puts the API's target errors next to the picker", async () => {
    const u = userEvent.setup();
    sdk.createPostSet.mockReturnValue(fail(422, "no_targets", "Pick at least one connected, active account.", "targets"));
    renderComposer({ item });
    await u.click(within(screen.getByRole("group", { name: "Targets" })).getByRole("button", { name: /Company Official/ }));
    await u.click(screen.getByRole("button", { name: "Create and draft" }));
    expect(await screen.findByText("Pick at least one connected, active account.")).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Targets" })).toHaveAccessibleDescription("Pick at least one connected, active account.");
    expect(router.push).not.toHaveBeenCalled();
  });

  it("is read-only for viewers, who can still reopen drafts", () => {
    renderComposer({ canManage: false, recent: [postSet()] });
    expect(screen.queryByRole("form", { name: "New post" })).toBeNull();
    expect(screen.getByText("You can read drafts here, not write them.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Postgres 18 ships async I/O" })).toHaveAttribute("href", "/w/acme/composer?set=ps-1");
  });

  it("has no axe violations", async () => {
    const { container } = renderComposer({ item, recent: [postSet()] });
    expect((await axe.run(container)).violations).toEqual([]);
  });
});

/* ---- Editor ---- */

function accountList() {
  return screen.getByRole("list", { name: "Accounts" });
}

describe("Composer: post set editor", () => {
  it("lists accounts in publishing order with offsets, confidence, counts and the off account", () => {
    renderComposer({ postSet: postSet() });
    expect(screen.getByText("Draft · 4 accounts")).toBeInTheDocument();
    const rows = within(accountList()).getAllByRole("button");
    const names = ["Work LinkedIn", "Acme Page", "X @main", "X @tech", "Acme FB"];
    expect(rows.map((r) => names.find((n) => r.textContent!.includes(n)))).toEqual(names);
    expect(rows[0]).toHaveAttribute("aria-current", "true");
    expect(rows[0]).toHaveTextContent("0.84");
    expect(rows[0]).toHaveTextContent("+0");
    expect(rows[0]).toHaveTextContent("98 / 3000");
    expect(rows[2]).toHaveTextContent("+8m");
    expect(rows[4]).toHaveTextContent("Off for this post — drops out of the set without losing its history.");
    const order = screen.getByRole("list", { name: "Publishing order" });
    expect(within(order).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "1Work LinkedIn+0",
      "2Acme Page+4m",
      "3X @main+8m",
      "4X @tech+12m",
    ]);
  });

  it("switches the editor and preview to the selected account", async () => {
    const u = userEvent.setup();
    renderComposer({ postSet: postSet() });
    expect(screen.getByRole("textbox", { name: "Post text for Work LinkedIn" })).toHaveValue(
      "Postgres 18 ships async I/O, and the numbers on read-heavy workloads are worth a look.",
    );
    expect(screen.getByText(/Renders as LinkedIn shows it · 3,000 character limit · 0–2 hashtags conventional/)).toBeInTheDocument();
    await u.click(within(accountList()).getByRole("button", { name: /X @main/ }));
    expect(screen.getByRole("textbox", { name: "Post text for X @main" })).toHaveValue("1/ Postgres 18 landed async I/O.");
    expect(screen.getByText(/Renders as X shows it · 280 character limit · 0–1 hashtag conventional/)).toBeInTheDocument();
    expect(within(screen.getByRole("figure", { name: "X preview" })).getByText("#postgres")).toBeInTheDocument();
  });

  it("shows why the agent wrote it: angle, confidence, voice and the Editor's issues", () => {
    renderComposer({ postSet: postSet() });
    const notes = screen.getByRole("region", { name: "Why this draft" });
    expect(within(notes).getByText("What it means for read replicas")).toBeInTheDocument();
    expect(within(notes).getByText("Statistic")).toBeInTheDocument();
    expect(within(notes).getByText("0.84")).toHaveAttribute("data-slot", "tag");
    expect(within(notes).getByText("Personal · from account · match 0.88")).toBeInTheDocument();
    expect(within(notes).getByText("The second paragraph is a little long.")).toBeInTheDocument();
    expect(within(notes).getByText("meaningful gains")).toBeInTheDocument();
    expect(within(notes).getByText("The release notes don't quantify the gains.")).toBeInTheDocument();
    expect(within(notes).getByText("Say 'gains on sequential scans' without implying a number.")).toBeInTheDocument();
    expect(screen.getByText("Personal · from account", { selector: "[data-slot=tag]" })).toBeInTheDocument();
  });

  it("saves an edit as a new version based on the one edited", async () => {
    const u = userEvent.setup();
    const edited = postSet();
    edited.variants = edited.variants.map((v) =>
      v.id === "v-li" ? { ...v, version_count: 2, current_version: version("ver-v-li-2", { version_no: 2, body: "Shorter.", source: "human" }) } : v,
    );
    sdk.createVersion.mockReturnValue(ok(edited, 201));
    renderComposer({ postSet: postSet() });
    const box = screen.getByRole("textbox", { name: "Post text for Work LinkedIn" });
    await u.clear(box);
    await u.type(box, "Shorter.");
    expect(screen.getByText(/Saving makes version 2/)).toBeInTheDocument();
    // Rewrites wait until the edit is saved or discarded.
    expect(screen.getByRole("button", { name: "Shorter" })).toBeDisabled();
    await u.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sdk.createVersion).toHaveBeenCalledTimes(1));
    expect(sdk.createVersion.mock.calls[0]![0]).toMatchObject({
      path: { workspace_id: "ws-1", set_id: "ps-1", variant_id: "v-li" },
      body: { body: "Shorter.", hashtags: ["postgres"], thread_parts: null, based_on_version_id: "ver-v-li" },
    });
    expect(await screen.findByText(/Version 2 · edited by a person/)).toBeInTheDocument();
  });

  it("says to reload when someone saved in between (409 version_conflict)", async () => {
    const u = userEvent.setup();
    sdk.createVersion.mockReturnValue(fail(409, "version_conflict", "A newer version exists."));
    renderComposer({ postSet: postSet() });
    await u.type(screen.getByRole("textbox", { name: "Post text for Work LinkedIn" }), " More.");
    await u.click(screen.getByRole("button", { name: "Save" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(VERSION_CONFLICT_TEXT);
    expect(within(alert).getByRole("button", { name: "Reload" })).toBeInTheDocument();
  });

  it("shows a guardrail block on human text with the reason", () => {
    const set = postSet();
    set.state = "blocked";
    set.variants = set.variants.map((v) =>
      v.id === "v-li"
        ? {
            ...v,
            state: "blocked",
            guardrails: [{ guardrail_key: "banned_words", outcome: "block", score: null, details: { found: ["game changer"] } }],
          }
        : v,
    );
    renderComposer({ postSet: set });
    expect(within(accountList()).getByRole("button", { name: /Work LinkedIn/ })).toHaveTextContent("Blocked");
    expect(screen.getByText("Contains a banned phrase from this account's voice: “game changer”.")).toBeInTheDocument();
    expect(screen.getByText("1 account blocked")).toBeInTheDocument();
  });

  it("sends a quick-rewrite chip to the Writer and shows drafting progress", async () => {
    const u = userEvent.setup();
    sdk.rewriteVariant.mockReturnValue(ok(postSet({ generation_status: "queued" }), 202));
    sdk.getPostSet.mockReturnValue(ok(postSet()));
    renderComposer({ postSet: postSet() });
    await u.click(screen.getByRole("button", { name: "Shorter" }));
    await waitFor(() => expect(sdk.rewriteVariant).toHaveBeenCalledTimes(1));
    expect(sdk.rewriteVariant.mock.calls[0]![0]).toMatchObject({
      path: { set_id: "ps-1", variant_id: "v-li" },
      body: { instruction: "Make it shorter. Keep the point and the link." },
    });
    expect(screen.getByText(/Queued — drafting starts in a moment…/)).toBeInTheDocument();
    await waitFor(() => expect(sdk.getPostSet).toHaveBeenCalled());
  });

  it("regenerates one account", async () => {
    const u = userEvent.setup();
    sdk.regenerateVariant.mockReturnValue(ok(postSet({ generation_status: "running" }), 202));
    sdk.getPostSet.mockReturnValue(ok(postSet()));
    renderComposer({ postSet: postSet() });
    await u.click(screen.getByRole("button", { name: "Regenerate" }));
    await waitFor(() => expect(sdk.regenerateVariant).toHaveBeenCalledWith(expect.objectContaining({ path: expect.objectContaining({ variant_id: "v-li" }) })));
  });

  it("polls while drafting and stops once the set is idle", async () => {
    sdk.getPostSet
      .mockReturnValueOnce(ok(postSet({ generation_status: "running" })))
      .mockReturnValueOnce(ok(postSet({ generation_status: "idle" })))
      .mockReturnValue(ok(postSet({ generation_status: "idle" })));
    renderComposer({ postSet: postSet({ generation_status: "running" }) });
    expect(screen.getByText(/Drafting…/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Post text for Work LinkedIn" })).toHaveAttribute("readonly");
    await waitFor(() => expect(sdk.getPostSet).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText(/Drafting…/)).toBeNull());
    await act(() => new Promise((r) => setTimeout(r, 120)));
    expect(sdk.getPostSet).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("textbox", { name: "Post text for Work LinkedIn" })).not.toHaveAttribute("readonly");
  });

  it("stops polling when drafting fails and offers a retry", async () => {
    sdk.getPostSet.mockReturnValue(ok(postSet({ generation_status: "failed", generation_error: "Writer timed out." })));
    renderComposer({ postSet: postSet({ generation_status: "queued" }) });
    expect(await screen.findByText(/Writer timed out\./)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try drafting again" })).toBeInTheDocument();
    await act(() => new Promise((r) => setTimeout(r, 100)));
    expect(sdk.getPostSet).toHaveBeenCalledTimes(1);
  });

  it("switches an account off for this post", async () => {
    const u = userEvent.setup();
    const off = postSet();
    off.variants = off.variants.map((v) => (v.id === "v-li" ? { ...v, enabled: false, state: "skipped" } : v));
    sdk.updateVariant.mockReturnValue(ok(off));
    renderComposer({ postSet: postSet() });
    const toggle = screen.getByRole("switch", { name: "Include Work LinkedIn in this post" });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await u.click(toggle);
    await waitFor(() => expect(sdk.updateVariant).toHaveBeenCalledWith(expect.objectContaining({ body: { enabled: false } })));
    expect(await screen.findByText("Work LinkedIn is off for this post. Its history is kept.")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "Include Work LinkedIn in this post" })).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText("3 enabled")).toBeInTheDocument();
  });

  it("switches the voice for one account", async () => {
    const u = userEvent.setup();
    sdk.updateVariant.mockReturnValue(ok(postSet()));
    const voice = {
      id: "voice-tech",
      name: "Technical",
      scope_type: "account_group" as const,
      scope_id: "grp-tech",
      scope_name: "Tech News",
      is_default: true,
      tone: null,
      target_audience: null,
      emoji_policy: "none" as const,
      cta_style: null,
      banned_words: [],
      example_posts: [],
      learned_preferences: [],
      about: null,
      story_bank: [],
      created_at: "2026-11-01T00:00:00Z",
      updated_at: "2026-11-01T00:00:00Z",
    };
    renderComposer({ postSet: postSet(), voices: [voice] });
    await u.click(screen.getByRole("button", { name: "Switch" }));
    await u.selectOptions(screen.getByRole("combobox", { name: "Voice for Work LinkedIn in this post" }), "voice-tech");
    await u.click(screen.getByRole("button", { name: "Use this voice" }));
    await waitFor(() => expect(sdk.updateVariant).toHaveBeenCalledWith(expect.objectContaining({ body: { brand_voice_id: "voice-tech" } })));
  });

  it("suggests hashtags with their evidence and never invents a lift", async () => {
    const u = userEvent.setup();
    sdk.suggestHashtags.mockReturnValue(
      ok({
        data: [
          { tag: "databases", rank: 1, basis: "content_only", sample_size: 0, lift_vs_account_median: null, rationale: "In the text." },
          { tag: "opensource", rank: 2, basis: "platform_convention", sample_size: 0, lift_vs_account_median: null, rationale: "Common here." },
        ],
      }),
    );
    renderComposer({ postSet: postSet() });
    await u.click(screen.getByRole("button", { name: "Suggest" }));
    const table = await screen.findByRole("table", { name: /Hashtag suggestions for Work LinkedIn/ });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Tag", "Basis", "Lift vs median", "Posts", "Add"]);
    const rows = within(table).getAllByRole("row").slice(1);
    expect(within(rows[0]!).getByText("Content only")).toBeInTheDocument();
    expect(within(rows[1]!).getByText("Platform convention")).toBeInTheDocument();
    for (const row of rows) {
      const cells = within(row).getAllByRole("cell");
      expect(cells[2]).toHaveTextContent("—");
      expect(cells[3]).toHaveTextContent("0");
    }
    expect(table.textContent).not.toMatch(/%/);
    expect(screen.getByText(/Below 10 posts there is no usable signal/)).toBeInTheDocument();
    expect(screen.getByText(/no measured history yet/)).toBeInTheDocument();
  });

  it("shows a measured lift as a change against the account's typical post (P3-M1.2)", async () => {
    const u = userEvent.setup();
    sdk.suggestHashtags.mockReturnValue(
      ok({
        data: [
          { tag: "postgres", rank: 1, basis: "history", sample_size: 14, lift_vs_account_median: 1.34, rationale: "Measured." },
          { tag: "sql", rank: 2, basis: "content_only", sample_size: 3, lift_vs_account_median: null, rationale: "Few posts." },
        ],
      }),
    );
    renderComposer({ postSet: postSet() });
    await u.click(screen.getByRole("button", { name: "Suggest" }));
    const table = await screen.findByRole("table", { name: /Hashtag suggestions for Work LinkedIn/ });
    const [measured, few] = within(table).getAllByRole("row").slice(1);
    expect(within(measured!).getAllByRole("cell")[2]).toHaveTextContent("+34%");
    expect(within(measured!).getAllByRole("cell")[3]).toHaveTextContent("14");
    expect(within(few!).getAllByRole("cell")[2]).toHaveTextContent("—");
    expect(within(few!).getAllByRole("cell")[3]).toHaveTextContent("3");
  });

  it("adds and removes hashtags as new versions", async () => {
    const u = userEvent.setup();
    sdk.createVersion.mockReturnValue(ok(postSet(), 201));
    renderComposer({ postSet: postSet() });
    await u.type(screen.getByRole("textbox", { name: "New hashtag" }), "#Open Source!");
    await u.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(sdk.createVersion).toHaveBeenCalledTimes(1));
    expect(sdk.createVersion.mock.calls[0]![0].body).toMatchObject({ hashtags: ["postgres", "opensource"], based_on_version_id: "ver-v-li" });
    await u.click(screen.getByRole("button", { name: "Remove #postgres" }));
    await waitFor(() => expect(sdk.createVersion).toHaveBeenCalledTimes(2));
  });

  it("names both accounts when a cross-account duplicate blocks the set", () => {
    renderComposer({
      postSet: postSet({
        state: "blocked",
        set_guardrails: [
          { guardrail_key: "cross_account_duplicate", outcome: "block", score: 0.93, details: { variant_ids: ["v-xmain", "v-xtech"], platform: "x" } },
        ],
      }),
    });
    expect(screen.getByText("Two accounts read almost the same.")).toBeInTheDocument();
    expect(
      screen.getByText(
        "X @main and X @tech read almost the same (similarity 0.93). Platforms suspend near-identical posts across accounts on X, so neither can go out until one is rewritten.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Two accounts too similar")).toBeInTheDocument();
  });

  it("derives the commit bar honestly; Schedule waits for M8, Send for approval is live", () => {
    renderComposer({ postSet: postSet() });
    expect(screen.getByText("All 4 accounts distinct")).toBeInTheDocument();
    expect(screen.getByText("Guardrails clear")).toBeInTheDocument();
    const schedule = screen.getByRole("button", { name: "Schedule…" });
    expect(schedule).toBeDisabled();
    expect(schedule).toHaveAccessibleDescription("Scheduling arrives with the calendar (M8).");
    const send = screen.getByRole("button", { name: "Send 4 for approval" });
    expect(send).toBeEnabled();
    expect(send).toHaveAccessibleDescription(/Each account is approved on its own, for the exact text saved here/);
    // Media is live (Sprint 7); generating images arrived with P2-M5.
    expect(screen.getByRole("button", { name: "Upload" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Generate" })).toBeEnabled();
  });

  it("uploads an image, then saves it with alt text as a new version on the edited one", async () => {
    const u = userEvent.setup();
    sdk.uploadMedia.mockReturnValue(ok({ id: "asset-1", default_alt_text: null }, 201));
    const saved = postSet();
    saved.variants = saved.variants.map((v) =>
      v.id === "v-li"
        ? {
            ...v,
            version_count: 2,
            current_version: version("ver-v-li-2", {
              version_no: 2,
              source: "human",
              media: [
                { asset_id: "rend-1", master_id: "asset-1", checksum: "a".repeat(64), alt_text: "Our dashboard", aspect: "1.91x1", width: 1200, height: 628 },
              ],
            }),
          }
        : v,
    );
    sdk.setVariantMedia.mockReturnValue(ok(saved));
    renderComposer({ postSet: postSet() });
    const file = new File([new Uint8Array([0xff, 0xd8, 0xff])], "shot.jpg", { type: "image/jpeg" });
    const input = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    await u.upload(input, file);
    await waitFor(() => expect(sdk.uploadMedia).toHaveBeenCalledTimes(1));
    expect(sdk.uploadMedia.mock.calls[0]![0]).toMatchObject({ path: { workspace_id: "ws-1" }, body: { file } });
    expect(await screen.findByText("Cropped on save")).toBeInTheDocument();
    await u.type(screen.getByRole("textbox", { name: "Alt text" }), "Our dashboard");
    await u.click(screen.getByRole("button", { name: "Save images" }));
    await waitFor(() => expect(sdk.setVariantMedia).toHaveBeenCalledTimes(1));
    expect(sdk.setVariantMedia.mock.calls[0]![0]).toMatchObject({
      path: { workspace_id: "ws-1", set_id: "ps-1", variant_id: "v-li" },
      body: { media: [{ asset_id: "asset-1", aspect: "1.91x1", alt_text: "Our dashboard" }], based_on_version_id: "ver-v-li" },
    });
    const img = await screen.findAllByRole("img", { name: "Our dashboard" });
    expect(img[0]).toHaveAttribute("src", "/api/mera/workspaces/ws-1/media/rend-1/content");
  });

  it("explains a refused upload in product words", async () => {
    const u = userEvent.setup();
    sdk.uploadMedia.mockReturnValue(fail(422, "animated_image", "Animated images are not supported yet."));
    renderComposer({ postSet: postSet() });
    const input = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    await u.upload(input, new File(["x"], "a.png", { type: "image/png" }));
    expect(await screen.findByText("Animated images aren't supported yet; upload a still.")).toBeInTheDocument();
    expect(sdk.setVariantMedia).not.toHaveBeenCalled();
  });

  it("says when the checks haven't run rather than claiming they passed", () => {
    const set = postSet({ set_guardrails: [] });
    set.variants = set.variants.map((v) => ({ ...v, guardrails: [] }));
    renderComposer({ postSet: set });
    expect(screen.getByText("Distinctness not checked yet")).toBeInTheDocument();
    expect(screen.getByText("Guardrails not run yet")).toBeInTheDocument();
    expect(screen.queryByText("Guardrails clear")).toBeNull();
  });

  it("shows the accounts left out at creation once", async () => {
    const u = userEvent.setup();
    window.sessionStorage.setItem("mera:skipped:ps-1", JSON.stringify([{ social_account_id: "a-fb", reason: "inactive" }]));
    renderComposer({ postSet: postSet() });
    expect(screen.getByText("Left out: Acme FB (not connected and active).")).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/Left out/)).toBeNull();
    expect(window.sessionStorage.getItem("mera:skipped:ps-1")).toBeNull();
  });

  it("archives the set after confirming", async () => {
    const u = userEvent.setup();
    sdk.archivePostSet.mockReturnValue(Promise.resolve({ data: undefined, response: new Response(null, { status: 204 }) }));
    renderComposer({ postSet: postSet() });
    await u.click(screen.getByRole("button", { name: "Archive" }));
    await u.click(screen.getByRole("button", { name: "Archive post set" }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/w/acme/composer"));
    expect(sdk.archivePostSet).toHaveBeenCalledWith(expect.objectContaining({ path: { workspace_id: "ws-1", set_id: "ps-1" } }));
  });

  it("is read-only for viewers", () => {
    renderComposer({ postSet: postSet(), canManage: false });
    expect(screen.getByRole("textbox", { name: "Post text for Work LinkedIn" })).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: "Shorter" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Archive" })).toBeNull();
    expect(screen.getByRole("switch", { name: "Include Work LinkedIn in this post" })).toBeDisabled();
  });

  it("renders a thread part by part", async () => {
    const u = userEvent.setup();
    const set = postSet();
    set.variants = set.variants.map((v) =>
      v.id === "v-xmain" ? { ...v, current_version: version("ver-t", { body: "1/", thread_parts: ["1/ First.", "2/ Second."], char_limit: 280 }) } : v,
    );
    renderComposer({ postSet: set });
    expect(within(accountList()).getByRole("button", { name: /X @main/ })).toHaveTextContent("Thread · 2");
    await u.click(within(accountList()).getByRole("button", { name: /X @main/ }));
    expect(screen.getByRole("textbox", { name: "Part 1 of 2" })).toHaveValue("1/ First.");
    expect(screen.getByRole("textbox", { name: "Part 2 of 2" })).toHaveValue("2/ Second.");
  });

  it("sends the set for approval and then points to the queue", async () => {
    const u = userEvent.setup();
    sdk.requestApproval.mockReturnValue(ok({ id: "ar-1", variants: [{}, {}, {}, {}] }, 201));
    sdk.getPostSet.mockReturnValue(ok(postSet({ state: "needs_approval" })));
    renderComposer({ postSet: postSet() });
    await u.click(screen.getByRole("button", { name: "Send 4 for approval" }));
    await waitFor(() => expect(sdk.requestApproval).toHaveBeenCalledTimes(1));
    expect(sdk.requestApproval.mock.calls[0]![0]).toMatchObject({ path: { workspace_id: "ws-1", set_id: "ps-1" } });
    expect(await screen.findByText(/Sent 4 accounts for approval/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Waiting in Approvals" })).toHaveAttribute("href", "/w/acme/approvals");
  });

  it("explains why a set is not ready for approval", async () => {
    const u = userEvent.setup();
    sdk.requestApproval.mockReturnValue(fail(409, "not_ready_for_approval", "X @main: contains a banned word or phrase."));
    renderComposer({ postSet: postSet() });
    await u.click(screen.getByRole("button", { name: "Send 4 for approval" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("X @main: contains a banned word or phrase.");
  });

  it("locks editing for an account past approval", () => {
    const set = postSet({ state: "scheduled" });
    set.variants = set.variants.map((v) => (v.id === "v-li" ? { ...v, state: "scheduled" } : v));
    renderComposer({ postSet: set });
    expect(screen.getByRole("textbox", { name: "Post text for Work LinkedIn" })).toHaveAttribute("readonly");
    expect(screen.getByText(/scheduled or published, so its text, hashtags and voice are fixed here/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Shorter" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Switch" })).toBeNull();
    expect(screen.getByRole("switch", { name: "Include Work LinkedIn in this post" })).toBeDisabled();
    // A scheduled set can't be archived (post_set_locked).
    expect(screen.queryByRole("button", { name: "Archive" })).toBeNull();
  });

  it("maps variant_locked from the API", async () => {
    const u = userEvent.setup();
    sdk.createVersion.mockReturnValue(fail(409, "variant_locked", "Locked."));
    renderComposer({ postSet: postSet() });
    await u.type(screen.getByRole("textbox", { name: "Post text for Work LinkedIn" }), "!");
    await u.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This account's post is scheduled or published");
  });

  it("offers only the voices this account may use", async () => {
    const u = userEvent.setup();
    const v = (id: string, name: string, scope_type: "workspace" | "account_group" | "social_account", scope_id: string | null) => ({
      id,
      name,
      scope_type,
      scope_id,
      scope_name: scope_id ?? "Workspace",
      is_default: false,
      tone: null,
      target_audience: null,
      emoji_policy: "none" as const,
      cta_style: null,
      banned_words: [],
      example_posts: [],
      learned_preferences: [],
      about: null,
      story_bank: [],
      created_at: "2026-11-01T00:00:00Z",
      updated_at: "2026-11-01T00:00:00Z",
    });
    renderComposer({
      postSet: postSet(),
      voices: [
        v("w", "Corporate", "workspace", null),
        v("g-own", "Technical", "account_group", "grp-tech"),
        v("g-other", "Launch", "account_group", "grp-launch"),
        v("a-own", "Personal", "social_account", "a-li"),
        v("a-other", "Other", "social_account", "a-xmain"),
      ],
    });
    await u.click(screen.getByRole("button", { name: "Switch" }));
    const options = within(screen.getByRole("combobox", { name: "Voice for Work LinkedIn in this post" })).getAllByRole("option");
    expect(options.map((o) => o.getAttribute("value"))).toEqual(["", "w", "g-own", "a-own"]);
  });

  it("asks to wait when hashtag suggestions are rate limited", async () => {
    const u = userEvent.setup();
    sdk.suggestHashtags.mockReturnValue(
      Promise.resolve({
        error: { error: { code: "rate_limited", message: "Slow down.", field_errors: [] } },
        response: new Response(null, { status: 429, headers: { "Retry-After": "20" } }),
      }),
    );
    renderComposer({ postSet: postSet() });
    await u.click(screen.getByRole("button", { name: "Suggest" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many attempts, try again in 20 seconds.");
  });

  it("explains a set that doesn't resolve", () => {
    renderComposer({ missingSetId: "nope" });
    expect(screen.getByText("That post set isn't here.")).toBeInTheDocument();
    expect(screen.getByRole("form", { name: "New post" })).toBeInTheDocument();
  });

  it("has no axe violations", async () => {
    const set = postSet({
      set_guardrails: [
        { guardrail_key: "cross_account_duplicate", outcome: "warn", score: 0.84, details: { variant_ids: ["v-xmain", "v-xtech"], platform: "x" } },
      ],
    });
    const { container } = renderComposer({ postSet: set, recent: [postSet({ id: "ps-old", title: "Older" })] });
    expect((await axe.run(container)).violations).toEqual([]);
  });
});

/* ---- Discovery: Draft for group ---- */

describe("Discovery: Draft for group", () => {
  const stats = { days: 7, ingested: 266, after_dedupe: 189, above_threshold: 31, filtered: 12, threshold: 0.5, drafted: null, published: null };

  function renderDiscovery(props: Partial<Parameters<typeof DiscoveryScreen>[0]> = {}) {
    return render(
      shell(
        <DiscoveryScreen
          workspace={ws}
          groups={groups}
          initialState={{ groupId: "grp-tech", window: "24h", min: false }}
          items={[item]}
          stats={stats}
          pillars={[]}
          canManage
          {...props}
        />,
      ),
    );
  }

  it("creates a post set for the ranked group and opens it in the Composer", async () => {
    const u = userEvent.setup();
    sdk.createPostSet.mockReturnValue(ok(postSet({ id: "ps-new", skipped: [{ social_account_id: "a-fb", reason: "inactive" }] }), 201));
    renderDiscovery();
    const draft = screen.getByRole("button", { name: "Draft for group" });
    expect(draft).toHaveAccessibleDescription("Draft for group creates a post for each account in Tech News and opens it in the Composer.");
    await u.click(draft);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/w/acme/composer?set=ps-new"));
    expect(sdk.createPostSet.mock.calls[0]![0].body).toEqual({
      content_item_id: "it-pg",
      targets: { account_group_ids: ["grp-tech"] },
      generate: true,
    });
    expect(window.sessionStorage.getItem("mera:skipped:ps-new")).not.toBeNull();
  });

  it("reports a failed create without navigating", async () => {
    const u = userEvent.setup();
    sdk.createPostSet.mockReturnValue(fail(422, "no_targets", "Pick at least one connected, active account.", "targets"));
    renderDiscovery();
    await u.click(screen.getByRole("button", { name: "Draft for group" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Pick at least one connected, active account.");
    expect(router.push).not.toHaveBeenCalled();
  });

  it("links to the Composer with the item in the whole-workspace view", () => {
    renderDiscovery({ initialState: { groupId: null, window: "24h", min: false } });
    const link = screen.getByRole("link", { name: "Draft for group" });
    expect(link).toHaveAttribute("href", "/w/acme/composer?item=it-pg");
    expect(link).toHaveAccessibleDescription("Draft for group opens the Composer with the item, so you pick the groups and accounts there.");
  });

  it("stays disabled for viewers", () => {
    renderDiscovery({ canManage: false });
    const draft = screen.getByRole("button", { name: "Draft for group" });
    expect(draft).toBeDisabled();
    expect(draft).toHaveAccessibleDescription("Editors and up draft posts from these items.");
  });
});
