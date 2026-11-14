import { describe, expect, it } from "vitest";
import {
  ACCOUNT_STATES,
  APPROVAL_VARIANT_STATES,
  POST_SET_STATES,
  POST_VARIANT_STATES,
  SCHEDULED_POST_STATES,
  SOURCE_STATES,
  STATUS_ENUMS,
  STATUS_TABLE,
  accountNeedsAttention,
  describeAccountHealth,
  describeStatus,
  type StatusKind,
} from "@/lib/status";

const TONES = ["ok", "warn", "danger", "info", "neutral"];
const SHAPES = ["full", "ring", "half", "diamond"];

/* The enums as written in docs/03 / the backend models. If the backend adds a
   value, add it here and to lib/status.ts; the `satisfies` there fails the build. */
const EXPECTED: Record<StatusKind, string[]> = {
  post_variant: [
    "drafting", "needs_approval", "approved", "scheduled", "publishing",
    "published", "failed", "rejected", "blocked", "skipped",
  ],
  approval_variant: ["pending", "approved", "rejected", "superseded", "expired", "cancelled"],
  scheduled_post: ["queued", "publishing", "published", "failed", "dead_lettered", "cancelled"],
  post_set: [
    "drafting", "needs_approval", "approved", "partially_approved", "scheduled", "published",
    "partially_published", "rejected", "blocked", "archived",
  ],
  account: ["active", "needs_reauth", "suspended", "disconnected"],
  source: ["active", "failing", "paused"],
};

describe("status mapping table", () => {
  it("lists exactly the backend enum values for every kind", () => {
    expect([...POST_VARIANT_STATES]).toEqual(EXPECTED.post_variant);
    expect([...APPROVAL_VARIANT_STATES]).toEqual(EXPECTED.approval_variant);
    expect([...SCHEDULED_POST_STATES]).toEqual(EXPECTED.scheduled_post);
    expect([...POST_SET_STATES]).toEqual(EXPECTED.post_set);
    expect([...ACCOUNT_STATES]).toEqual(EXPECTED.account);
    expect([...SOURCE_STATES]).toEqual(EXPECTED.source);
  });

  it.each(Object.keys(EXPECTED) as StatusKind[])("maps every %s state to a label, tone and shape", (kind) => {
    const table = STATUS_TABLE[kind] as Record<string, { label: string; tone: string; shape: string }>;
    expect(Object.keys(table).sort()).toEqual([...EXPECTED[kind]].sort());
    for (const state of STATUS_ENUMS[kind]) {
      const spec = table[state];
      expect(spec, `${kind}.${state}`).toBeDefined();
      expect(spec!.label.trim().length).toBeGreaterThan(0);
      expect(TONES).toContain(spec!.tone);
      expect(SHAPES).toContain(spec!.shape);
    }
  });

  it("never renders partial states as danger (Partial is normal)", () => {
    expect(STATUS_TABLE.post_set.partially_approved.tone).not.toBe("danger");
    expect(STATUS_TABLE.post_set.partially_published.tone).not.toBe("danger");
  });

  it("gives each kind distinct labels, so no two states read the same", () => {
    for (const kind of Object.keys(EXPECTED) as StatusKind[]) {
      const labels = Object.values(STATUS_TABLE[kind]).map((s) => s.label);
      expect(new Set(labels).size).toBe(labels.length);
    }
  });

  it("derives display labels from inputs, never stored", () => {
    expect(describeStatus("post_variant", "needs_approval", { pinRequired: true }).label).toBe("Pending · PIN");
    expect(describeStatus("approval_variant", "pending", { approvalsRemaining: 1 }).label).toBe("Needs 1 more");
    expect(describeStatus("scheduled_post", "failed", { attempt: 2, maxAttempts: 5 }).label).toBe("Retrying 2/5");
    expect(describeStatus("scheduled_post", "failed", { attempt: 5, maxAttempts: 5 }).label).toBe("Failed");
    expect(describeStatus("post_set", "needs_approval", { via: "Telegram" }).label).toBe("Awaiting approval · Telegram");
    // Modifiers do not leak onto states they do not apply to.
    expect(describeStatus("post_variant", "approved", { pinRequired: true }).label).toBe("Approved");
  });
});

describe("account status and health", () => {
  it("maps each social_account.status, with both states that need a person as danger", () => {
    expect(STATUS_TABLE.account.active).toMatchObject({ label: "Healthy", tone: "ok" });
    expect(STATUS_TABLE.account.needs_reauth).toMatchObject({ label: "Needs reauth", tone: "danger" });
    expect(STATUS_TABLE.account.suspended.tone).toBe("danger");
    expect(STATUS_TABLE.account.disconnected).toMatchObject({ tone: "neutral", shape: "ring" });
  });

  it("lets a non-active status win over whatever the last check said", () => {
    expect(describeAccountHealth("needs_reauth", { token_valid: true }).label).toBe("Needs reauth");
    expect(describeAccountHealth("disconnected", { last_error: "boom" }).label).toBe("Disconnected");
  });

  it("refines an active account from its health check", () => {
    expect(describeAccountHealth("active", {})).toMatchObject({ label: "Healthy", tone: "ok" });
    expect(describeAccountHealth("active", { token_valid: true, scopes_ok: true, last_error: null }).label).toBe("Healthy");
    expect(describeAccountHealth("active", { token_valid: false })).toMatchObject({ label: "Token invalid", tone: "danger" });
    expect(describeAccountHealth("active", { scopes_ok: false })).toMatchObject({ label: "Missing scopes", tone: "warn" });
    expect(describeAccountHealth("active", { last_error: "timeout" })).toMatchObject({ label: "Check failed", tone: "warn" });
    expect(describeAccountHealth("active", { last_error: "  " }).label).toBe("Healthy");
  });

  it("puts warn and danger health under Needs attention, and nothing else", () => {
    expect(accountNeedsAttention("active", {})).toBe(false);
    expect(accountNeedsAttention("disconnected", {})).toBe(false);
    expect(accountNeedsAttention("needs_reauth", {})).toBe(true);
    expect(accountNeedsAttention("suspended", {})).toBe(true);
    expect(accountNeedsAttention("active", { scopes_ok: false })).toBe(true);
  });
});
