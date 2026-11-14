import type { AccountOut, NotificationOut, PlatformCapabilities } from "@mera/sdk";
import { describe, expect, it } from "vitest";
import { defaultsPatch } from "@/components/settings/workspace-panels";
import {
  PLATFORM_CATALOGUE,
  accountCapabilityTags,
  connectNotice,
  dashboardAttention,
  filterAccounts,
  initialConnectResult,
  parseConnectReturn,
  stripConnectParams,
} from "@/lib/accounts";
import type { GroupMember } from "@/lib/api/models";
import {
  minutesToSeconds,
  moveMember,
  platformSummary,
  secondsToMinutes,
  staggerBlock,
  staggerBoundsError,
  staggerConflict,
  stepMove,
} from "@/lib/groups";
import { formatDays, formatPostingTimes, formatSettingValue, resolvedRows } from "@/lib/level-settings";
import { scopeSummary } from "@/lib/workspace";

function account(over: Partial<AccountOut> = {}): AccountOut {
  return {
    id: "acc-1",
    platform: "linkedin_member",
    platform_account_id: "urn:li:person:1",
    handle: "@alex",
    display_name: "Alex",
    nickname: "Work LinkedIn",
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
    ...over,
  };
}

function member(id: string, position: number, over: Partial<GroupMember> = {}): GroupMember {
  return {
    social_account_id: id,
    platform: "x",
    nickname: id,
    handle: `@${id}`,
    status: "active",
    position,
    enabled_by_default: true,
    ...over,
  };
}

/* ---- connect return ---- */

describe("connect return", () => {
  it("reads the one-time code, or an error with an optional account", () => {
    expect(parseConnectReturn(new URLSearchParams("connect_code=abc123"))).toEqual({ kind: "code", code: "abc123" });
    expect(parseConnectReturn({ error: "platform_denied" })).toEqual({ kind: "error", code: "platform_denied" });
    expect(parseConnectReturn({ error: "account_already_connected", account: "acc-9" })).toEqual({
      kind: "error",
      code: "account_already_connected",
      accountId: "acc-9",
    });
    expect(parseConnectReturn({ error: "something_new" })).toEqual({ kind: "error", code: "unknown" });
    expect(parseConnectReturn({ connect_code: ["first", "second"] })).toEqual({ kind: "code", code: "first" });
    expect(parseConnectReturn({})).toBeNull();
    expect(parseConnectReturn({ connect_code: "  " })).toBeNull();
  });

  it("prefers the code when both are present", () => {
    expect(parseConnectReturn({ connect_code: "c", error: "platform_error" })?.kind).toBe("code");
  });

  it("strips only the connect-return keys, keeping path, other params and hash", () => {
    expect(stripConnectParams("https://mera.test/w/acme/accounts?connect_code=abc&tab=x#detail")).toBe(
      "https://mera.test/w/acme/accounts?tab=x#detail",
    );
    expect(stripConnectParams("/w/acme/accounts?error=account_already_connected&account=acc-9")).toBe("/w/acme/accounts");
    expect(stripConnectParams("/w/acme/accounts?connected=acc-1")).toBe("/w/acme/accounts");
    expect(stripConnectParams("/w/acme/accounts")).toBe("/w/acme/accounts");
  });

  it("starts as 'finishing' for a code and as the error for an error", () => {
    expect(initialConnectResult(null)).toBeNull();
    expect(initialConnectResult({ kind: "code", code: "c" })).toEqual({ kind: "pending" });
    expect(initialConnectResult({ kind: "error", code: "platform_denied" })).toEqual({ kind: "error", code: "platform_denied" });
  });

  it("names the account once the list has it", () => {
    const list = [account({ id: "acc-1", nickname: "Work LinkedIn" })];
    expect(connectNotice({ kind: "connected", accountId: "acc-1" }, list)).toMatchObject({
      tone: "info",
      title: "Connected Work LinkedIn.",
      accountId: "acc-1",
    });
    expect(connectNotice({ kind: "reconnected", accountId: "acc-1" }, list).title).toBe("Reconnected Work LinkedIn.");
    expect(connectNotice({ kind: "connected", accountId: "acc-2" }, list).title).toBe("Account connected.");
    expect(connectNotice({ kind: "pending" }, list).tone).toBe("default");
  });

  it("explains every error code, using the API's message where it is the specific part", () => {
    expect(connectNotice({ kind: "error", code: "invalid_connect_code" }, []).body).toBe(
      "This connection link has expired or belongs to someone else. Start again.",
    );
    expect(connectNotice({ kind: "error", code: "account_already_connected", message: "Already connected as X @main." }, []).body).toBe(
      "Already connected as X @main.",
    );
    const stagger = connectNotice({ kind: "error", code: "stagger_required", message: "Tech News is set to all at once." }, []);
    expect(stagger.body).toContain("Tech News is set to all at once.");
    expect(stagger.body).toContain("stagger mode");
    expect(connectNotice({ kind: "error", code: "unknown" }, []).tone).toBe("danger");
    for (const code of ["platform_denied", "platform_error", "forbidden", "workspace_archived", "account_mismatch", "account_limit_reached", "account_suspended"] as const) {
      const n = connectNotice({ kind: "error", code }, []);
      expect(n.title.length, code).toBeGreaterThan(0);
      expect(n.body?.length, code).toBeGreaterThan(0);
    }
  });
});

