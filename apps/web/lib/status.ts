/*
 * The one table that turns backend state enums into what the UI shows
 * (docs/12 §3 F2, "Rules baked into components, not pages").
 *
 * Inputs are the enums from docs/03: post_variant.state,
 * approval_variant_state.state, scheduled_post.state, post_set.state,
 * social_account.status (plus its health check, via describeAccountHealth)
 * and source.status.
 * Every value maps to a label, a tone and a dot shape, so status is never
 * carried by colour alone (mera.css .status).
 *
 * Tone rules:
 *   ok       done and good (approved, published)
 *   warn     waiting on a person (pending approval)
 *   info     the machine has it in hand (scheduled, publishing, partial)
 *   danger   needs someone to act (failed, blocked, dead-lettered)
 *   neutral  ended or set aside, no action needed (skipped, expired, rejected)
 *
 * Partial states are NOT danger (PRODUCT.md "Partial is normal"): two of four
 * approved, three of four published, are states to render clearly.
 *
 * Display variants seen in the mockups ("Pending · PIN", "Needs 1 more",
 * "Retrying 2/5") are derived here from the inputs plus a few modifiers; they
 * are never stored.
 */

export type StatusTone = "ok" | "warn" | "danger" | "info" | "neutral";
/** full (filled circle), ring (outline), half (half-filled), diamond. */
export type StatusShape = "full" | "ring" | "half" | "diamond";

export const POST_VARIANT_STATES = [
  "drafting",
  "needs_approval",
  "approved",
  "scheduled",
  "publishing",
  "published",
  "failed",
  "rejected",
  "blocked",
  "skipped",
] as const;

export const APPROVAL_VARIANT_STATES = [
  "pending",
  "approved",
  "rejected",
  "superseded",
  "expired",
  "cancelled",
] as const;

export const SCHEDULED_POST_STATES = [
  "queued",
  "publishing",
  "published",
  "failed",
  "dead_lettered",
  "cancelled",
] as const;

export const POST_SET_STATES = [
  "drafting",
  "needs_approval",
  "approved",
  "partially_approved",
  "scheduled",
  "published",
  "partially_published",
  "rejected",
  "blocked",
  "archived",
] as const;

export const ACCOUNT_STATES = ["active", "needs_reauth", "suspended", "disconnected"] as const;

/** source.status (docs/03). "failing" is set only by the system. */
export const SOURCE_STATES = ["active", "failing", "paused"] as const;

export type PostVariantState = (typeof POST_VARIANT_STATES)[number];
export type ApprovalVariantState = (typeof APPROVAL_VARIANT_STATES)[number];
export type ScheduledPostState = (typeof SCHEDULED_POST_STATES)[number];
export type PostSetState = (typeof POST_SET_STATES)[number];
export type AccountState = (typeof ACCOUNT_STATES)[number];
export type SourceState = (typeof SOURCE_STATES)[number];

export interface StatusStates {
  post_variant: PostVariantState;
  approval_variant: ApprovalVariantState;
  scheduled_post: ScheduledPostState;
  post_set: PostSetState;
  account: AccountState;
  source: SourceState;
}

export type StatusKind = keyof StatusStates;

export interface StatusSpec {
  label: string;
  tone: StatusTone;
  shape: StatusShape;
}

const s = (label: string, tone: StatusTone, shape: StatusShape): StatusSpec => ({ label, tone, shape });

/** Exhaustive by construction: `satisfies` fails the build if an enum value is missing. */
export const STATUS_TABLE = {
  post_variant: {
    drafting: s("Drafting", "neutral", "ring"),
    needs_approval: s("Pending", "warn", "half"),
    approved: s("Approved", "ok", "full"),
    scheduled: s("Scheduled", "info", "full"),
    publishing: s("Publishing", "info", "half"),
    published: s("Published", "ok", "full"),
    failed: s("Failed", "danger", "full"),
    rejected: s("Rejected", "neutral", "full"),
    blocked: s("Blocked", "danger", "full"),
    skipped: s("Skipped", "neutral", "ring"),
  },
  approval_variant: {
    pending: s("Pending", "warn", "half"),
    approved: s("Approved", "ok", "full"),
    rejected: s("Rejected", "neutral", "full"),
    superseded: s("Superseded", "neutral", "ring"),
    expired: s("Expired", "neutral", "ring"),
    cancelled: s("Cancelled", "neutral", "ring"),
  },
  scheduled_post: {
    queued: s("Queued", "info", "ring"),
    publishing: s("Publishing", "info", "half"),
    published: s("Published", "ok", "full"),
    failed: s("Failed", "danger", "full"),
    dead_lettered: s("Dead letter", "danger", "full"),
    cancelled: s("Cancelled", "neutral", "ring"),
  },
  post_set: {
    drafting: s("Draft", "neutral", "ring"),
    needs_approval: s("Awaiting approval", "warn", "half"),
    approved: s("Approved", "ok", "full"),
    partially_approved: s("Partially approved", "info", "half"),
    scheduled: s("Scheduled", "info", "full"),
    published: s("Published", "ok", "full"),
    partially_published: s("Partially published", "info", "half"),
    rejected: s("Rejected", "neutral", "full"),
    blocked: s("Blocked", "danger", "full"),
    archived: s("Archived", "neutral", "ring"),
  },
  // social_account.status. A dead token silently stops publishing, so both
  // states that need a person to act are danger (dashboard.html, accounts.html).
  account: {
    active: s("Healthy", "ok", "full"),
    needs_reauth: s("Needs reauth", "danger", "full"),
    suspended: s("Suspended", "danger", "full"),
    disconnected: s("Disconnected", "neutral", "ring"),
  },
  // source.status (sources.html): a failing feed silently stops new items, so it is danger.
  source: {
    active: s("Active", "ok", "full"),
    failing: s("Failing", "danger", "full"),
    paused: s("Paused", "neutral", "ring"),
  },
} as const satisfies { [K in StatusKind]: Record<StatusStates[K], StatusSpec> };

