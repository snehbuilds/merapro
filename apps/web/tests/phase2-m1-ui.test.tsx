// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ShellProvider } from "@/components/shell/shell-provider";

const router = { replace: vi.fn(), refresh: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router, usePathname: () => "/w/acme/library" }));

const sdk = {
  putHashtag: vi.fn(),
  deleteHashtag: vi.fn(),
  createTemplate: vi.fn(),
  updateTemplate: vi.fn(),
  deleteTemplate: vi.fn(),
  completeConnect: vi.fn(),
  postingSlots: vi.fn(),
  updateGroup: vi.fn(),
  abortAgentRun: vi.fn(),
  retryAgentRun: vi.fn(),
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

const { LibraryScreen } = await import("@/components/library/library-screen");
const { PagePicker } = await import("@/components/accounts/page-picker");
const { PostingTimesPanel } = await import("@/components/groups/posting-times-panel");

function ok<T>(data: T, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) });
}
function fail(status: number, code: string, message = code) {
  return Promise.resolve({ error: { error: { code, message } }, response: new Response(null, { status }) });
}

const ws = { id: "ws-1", slug: "acme", timezone: "Asia/Kolkata", archived: false };
const shellWs = { ...ws, name: "Acme", mark: "AC", meta: "Admin · IST", role: "admin" };
const user = { id: "u1", name: "Alex", email: "a@b.c", initials: "AL", emailVerified: true };
const shell = (c: ReactNode) => (
  <ShellProvider workspace={shellWs} workspaces={[shellWs]} user={user}>
    {c}
  </ShellProvider>
);

const tag = (id: string, t: string, status: string) => ({
  id,
  tag: t,
  status,
  aliases: [],
  first_used_at: null,
  updated_at: "2026-11-10T00:00:00Z",
});
const template = {
  id: "t1",
  name: "Release notes",
  description: null,
  topic: "What shipped this week",
  outline: "Headline, why, link.",
  hashtags: ["data"],
  target_group_ids: ["g1"],
  use_count: 3,
  updated_at: "2026-11-10T00:00:00Z",
};

beforeEach(() => vi.clearAllMocks());
afterEach(cleanup);