/* ---- platforms, capabilities, filters, dashboard ---- */

describe("accounts", () => {
  it("offers exactly the Phase 1 adapters as connectable", () => {
    expect(PLATFORM_CATALOGUE.filter((p) => p.phase === 1).map((p) => p.platform).sort()).toEqual(
      ["facebook", "instagram", "linkedin_member", "linkedin_organization", "threads", "x"].sort(),
    );
    expect(PLATFORM_CATALOGUE).toHaveLength(8);
  });

  it("derives capability tags from the descriptor: supported, limitations, then partial", () => {
    const caps: PlatformCapabilities = {
      platform: "linkedin_member",
      display_name: "LinkedIn",
      phase: 1,
      max_chars: 3000,
      account_types: ["personal"],
      conventional_hashtags: [0, 3],
      supports_threads: false,
      supports_edit: false,
      supports_hide_comments: false,
      supports_alt_text: true,
      supports_delete: true,
      account_analytics: "counts_only",
    };
    const tags = accountCapabilityTags(caps);
    expect(tags.map((t) => t.label)).toEqual([
      "Alt text",
      "Delete",
      "No threads",
      "No editing",
      "No comment hiding",
      "Counts only, no impressions",
    ]);
    expect(tags.at(-1)?.support).toBe("partial");
    const x = accountCapabilityTags({ ...caps, account_analytics: "paid", prohibits_duplicate_across_accounts: true });
    expect(x.filter((t) => t.support === "partial").map((t) => t.label)).toEqual([
      "Analytics need a paid API tier",
      "Different text per account",
    ]);
  });

  it("filters by attention, platform and group together", () => {
    const list = [
      account({ id: "a", platform: "x", group_ids: ["g1"] }),
      account({ id: "b", platform: "x", status: "needs_reauth", group_ids: [] }),
      account({ id: "c", platform: "bluesky", group_ids: ["g1"], health: { scopes_ok: false } }),
    ];
    const ids = (f: Parameters<typeof filterAccounts>[1]) => filterAccounts(list, f).map((a) => a.id);
    expect(ids({ view: "all" })).toEqual(["a", "b", "c"]);
    expect(ids({ view: "attention" })).toEqual(["b", "c"]);
    expect(ids({ view: "all", platform: "x" })).toEqual(["a", "b"]);
    expect(ids({ view: "all", group: "g1" })).toEqual(["a", "c"]);
    expect(ids({ view: "all", group: "ungrouped" })).toEqual(["b"]);
    expect(ids({ view: "attention", platform: "x" })).toEqual(["b"]);
  });

  it("leads the dashboard with reauth accounts, then unread critical notifications not already covered", () => {
    const reauth = account({ id: "acc-r", status: "needs_reauth" });
    const n = (over: Partial<NotificationOut>): NotificationOut => ({
      id: "n",
      kind: "account_health",
      severity: "critical",
      title: "t",
      body: null,
      social_account_id: null,
      subject_id: null,
      subject_type: null,
      created_at: "2026-11-08T00:00:00Z",
      read_at: null,
      ...over,
    });
    const items = dashboardAttention(
      [account(), reauth],
      [
        n({ id: "dup", social_account_id: "acc-r" }),
        n({ id: "crit", social_account_id: "acc-1" }),
        n({ id: "warn", severity: "warning" }),
        n({ id: "read", read_at: "2026-11-08T01:00:00Z" }),
      ],
    );
    expect(items.map((i) => i.key)).toEqual(["a:acc-r", "n:crit"]);
  });

  it("words a member's scope for Team", () => {
    const groups = [{ id: "g1", name: "Tech News" }];
    const accounts = [{ id: "a1", nickname: "X @main" }];
    expect(scopeSummary({ restricted: false, account_group_ids: [], social_account_ids: [] }, groups, accounts)).toBe("Whole workspace");
    expect(scopeSummary({ restricted: true, account_group_ids: ["g1"], social_account_ids: ["a1", "zz"] }, groups, accounts)).toBe(
      "Tech News (group), X @main, 1 more",
    );
    expect(scopeSummary({ restricted: true, account_group_ids: [], social_account_ids: [] }, groups, accounts)).toBe("Nothing yet");
  });
});

