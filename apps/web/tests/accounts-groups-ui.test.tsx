// @vitest-environment jsdom
import type { AccountOut, GroupOut } from "@mera/sdk";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ShellProvider } from "@/components/shell/shell-provider";
import type { GroupMember } from "@/lib/api/models";

const router = { replace: vi.fn(), refresh: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router, usePathname: () => "/w/acme/accounts" }));

/* The SDK operations the screens call, as mocks returning the SDK's { data, error, response } shape. */
const sdk = {
  updateGroup: vi.fn(),
  updateGroupMember: vi.fn(),
  groupSettings: vi.fn(),
  accountCapabilities: vi.fn(),
  completeConnect: vi.fn(),
};
vi.mock("@mera/sdk", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@mera/sdk")>()),
  updateGroup: (...a: unknown[]) => sdk.updateGroup(...a),
  updateGroupMember: (...a: unknown[]) => sdk.updateGroupMember(...a),
  groupSettings: (...a: unknown[]) => sdk.groupSettings(...a),
  accountCapabilities: (...a: unknown[]) => sdk.accountCapabilities(...a),
  completeConnect: (...a: unknown[]) => sdk.completeConnect(...a),
}));

// lib/theme reads prefers-color-scheme through the topbar's theme toggle.
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

const { StaggerPanel, staggerFormState } = await import("@/components/groups/stagger-panel");
const { MemberTable } = await import("@/components/groups/member-table");
const { AccountsScreen } = await import("@/components/accounts/accounts-screen");

function ok<T>(data: T, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) });
}
function fail(status: number, code: string, message: string, field?: string) {
  return Promise.resolve({
    error: { error: { code, message, field_errors: field ? [{ field, code, message }] : [] } },
    response: new Response(null, { status }),
  });
}

function member(id: string, position: number, over: Partial<GroupMember> = {}): GroupMember {
  return { social_account_id: id, platform: "x", nickname: id, handle: `@${id}`, status: "active", position, enabled_by_default: true, ...over };
}

