// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ShellProvider } from "@/components/shell/shell-provider";
import type { ApprovalRequest, ApprovalVariant } from "@/lib/api/models";

const router = { replace: vi.fn(), refresh: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router, usePathname: () => "/w/acme/approvals" }));

const sdk = { decide: vi.fn(), cancelApprovalRequest: vi.fn() };
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

const { ApprovalsScreen } = await import("@/components/approvals/approvals-screen");

function ok<T>(data: T, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) });
}
function fail(status: number, code: string, message: string) {
  return Promise.resolve({ error: { error: { code, message, field_errors: [] } }, response: new Response(null, { status }) });
}

const ws = { id: "ws-1", slug: "acme", timezone: "UTC", archived: false };
const shellWs = { ...ws, name: "Acme", mark: "AC", meta: "Approver · UTC", role: "approver" };
const user = { id: "u1", name: "Alex", email: "a@b.c", initials: "AL", emailVerified: true };

function shell(children: ReactNode) {
  return (
    <ShellProvider workspace={shellWs} workspaces={[shellWs]} user={user}>
      {children}
    </ShellProvider>
  );
}

function variant(id: string, nickname: string, platform: string, over: Partial<ApprovalVariant> = {}): ApprovalVariant {
  return {
    post_variant_id: id,
    social_account_id: `a-${id}`,
    platform,
    nickname,
    handle: `@${nickname.toLowerCase().replace(/\s+/g, "")}`,
    avatar_url: null,
    state: "pending",
    required: 1,
    quorum: "any_of",
    approve_count: 0,
    reject_count: 0,
    decided_at: null,
    post_version_id: `ver-${id}`,
    version_no: 1,
    content_hash: id.padEnd(64, "a").slice(0, 64),
    is_current: true,
    body: `Post for ${nickname}.`,
    thread_parts: null,
    hashtags: ["postgres"],
    link_url: null,
    first_comment: null,
    media: [],
    confidence_score: 0.82,
    compliance_flags: [],
    my_vote: null,
    can_decide: true,
    ...over,
  };
}

function request(over: Partial<ApprovalRequest> = {}): ApprovalRequest {
  return {
    id: "ar-1",
    post_set_id: "ps-1",
    title: "Postgres 18 ships async I/O",
    state: "pending",
    content_hash: "c".repeat(64),
    created_at: "2026-11-09T08:00:00Z",
    expires_at: "2026-11-09T20:00:00Z",
    remind_at: null,
    approved_at: null,
    decided_at: null,
    requested_by: "Priya",
    variants: [
      variant("v-li", "Work LinkedIn", "linkedin_member"),
      variant("v-fb", "Acme Page", "facebook", { required: 2, quorum: "all_of", approve_count: 1 }),
      variant("v-x", "X main", "x", { can_decide: false }),
    ],
    decisions: [],
    ...over,
  };
}

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("Approval Queue", () => {
  it("shows each account's exact text and approves one account with the hash shown", async () => {
    const u = userEvent.setup();
    const after = request();
    after.variants = after.variants.map((v) => (v.post_variant_id === "v-li" ? { ...v, state: "approved", approve_count: 1, can_decide: false } : v));
    sdk.decide.mockReturnValue(ok(after));
    render(shell(<ApprovalsScreen workspace={ws} requests={[request()]} />));
    expect(screen.getByText("3 accounts across 1 post")).toBeInTheDocument();
    expect(screen.getByText("Post for Work LinkedIn.")).toBeInTheDocument();
    expect(screen.getByText("1 of 2 approvals (every named approver)")).toBeInTheDocument();
    // Not this approver's account: no buttons for it.
    expect(screen.queryByRole("button", { name: "Approve X main" })).toBeNull();
    await u.click(screen.getByRole("button", { name: "Approve Work LinkedIn" }));
    await waitFor(() => expect(sdk.decide).toHaveBeenCalledTimes(1));
    expect(sdk.decide.mock.calls[0]![0]).toMatchObject({
      path: { workspace_id: "ws-1", request_id: "ar-1" },
      body: { action: "approve", variant_scope: ["v-li"], content_hashes: { "v-li": request().variants[0]!.content_hash } },
    });
    expect(await screen.findByText("Approved: Work LinkedIn.")).toBeInTheDocument();
    expect(router.refresh).toHaveBeenCalled();
  });

  it("approves every account this approver may decide in one go", async () => {
    const u = userEvent.setup();
    sdk.decide.mockReturnValue(ok(request({ state: "pending" })));
    render(shell(<ApprovalsScreen workspace={ws} requests={[request()]} />));
    await u.click(screen.getByRole("button", { name: "Approve all 2" }));
    await waitFor(() => expect(sdk.decide).toHaveBeenCalledTimes(1));
    const body = sdk.decide.mock.calls[0]![0].body;
    expect(body.variant_scope).toEqual(["v-li", "v-fb"]);
    expect(Object.keys(body.content_hashes)).toEqual(["v-li", "v-fb"]);
  });

  it("rejects with a reason, and explains a changed post", async () => {
    const u = userEvent.setup();
    sdk.decide.mockReturnValue(fail(422, "approval_content_mismatch", "changed"));
    render(shell(<ApprovalsScreen workspace={ws} requests={[request()]} />));
    const li = screen.getByRole("article", { name: /Work LinkedIn/ });
    await u.click(within(li).getByRole("button", { name: "Reject…" }));
    await u.type(within(li).getByRole("textbox", { name: "Why reject Work LinkedIn?" }), "Too salesy");
    await u.click(within(li).getByRole("button", { name: "Reject Work LinkedIn" }));
    await waitFor(() => expect(sdk.decide).toHaveBeenCalledTimes(1));
    expect(sdk.decide.mock.calls[0]![0].body).toMatchObject({ action: "reject", reason: "Too salesy", variant_scope: ["v-li"] });
    expect(await screen.findByRole("alert")).toHaveTextContent("This post changed since you opened it");
  });

  it("lists decided requests separately and has no axe violations", async () => {
    const decided = request({ id: "ar-2", state: "partially_approved", decided_at: "2026-11-09T09:00:00Z" });
    decided.variants = decided.variants.map((v, i) => ({ ...v, state: i === 0 ? "approved" : "rejected", can_decide: false }));
    const { container } = render(shell(<ApprovalsScreen workspace={ws} requests={[request(), decided]} />));
    const recent = screen.getByRole("region", { name: "Recently decided" });
    expect(within(recent).getByText("Partially approved")).toBeInTheDocument();
    expect(within(recent).getByText("1 of 3 approved")).toBeInTheDocument();
    const results = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations.map((v) => v.id)).toEqual([]);
  });

  it("says when nothing is waiting", () => {
    render(shell(<ApprovalsScreen workspace={ws} requests={[]} />));
    expect(screen.getByText("Nothing waiting for approval")).toBeInTheDocument();
  });
});