describe("Library", () => {
  it("adds a banned hashtag, changes a status and explains bad input", async () => {
    sdk.putHashtag.mockReturnValueOnce(fail(400, "invalid_hashtag")).mockReturnValue(ok(tag("h2", "crypto", "banned")));
    render(
      shell(
        <LibraryScreen
          workspace={ws}
          hashtags={[tag("h1", "data", "preferred")]}
          templates={[template]}
          groups={[{ id: "g1", name: "Tech News" }]}
          canEdit
        />,
      ),
    );
    const form = screen.getByRole("form", { name: "Add a hashtag" });
    await userEvent.type(within(form).getByLabelText("Hashtag"), "two words");
    await userEvent.click(within(form).getByRole("button", { name: "Add" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("one word");
    await userEvent.clear(within(form).getByLabelText("Hashtag"));
    await userEvent.type(within(form).getByLabelText("Hashtag"), "#crypto");
    await userEvent.click(within(form).getByRole("button", { name: "Add" }));
    await waitFor(() =>
      expect(sdk.putHashtag).toHaveBeenLastCalledWith(expect.objectContaining({ body: { tag: "#crypto", status: "banned" } })),
    );
    expect(router.refresh).toHaveBeenCalled();
    await userEvent.selectOptions(screen.getByLabelText("Status of #data"), "banned");
    expect(sdk.putHashtag).toHaveBeenLastCalledWith(expect.objectContaining({ body: { tag: "data", status: "banned" } }));
    expect(screen.getByRole("link", { name: "Start a post" })).toHaveAttribute("href", "/w/acme/composer?template=t1");
    expect(screen.getByText("Targets Tech News")).toBeVisible();
  });

  it("creates a template with groups and shows a taken name", async () => {
    sdk.createTemplate.mockReturnValueOnce(fail(409, "template_name_taken")).mockReturnValue(ok(template, 201));
    render(shell(<LibraryScreen workspace={ws} hashtags={[]} templates={[]} groups={[{ id: "g1", name: "Tech News" }]} canEdit />));
    await userEvent.click(screen.getByRole("button", { name: "New template" }));
    const form = screen.getByRole("form", { name: "New template" });
    await userEvent.type(within(form).getByLabelText("Name"), "Release notes");
    await userEvent.type(within(form).getByLabelText(/^Brief/), "What shipped");
    await userEvent.type(within(form).getByLabelText(/^Hashtags/), "#data #infra");
    await userEvent.click(within(form).getByLabelText("Tech News"));
    await userEvent.click(within(form).getByRole("button", { name: "Create template" }));
    expect(await screen.findByText("A template with that name already exists.")).toBeVisible();
    await userEvent.click(within(form).getByRole("button", { name: "Create template" }));
    await waitFor(() =>
      expect(sdk.createTemplate).toHaveBeenLastCalledWith(
        expect.objectContaining({
          body: expect.objectContaining({ name: "Release notes", hashtags: ["#data", "#infra"], target_group_ids: ["g1"] }),
        }),
      ),
    );
  });

  it("is read-only for viewers and passes axe", async () => {
    const { container } = render(
      shell(<LibraryScreen workspace={ws} hashtags={[tag("h1", "data", "banned")]} templates={[template]} groups={[]} canEdit={false} />),
    );
    expect(screen.queryByRole("form", { name: "Add a hashtag" })).toBeNull();
    expect(screen.queryByRole("button", { name: "New template" })).toBeNull();
    const results = await axe.run(container, { rules: { region: { enabled: false } } });
    expect(results.violations).toEqual([]);
  });
});

describe("Choosing a Page", () => {
  it("redeems the code for the chosen Page only", async () => {
    sdk.completeConnect.mockReturnValue(ok({ outcome: "connected", account_id: "a9", workspace_id: "ws-1" }));
    const onDone = vi.fn();
    render(
      <PagePicker
        code="code-123456"
        choices={[
          { platform_account_id: "p1", handle: "acme_news", display_name: "Acme News", account_type: "page" },
          { platform_account_id: "p2", handle: "acme_jobs", display_name: "Acme Jobs", account_type: "page" },
        ]}
        onDone={onDone}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: "Connect Acme Jobs" }));
    expect(sdk.completeConnect).toHaveBeenCalledWith(
      expect.objectContaining({ body: { connect_code: "code-123456", platform_account_id: "p2" } }),
    );
    await waitFor(() => expect(onDone).toHaveBeenCalledWith({ kind: "connected", accountId: "a9" }));
  });
});

describe("Posting times", () => {
  const group = {
    id: "g1",
    name: "Tech News",
    posting_schedule: [{ day: 0, time: "09:00" }],
    members: [],
  } as unknown as Parameters<typeof PostingTimesPanel>[0]["group"];

  it("shows the next free times and saves a sorted, deduplicated schedule", async () => {
    sdk.postingSlots.mockReturnValue(
      ok({
        data: [
          { social_account_id: "a1", account_nickname: "Work LinkedIn", platform: "linkedin_member", account_group_id: "g1", at: "2026-11-16T03:30:00Z", taken: false, source: "group" },
          { social_account_id: "a2", account_nickname: "X @tech", platform: "x", account_group_id: "g1", at: "2026-11-16T03:30:00Z", taken: true, source: "group" },
        ],
      }),
    );
    sdk.updateGroup.mockReturnValue(ok({}));
    render(<PostingTimesPanel group={group} workspaceId="ws-1" timezone="Asia/Kolkata" canEdit />);
    expect(await screen.findByText(/Work LinkedIn/)).toBeVisible();
    expect(screen.queryByText(/X @tech/)).toBeNull(); // taken slots are not offered
    await userEvent.click(screen.getByRole("button", { name: "Weekdays 09:00" }));
    await userEvent.click(screen.getByRole("button", { name: "Add a time" }));
    await userEvent.click(screen.getByRole("button", { name: "Save times" }));
    await waitFor(() => expect(sdk.updateGroup).toHaveBeenCalled());
    const body = sdk.updateGroup.mock.calls[0]?.[0].body;
    expect(body.posting_schedule).toHaveLength(5); // the added Mon 09:00 duplicates the first
    expect(body.posting_schedule[0]).toEqual({ day: 0, time: "09:00" });
  });

  it("rejects a malformed time before saving", async () => {
    sdk.postingSlots.mockReturnValue(ok({ data: [] }));
    render(<PostingTimesPanel group={group} workspaceId="ws-1" timezone="Asia/Kolkata" canEdit />);
    const input = screen.getByLabelText("Time 1");
    await userEvent.clear(input);
    await userEvent.click(screen.getByRole("button", { name: "Save times" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("24-hour");
    expect(sdk.updateGroup).not.toHaveBeenCalled();
  });
});
