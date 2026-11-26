// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ShellProvider } from "@/components/shell/shell-provider";

const router = { replace: vi.fn(), refresh: vi.fn(), push: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router, usePathname: () => "/w/acme/calendar" }));

const sdk = {
  calendarMove: vi.fn(),
  publishNow: vi.fn(),
  cancelScheduled: vi.fn(),
  retry: vi.fn(),
  resolveDeadLetter: vi.fn(),
  mintPairingCode: vi.fn(),
  setTelegram: vi.fn(),
  testTelegram: vi.fn(),
  revokeBinding: vi.fn(),
  killSwitch: vi.fn(),
  timeseries: vi.fn(),
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

const { CalendarScreen } = await import("@/components/calendar/calendar-screen");
const { ChannelsScreen } = await import("@/components/channels/channels-screen");
const { AutomationScreen } = await import("@/components/automation/automation-screen");
const { AnalyticsScreen } = await import("@/components/analytics/analytics-screen");

function ok<T>(data: T, status = 200) {
  return Promise.resolve({ data, response: new Response(null, { status }) });
}

const ws = { id: "ws-1", slug: "acme", timezone: "Asia/Kolkata", archived: false };
const shellWs = { ...ws, name: "Acme", mark: "AC", meta: "Admin · IST", role: "admin" };
const user = { id: "u1", name: "Alex", email: "a@b.c", initials: "AL", emailVerified: true };
const shell = (c: ReactNode) => (
  <ShellProvider workspace={shellWs} workspaces={[shellWs]} user={user}>
    {c}
  </ShellProvider>
);

function slot(id: string, at: string, over: Record<string, unknown> = {}) {
  return {
    id,
    post_set_id: "ps1",
    post_set_title: "Postgres 18 async I/O",
    post_variant_id: `v-${id}`,
    post_version_id: `ver-${id}`,
    social_account_id: `a-${id}`,
    account_nickname: `Work ${id}`,
    account_handle: `work_${id}`,
    account_avatar_url: null,
    platform: "linkedin_member",
    account_group_id: "g1",
    group_name: "Tech News",
    group_color: null,
    scheduled_for: at,
    stagger_offset_seconds: 240,
    state: "queued",
    attempt_count: 0,
    max_attempts: 5,
    next_attempt_at: at,
    last_error: null,
    moved_by: [],
    excerpt: "Postgres 18 ships async I/O.",
    permalink: null,
    published_at: null,
    ...over,
  };
}

class FakeSource {
  onmessage: unknown = null;
  addEventListener() {}
  close() {}
}

beforeEach(() => {
  Object.values(sdk).forEach((f) => f.mockReset());
  router.refresh.mockReset();
  (globalThis as { EventSource?: unknown }).EventSource = FakeSource;
});
afterEach(cleanup);

async function noSeriousAxe(container: HTMLElement) {
  const r = await axe.run(container, { rules: { "color-contrast": { enabled: false }, region: { enabled: false } } });
  expect(r.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);
}

describe("Calendar", () => {
  it("lays a week out in the workspace zone and reschedules from the keyboard path", async () => {
    // 03:30Z is 09:00 in Kolkata on the 10th; 20:00Z is 01:30 on the 11th.
    const items = [slot("1", "2026-11-10T03:30:00Z"), slot("2", "2026-11-10T20:00:00Z", { state: "dead_lettered", last_error: "Meta API returned 500" })];
    sdk.calendarMove.mockReturnValue(ok({ ...items[0], scheduled_for: "2026-11-12T05:30:00Z", moved_by: ["quiet hours"] }));
    const { container } = render(shell(<CalendarScreen workspace={ws} week="2026-11-09" items={items as never} deadLetters={[]} groups={[{ id: "g1", name: "Tech News" }]} groupId={null} />));
    const tue = screen.getByRole("listitem", { name: /Tue 10 Nov/ });
    expect(tue.textContent).toContain("09:00");
    expect(screen.getByRole("listitem", { name: /Wed 11 Nov/ }).textContent).toContain("01:30");
    expect(screen.getByText("Failed")).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: /09:00 Work 1/ }));
    const date = screen.getByLabelText("Date");
    await userEvent.clear(date);
    await userEvent.type(date, "2026-11-12");
    await userEvent.click(screen.getByRole("button", { name: "Reschedule" }));
    await waitFor(() => expect(sdk.calendarMove).toHaveBeenCalled());
    const body = sdk.calendarMove.mock.calls[0]?.[0].body;
    expect(body.scheduled_for).toBe("2026-11-12T03:30:00.000Z"); // 09:00 IST, kept
    await screen.findByText(/moved for quiet hours/);
    await noSeriousAxe(container);
  });
});