/* ---- groups ---- */

describe("group member order", () => {
  const members = [member("a", 0), member("b", 1), member("c", 2), member("d", 3)];
  const order = (list: GroupMember[] | null) => list?.map((m) => `${m.social_account_id}${m.position}`).join(" ");

  it("moves an item and renumbers 0..n-1, matching the API's remove-then-insert-at-position", () => {
    expect(order(moveMember(members, 0, 2))).toBe("b0 c1 a2 d3");
    expect(order(moveMember(members, 3, 0))).toBe("d0 a1 b2 c3");
    expect(order(moveMember(members, 1, 2))).toBe("a0 c1 b2 d3");
  });

  it("sorts by position before moving, whatever order the list arrived in", () => {
    const shuffled = [member("c", 2), member("a", 0), member("d", 3), member("b", 1)];
    expect(order(moveMember(shuffled, 0, 1))).toBe("b0 a1 c2 d3");
  });

  it("refuses no-op and out-of-range moves", () => {
    expect(moveMember(members, 1, 1)).toBeNull();
    expect(moveMember(members, -1, 0)).toBeNull();
    expect(moveMember(members, 0, 4)).toBeNull();
  });

  it("turns up / down into a move, and nothing past either end", () => {
    expect(stepMove(2, "up", 4)).toEqual({ from: 2, to: 1 });
    expect(stepMove(2, "down", 4)).toEqual({ from: 2, to: 3 });
    expect(stepMove(0, "up", 4)).toBeNull();
    expect(stepMove(3, "down", 4)).toBeNull();
  });

  it("summarises platforms in publish order", () => {
    expect(
      platformSummary([
        member("w", 0, { platform: "linkedin_member" }),
        member("p", 1, { platform: "linkedin_member" }),
        member("x1", 2),
        member("x2", 3),
        member("fb", 4, { platform: "facebook" }),
      ]),
    ).toBe("LinkedIn ×2, X ×2, Facebook Page");
    expect(platformSummary([])).toBe("No accounts yet");
  });
});

