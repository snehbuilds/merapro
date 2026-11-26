// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ShellProvider } from "@/components/shell/shell-provider";

const router = { replace: vi.fn(), refresh: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  usePathname: () => "/w/acme/inbox",
  useSearchParams: () => new URLSearchParams(""),
}));

const sdk = {
  triageComments: vi.fn(),
  draftReply: vi.fn(),
  approveReply: vi.fn(),
  submitReply: vi.fn(),
  editReply: vi.fn(),
  rejectReply: vi.fn(),
  hideComment: vi.fn(),
  deleteComment: vi.fn(),
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

const { InboxScreen } = await import("@/components/inbox/inbox-screen");

function ok<T>(data: T, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) });
}

const ws = { id: "ws-1", slug: "acme", timezone: "UTC", archived: false };
const shellWs = { ...ws, name: "Acme", mark: "AC", meta: "Admin · UTC", role: "admin" };
const user = { id: "u1", name: "Alex", email: "a@b.c", initials: "AL", emailVerified: true };
const shell = (c: ReactNode) => (
  <ShellProvider workspace={shellWs} workspaces={[shellWs]} user={user}>
    {c}
  </ShellProvider>
);
const reply = {
  id: "r1",
  comment_id: "c1",
  social_account_id: "acc-x",
  body: "About two weeks, most of it testing.",
  content_hash: "a".repeat(64),
  state: "pending_approval",
  source: "agent",
  confidence: 0.72,
  error: null,
  platform_reply_id: null,
  created_at: "2026-11-10T10:00:00Z",
  approved_at: null,
  sent_at: null,
};
const comment = {
  id: "c1",
  published_post_id: "pp1",
  social_account_id: "acc-x",
  account_nickname: "Tech X",
  account_handle: "tech",
    account_avatar_url: null,
  account_group_id: null,
  platform: "x",
  author_handle: "ana_dev",
  body: "How long did the migration take? <script>alert(1)</script>",
  permalink: null,
  post_permalink: null,
  post_excerpt: "What moving nightly batch jobs to Postgres 18 changed",
  sentiment: "neutral",
  intent: "question",
  state: "triaged",
  escalation_reason: null,
  is_own: false,
  created_at_platform: "2026-11-10T09:00:00Z",
  created_at: "2026-11-10T09:01:00Z",
  replies: [reply],
  can_hide: true,
  can_delete: true,
};
const summary = { states: { new: 2, escalated: 1, triaged: 4 }, replies_pending_approval: 1, platforms: ["x"] };
const props = {
  workspace: ws,
  comments: [comment],
  summary,
  accounts: [{ id: "acc-x", nickname: "Tech X", platform: "x" }],
  groups: [],
  filters: {},
  canReply: true,
  canApprove: true,
};

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("Inbox (P2-M6)", () => {
  it("shows comments as plain text, the reply account, and approves the exact hash", async () => {
    sdk.approveReply.mockReturnValue(ok({ ...reply, state: "approved" }));
    const { container } = render(shell(<InboxScreen {...props} />));
    expect(screen.getByText("3 need attention")).toBeTruthy();
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getAllByText(/<script>alert\(1\)<\/script>/).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: /Replies as Tech X/ })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Approve and send" }));
    await waitFor(() => expect(sdk.approveReply).toHaveBeenCalled());
    expect(sdk.approveReply.mock.calls[0]?.[0]).toMatchObject({
      path: { workspace_id: "ws-1", reply_id: "r1" },
      body: { content_hash: "a".repeat(64) },
    });
    expect(await screen.findByText("Approved. Sending as Tech X.")).toBeTruthy();
  });

  it("bulk-triages the selected comments", async () => {
    sdk.triageComments.mockReturnValue(ok({ changed: 1 }));
    render(shell(<InboxScreen {...props} />));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select comment from ana_dev" }));
    const bar = screen.getByRole("group", { name: "Bulk triage" });
    await userEvent.click(within(bar).getByRole("button", { name: "Ignore" }));
    await waitFor(() => expect(sdk.triageComments).toHaveBeenCalled());
    expect(sdk.triageComments.mock.calls[0]?.[0].body).toEqual({ comment_ids: ["c1"], state: "ignored" });
  });

  it("asks the agent for a draft when no text is given, and a viewer sees no actions", async () => {
    sdk.draftReply.mockReturnValue(ok(reply, 201));
    render(shell(<InboxScreen {...props} />));
    await userEvent.click(screen.getByRole("button", { name: "Draft with agent" }));
    await waitFor(() => expect(sdk.draftReply).toHaveBeenCalled());
    expect(sdk.draftReply.mock.calls[0]?.[0].body).toEqual({});
    cleanup();
    render(shell(<InboxScreen {...props} canReply={false} canApprove={false} />));
    expect(screen.queryByRole("button", { name: "Approve and send" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Draft with agent" })).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });
});