describe("Approval Channels", () => {
  it("mints a pairing code and shows the command", async () => {
    sdk.mintPairingCode.mockReturnValue(
      ok({ code: "MERA-ABCD-EFGH", command: "/pair MERA-ABCD-EFGH", deep_link: "https://t.me/mera_bot?start=MERA-ABCD-EFGH", expires_at: "2026-11-10T05:00:00Z" }, 201),
    );
    const status = { telegram: { configured: true, sandbox: false, bot_username: "mera_bot", mode: "webhook", enabled: true, last_test_at: null, last_test_result: null }, whatsapp: { available: true, configured: false, display_number: null, enabled: false, last_test_at: null, last_test_result: null, usage: null } };
    const { container } = render(shell(<ChannelsScreen workspace={ws} status={status} bindings={[]} canManage />));
    await userEvent.click(screen.getByRole("button", { name: "Get a pairing code" }));
    expect(await screen.findByLabelText("Pairing code")).toHaveProperty("textContent", "MERA-ABCD-EFGH");
    expect(screen.getByText("/pair MERA-ABCD-EFGH")).toBeTruthy();
    expect(screen.getByText("Not set up")).toBeTruthy();
    await noSeriousAxe(container);
  });
});

describe("Automation", () => {
  it("is Manual only and stops a group", async () => {
    sdk.killSwitch.mockReturnValue(ok({ scope_type: "account_group", scope_id: "g1", autopilot_mode: "manual", kill_switch_engaged: true }));
    render(shell(<AutomationScreen workspace={{ ...ws, role: "admin" }} states={[]} groups={[{ id: "g1", name: "Tech News" }]} accounts={[]} />));
    expect((screen.getByRole("radio", { name: "Full Autopilot" }) as HTMLInputElement).disabled).toBe(true);
    const stops = screen.getAllByRole("button", { name: "Stop" });
    await userEvent.click(stops[1]!);
    await waitFor(() => expect(sdk.killSwitch).toHaveBeenCalled());
    expect(sdk.killSwitch.mock.calls[0]?.[0].body).toMatchObject({ scope_type: "account_group", scope_id: "g1", engaged: true });
  });
});

describe("Analytics", () => {
  it("renders an unreported metric as Not reported, never zero", () => {
    render(
      shell(
        <AnalyticsScreen
          workspace={ws}
          filters={{ days: 30, groupId: null, accountId: null, from: "2026-10-10T00:00:00Z" }}
          groups={[]}
          accounts={[]}
          overview={{ totals: { posts: 4, impressions: null, reach: null, likes: 12, comments: 3, shares: 0, engagement_rate: null } }}
          series={{ metric: "engagement", points: [{ date: "2026-11-09", value: 4 }, { date: "2026-11-10", value: null }] }}
          accountsCmp={[{ social_account_id: "a", nickname: "Work", platform: "linkedin_member", posts: 1, median_engagement: 9, index_vs_baseline: null }]}
          groupsCmp={[]}
          turnaround={{ count: 2, median_minutes: 42, p90_minutes: 90, undecided: 0, by_group: [] }}
          top={[]}
        />,
      ),
    );
    const tiles = screen.getByRole("list", { name: "Headline figures" });
    expect(tiles.textContent).toContain("Not reported");
    expect(tiles.textContent).toContain("Shares0"); // a reported zero stays zero
    expect(screen.getByText("No baseline yet")).toBeTruthy();
    expect(screen.getByText("42 min")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Export CSV" }).getAttribute("href")).toContain("/analytics/export.csv?from=");
  });
});