describe("stagger guardrail", () => {
  it("blocks 'all at once' with two live accounts on one platform", () => {
    expect(staggerConflict([member("a", 0), member("b", 1)])).toEqual({ platform: "x", label: "X", count: 2 });
    expect(staggerBlock([member("a", 0), member("b", 1)])).toBe(
      "This group has 2 X accounts, so posts must be staggered. Choose a stagger mode other than 'all at once'.",
    );
  });

  it("ignores disconnected accounts and different platforms", () => {
    expect(staggerBlock([member("a", 0), member("b", 1, { status: "disconnected" })])).toBeNull();
    expect(staggerBlock([member("a", 0), member("b", 1, { platform: "bluesky" })])).toBeNull();
    expect(staggerBlock([])).toBeNull();
  });

  it("converts minutes and checks bounds before the API does", () => {
    expect(secondsToMinutes(180)).toBe("3");
    expect(secondsToMinutes(90)).toBe("1.5");
    expect(minutesToSeconds("15")).toBe(900);
    expect(minutesToSeconds("1.5")).toBe(90);
    expect(minutesToSeconds("")).toBeNull();
    expect(minutesToSeconds("-3")).toBeNull();
    expect(staggerBoundsError(180, 900)).toBeNull();
    expect(staggerBoundsError(900, 180)).toMatch(/minimum/);
    expect(staggerBoundsError(0, 361 * 60)).toMatch(/6 hours/);
    expect(staggerBoundsError(null, 60)).toMatch(/both/);
  });
});

/* ---- settings ---- */

describe("settings display", () => {
  it("collapses day runs and groups days that share times", () => {
    expect(formatDays([0, 1, 2, 3, 4])).toBe("Mon–Fri");
    expect(formatDays([0, 2, 4])).toBe("Mon, Wed, Fri");
    expect(formatDays([5, 6])).toBe("Sat, Sun");
    const weekdays = [0, 1, 2, 3, 4].flatMap((day) => [
      { day, time: "14:00" },
      { day, time: "09:00" },
    ]);
    expect(formatPostingTimes(weekdays)).toBe("Mon–Fri 09:00, 14:00");
    expect(formatPostingTimes([...weekdays, { day: 5, time: "10:00" }])).toBe("Mon–Fri 09:00, 14:00 · Sat 10:00");
    expect(formatPostingTimes(null)).toBe("None");
  });

  it("words resolved values and maps the API's levels and rules", () => {
    expect(formatSettingValue("daily_post_cap", 3)).toBe("3 per account per day");
    expect(formatSettingValue("required_approvers", 1)).toBe("Any 1 approver");
    expect(formatSettingValue("required_approvers", 2)).toBe("2 approvers");
    expect(formatSettingValue("autopilot_mode", "manual")).toBe("Manual");
    expect(formatSettingValue("quiet_hours", { start: "22:00", end: "07:00" })).toBe("22:00–07:00");
    expect(formatSettingValue("quiet_hours", null)).toBe("None");
    const rows = resolvedRows([
      { key: "required_approvers", value: 2, source_level: "group", rule: "strictest" },
      { key: "daily_post_cap", value: 5, source_level: "system", rule: "most_specific" },
    ]);
    expect(rows.map((r) => [r.label, r.source, r.rule])).toEqual([
      ["Daily cap", "system", "most_specific"],
      ["Approvers", "group", "strictest_wins"],
    ]);
  });

  it("sends only changed workspace defaults, clearing to null and validating", () => {
    const initial = { cap: "3", approvers: "", quietStart: "", quietEnd: "" };
    expect(defaultsPatch(initial, initial)).toEqual({ settings: {}, errors: {} });
    expect(defaultsPatch(initial, { ...initial, cap: "" }).settings).toEqual({ daily_post_cap: null });
    expect(defaultsPatch(initial, { ...initial, approvers: "2" }).settings).toEqual({ required_approvers: 2 });
    expect(defaultsPatch(initial, { ...initial, approvers: "9" }).errors.approvers).toBeDefined();
    expect(defaultsPatch(initial, { ...initial, quietStart: "22:00", quietEnd: "07:00" }).settings).toEqual({
      quiet_hours: { start: "22:00", end: "07:00" },
    });
    expect(defaultsPatch(initial, { ...initial, quietStart: "22:00" }).errors.quiet).toBeDefined();
  });
});