function group(over: Partial<GroupOut> = {}): GroupOut {
  return {
    id: "grp-1",
    name: "Tech News",
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
    member_count: 2,
    members: [member("x-main", 0), member("x-tech", 1)],
    created_at: "2026-11-01T00:00:00Z",
    ...over,
  };
}

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("StaggerPanel and stagger_required", () => {
  it("predicts the block from the members and prefers the API's message once it has one", () => {
    expect(staggerFormState(group(), null)).toEqual({
      blockedReason: "This group has 2 X accounts, so posts must be staggered. Choose a stagger mode other than 'all at once'.",
      noneEnabled: false,
    });
    expect(staggerFormState(group({ members: [member("a", 0)] }), null)).toEqual({ blockedReason: null, noneEnabled: true });
    expect(staggerFormState(group({ members: [member("a", 0)] }), "API says no").blockedReason).toBe("API says no");
  });

  it("disables “All at once” and gives the reason next to the control", async () => {
    const { container } = render(<StaggerPanel group={group()} workspaceId="ws-1" canEdit />);
    const select = screen.getByLabelText("Stagger mode");
    const none = within(select).getByRole("option", { name: "All at once (blocked)" });
    expect(none).toBeDisabled();
    expect(select).toHaveAccessibleDescription(/2 X accounts, so posts must be staggered/);
    expect((await axe.run(container)).violations).toEqual([]);
  });

  it("shows the API's stagger_required message as the control's error and keeps a staggered mode", async () => {
    // Membership changed elsewhere: the client thought "all at once" was fine.
    sdk.updateGroup.mockReturnValue(
      fail(422, "stagger_required", "This group has 2 Bluesky accounts, so posts must be staggered.", "stagger_mode"),
    );
    render(<StaggerPanel group={group({ members: [member("a", 0)], stagger_mode: "fixed" })} workspaceId="ws-1" canEdit />);
    const select = screen.getByLabelText("Stagger mode");
    await userEvent.selectOptions(select, "none");
    await userEvent.click(screen.getByRole("button", { name: "Save publishing" }));

    expect(sdk.updateGroup).toHaveBeenCalledWith(
      expect.objectContaining({ path: { workspace_id: "ws-1", group_id: "grp-1" }, body: { stagger_mode: "none" } }),
    );
    expect(await screen.findByText("This group has 2 Bluesky accounts, so posts must be staggered.")).toBeInTheDocument();
    expect(select).toHaveAttribute("aria-invalid", "true");
    expect(select).toHaveValue("fixed");
    expect(within(select).getByRole("option", { name: "All at once (blocked)" })).toBeDisabled();
    expect(router.refresh).not.toHaveBeenCalled();
  });

  it("is read-only without edit rights", () => {
    render(<StaggerPanel group={group()} workspaceId="ws-1" canEdit={false} />);
    expect(screen.getByLabelText("Stagger mode")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Save publishing" })).toBeNull();
  });
});

describe("MemberTable reorder", () => {
  const three = group({ members: [member("a", 0), member("b", 1, { platform: "bluesky" }), member("c", 2, { platform: "mastodon" })], member_count: 3 });

  it("moves with the keyboard path and sends the new position", async () => {
    sdk.updateGroupMember.mockImplementation(() =>
      ok({ ...three, members: [member("b", 0, { platform: "bluesky" }), member("a", 1), member("c", 2, { platform: "mastodon" })] }),
    );
    render(<MemberTable group={three} accounts={[]} workspaceId="ws-1" canEdit />);
    expect(screen.getByRole("button", { name: "Move a up" })).toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: "Move b up" }));
    expect(sdk.updateGroupMember).toHaveBeenCalledWith(
      expect.objectContaining({ path: { workspace_id: "ws-1", group_id: "grp-1", account_id: "b" }, body: { position: 0 } }),
    );
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell")[1]!.textContent)).toEqual(["Bluesky: b@b", "X: a@a", "Mastodon: c@c"]);
    expect(screen.getByText("b moved to position 1 of 3.")).toBeInTheDocument();
    await waitFor(() => expect(router.refresh).toHaveBeenCalled());
  });

  it("puts the order back when the API refuses", async () => {
    sdk.updateGroupMember.mockReturnValue(fail(409, "workspace_archived", "Archived."));
    render(<MemberTable group={three} accounts={[]} workspaceId="ws-1" canEdit />);
    await userEvent.click(screen.getByRole("button", { name: "Move a down" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This workspace is archived");
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell")[1]!.textContent)).toEqual(["X: a@a", "Bluesky: b@b", "Mastodon: c@c"]);
  });

  it("offers no reorder controls to read-only viewers", () => {
    render(<MemberTable group={three} accounts={[]} workspaceId="ws-1" canEdit={false} />);
    expect(screen.queryByRole("button", { name: /Move/ })).toBeNull();
    expect(screen.getByRole("switch", { name: "a on by default" })).toBeDisabled();
  });
});

describe("AccountsScreen connect return", () => {
  const ws = { id: "ws-1", slug: "acme", name: "Acme", timezone: "UTC", archived: false };
  const shellWs = { ...ws, mark: "AC", meta: "Owner · UTC", role: "owner" };
  const user = { id: "u1", name: "Alex", email: "a@b.c", initials: "AL", emailVerified: true };
  const acct: AccountOut = {
    id: "acc-1",
    platform: "x",
    platform_account_id: "1",
    handle: "@acme",
    display_name: null,
    nickname: "X @main",
    avatar_url: null,
    account_type: "personal",
    status: "active",
    status_reason: null,
    granted_scopes: [],
    token_expires_at: null,
    last_health_check_at: null,
    health: {},
    default_posting_times: null,
    default_brand_voice_id: null,
    settings: {},
    group_ids: [],
    connected_at: null,
  };

  function renderScreen(connectReturn: Parameters<typeof AccountsScreen>[0]["connectReturn"]) {
    sdk.accountCapabilities.mockReturnValue(new Promise(() => {}));
    return render(
      <ShellProvider workspace={shellWs} workspaces={[shellWs]} user={user}>
        <AccountsScreen workspace={ws} accounts={[acct]} groups={[]} canManage connectReturn={connectReturn} />
      </ShellProvider>,
    );
  }

  it("strips the code from the URL before redeeming it, then reports the outcome", async () => {
    window.history.replaceState(null, "", "/w/acme/accounts?connect_code=one-time&x=1");
    let urlAtCall = "";
    sdk.completeConnect.mockImplementation(() => {
      urlAtCall = window.location.pathname + window.location.search;
      return ok({ outcome: "connected", account_id: "acc-1", workspace_id: "ws-1" });
    });
    renderScreen({ kind: "code", code: "one-time" });
    expect(screen.getByText("Finishing the connection…")).toBeInTheDocument();
    // Redemption is the page's first API call: the detail panel's capability fetch waits for it.
    expect(sdk.accountCapabilities).not.toHaveBeenCalled();

    expect(await screen.findByText("Connected X @main.")).toBeInTheDocument();
    expect(urlAtCall).toBe("/w/acme/accounts?x=1");
    expect(sdk.completeConnect).toHaveBeenCalledTimes(1);
    expect(sdk.completeConnect).toHaveBeenCalledWith(
      expect.objectContaining({ body: { connect_code: "one-time" } }),
    );
    // The router itself is moved to the clean URL (a bare refresh could restore the code).
    expect(router.replace).toHaveBeenCalledWith("/w/acme/accounts?x=1", { scroll: false });
  });

  it("shows an expired or foreign code as a plain explanation", async () => {
    window.history.replaceState(null, "", "/w/acme/accounts?connect_code=stale");
    sdk.completeConnect.mockReturnValue(fail(404, "invalid_connect_code", "Invalid code."));
    renderScreen({ kind: "code", code: "stale" });
    expect(await screen.findByText(/expired or belongs to someone else/)).toBeInTheDocument();
    expect(window.location.search).toBe("");
  });

  it("shows a redirect error without calling the API, and strips it", async () => {
    window.history.replaceState(null, "", "/w/acme/accounts?error=platform_denied");
    renderScreen({ kind: "error", code: "platform_denied" });
    expect(screen.getByText("Nothing was connected.")).toBeInTheDocument();
    await act(async () => {});
    expect(window.location.search).toBe("");
    expect(sdk.completeConnect).not.toHaveBeenCalled();
  });
});