export const STATUS_ENUMS: { [K in StatusKind]: readonly StatusStates[K][] } = {
  post_variant: POST_VARIANT_STATES,
  approval_variant: APPROVAL_VARIANT_STATES,
  scheduled_post: SCHEDULED_POST_STATES,
  post_set: POST_SET_STATES,
  account: ACCOUNT_STATES,
  source: SOURCE_STATES,
};

/**
 * Context that refines a label without changing the stored state.
 * - pinRequired: the account publishes behind a PIN ("Pending · PIN").
 * - approvalsRemaining: multi-approver policy ("Needs 1 more").
 * - attempt / maxAttempts: a failed scheduled post that will retry ("Retrying 2/5").
 * - via: the surface a pending request went out on ("Awaiting approval · Telegram").
 */
export interface StatusModifiers {
  pinRequired?: boolean;
  approvalsRemaining?: number;
  attempt?: number;
  maxAttempts?: number;
  via?: string;
}

export function statusSpec<K extends StatusKind>(kind: K, state: StatusStates[K]): StatusSpec {
  const table = STATUS_TABLE[kind] as Record<StatusStates[K], StatusSpec>;
  return table[state];
}

/** Resolve the spec and apply modifiers. Pure, so it is safe in server components. */
export function describeStatus<K extends StatusKind>(
  kind: K,
  state: StatusStates[K],
  mods: StatusModifiers = {},
): StatusSpec {
  const base = statusSpec(kind, state);
  const pending =
    (kind === "post_variant" && state === "needs_approval") ||
    (kind === "approval_variant" && state === "pending") ||
    (kind === "post_set" && state === "needs_approval");

  if (pending && mods.approvalsRemaining !== undefined && mods.approvalsRemaining > 0) {
    return { ...base, label: `Needs ${mods.approvalsRemaining} more` };
  }
  if (pending && mods.pinRequired) {
    return { ...base, label: `${base.label} · PIN` };
  }
  if (pending && mods.via) {
    return { ...base, label: `${base.label} · ${mods.via}` };
  }
  const retrying =
    (kind === "scheduled_post" || kind === "post_variant") &&
    state === "failed" &&
    mods.attempt !== undefined &&
    mods.maxAttempts !== undefined &&
    mods.attempt < mods.maxAttempts;
  if (retrying) {
    return { ...base, label: `Retrying ${mods.attempt}/${mods.maxAttempts}` };
  }
  return base;
}

/**
 * The last health check on a social account (AccountOut.health). Every field
 * is optional: an account that has never been checked has an empty object.
 */
export interface AccountHealth {
  token_valid?: boolean | null;
  scopes_ok?: boolean | null;
  rate_remaining?: number | null;
  last_error?: string | null;
}

/**
 * The Health column: the account's status first (anything but active wins),
 * then what the last health check found on an active account.
 *   token rejected       danger  "Token invalid"
 *   scopes missing       warn    "Missing scopes"
 *   check errored        warn    "Check failed"
 *   otherwise            the status table's "Healthy"
 */
export function describeAccountHealth(status: AccountState, health: AccountHealth | Record<string, unknown> = {}): StatusSpec {
  if (status !== "active") return statusSpec("account", status);
  const h = health as AccountHealth;
  if (h.token_valid === false) return s("Token invalid", "danger", "full");
  if (h.scopes_ok === false) return s("Missing scopes", "warn", "half");
  if (typeof h.last_error === "string" && h.last_error.trim() !== "") return s("Check failed", "warn", "half");
  return statusSpec("account", "active");
}

/** Warn or danger health: the account shows under "Needs attention". */
export function accountNeedsAttention(status: AccountState, health?: AccountHealth | Record<string, unknown>): boolean {
  const tone = describeAccountHealth(status, health).tone;
  return tone === "warn" || tone === "danger";
}
