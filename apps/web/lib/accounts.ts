import type { AccountOut, NotificationOut, Platform as ApiPlatform, PlatformCapabilities } from "@mera/sdk";
import type { Platform as IconPlatform } from "@/components/icons";
import type { CapabilityTag } from "@/components/mera/capability-tags";
import { accountNeedsAttention } from "@/lib/status";

/*
 * Connected Accounts view logic (accounts.html), kept out of the components so
 * it can be tested without a DOM: the platform catalogue, the connect-return
 * query (?connected= / ?error=), capability tags from the adapter descriptor,
 * and what the dashboard surfaces.
 */

export type { ApiPlatform };

export interface PlatformInfo {
  platform: ApiPlatform;
  /** PlatformCapabilities.display_name. */
  label: string;
  /** The docs/11 phase the adapter ships in (PlatformCapabilities.phase). */
  phase: 1 | 2;
  icon: IconPlatform;
  /** How an account connects (PlatformCapabilities.connect_method, P2-M3). */
  connect: "oauth" | "oauth_instance" | "app_password";
}

/**
 * Every platform the API knows, in Connect-menu order. Mirrors the adapter
 * capability descriptors (`phase`, `display_name`); there is no catalogue
 * endpoint, so keep this in step with backend/.../platforms/capabilities.py.
 * Only phase 1 is connectable (docs/12 §4).
 */
export const PLATFORM_CATALOGUE: readonly PlatformInfo[] = [
  { platform: "linkedin_member", label: "LinkedIn", phase: 1, icon: "linkedin", connect: "oauth" },
  { platform: "linkedin_organization", label: "LinkedIn Page", phase: 1, icon: "linkedin", connect: "oauth" },
  { platform: "facebook", label: "Facebook Page", phase: 1, icon: "facebook", connect: "oauth" },
  { platform: "instagram", label: "Instagram", phase: 1, icon: "instagram", connect: "oauth" },
  { platform: "threads", label: "Threads", phase: 1, icon: "threads", connect: "oauth" },
  { platform: "x", label: "X", phase: 1, icon: "x", connect: "oauth" },
  { platform: "bluesky", label: "Bluesky", phase: 2, icon: "bluesky", connect: "app_password" },
  { platform: "mastodon", label: "Mastodon", phase: 2, icon: "mastodon", connect: "oauth_instance" },
];

const BY_PLATFORM = new Map(PLATFORM_CATALOGUE.map((p) => [p.platform, p]));

export function platformInfo(platform: ApiPlatform): PlatformInfo {
  return BY_PLATFORM.get(platform) ?? { platform, label: platform, phase: 2, icon: "linkedin", connect: "oauth" };
}

export function platformLabel(platform: ApiPlatform): string {
  return platformInfo(platform).label;
}

/** The glyph for an API platform (both LinkedIn kinds share one mark). */
export function platformIcon(platform: ApiPlatform): IconPlatform {
  return platformInfo(platform).icon;
}

export const ACCOUNT_TYPE_LABEL: Readonly<Record<string, string>> = {
  personal: "Personal",
  page: "Page",
  organization: "Company page",
  business: "Business",
};

export function accountTypeLabel(type: string): string {
  return ACCOUNT_TYPE_LABEL[type] ?? type.charAt(0).toUpperCase() + type.slice(1);
}

/** AccountChip input from an API account. */
export function accountRef(a: Pick<AccountOut, "id" | "platform" | "nickname" | "handle"> & { display_name?: string | null; avatar_url?: string | null }) {
  return {
    id: a.id,
    platform: platformIcon(a.platform),
    name: a.display_name?.trim() || a.nickname,
    handle: a.handle,
    avatar_url: a.avatar_url,
    platformLabel: platformLabel(a.platform),
  };
}

export function needsAttention(a: Pick<AccountOut, "status" | "health">): boolean {
  return accountNeedsAttention(a.status, a.health);
}

/** Disconnected accounts are history, not part of the workspace's live count. */
export function liveAccounts<T extends Pick<AccountOut, "status">>(accounts: readonly T[]): T[] {
  return accounts.filter((a) => a.status !== "disconnected");
}

/** "9 accounts · 5 platforms" for the topbar. */
export function accountsSummary(accounts: readonly Pick<AccountOut, "status" | "platform">[]): string {
  const live = liveAccounts(accounts);
  const platforms = new Set(live.map((a) => platformLabel(a.platform))).size;
  const n = live.length;
  return `${n} ${n === 1 ? "account" : "accounts"} · ${platforms} ${platforms === 1 ? "platform" : "platforms"}`;
}

/* ---- connect return ---------------------------------------------------- */

/*
 * The connect round trip (security review, Sprint 4): the platform redirects
 * to the API's callback, which redirects here with a one-time
 * `?connect_code=` (or `?error=platform_denied|platform_error`). The page
 * strips the code from the URL first, so a reload can't replay it, then the
 * signed-in user completes the connection with
 * POST /social-accounts/connect/complete (SDK completeConnect) through the BFF. Completing in the
 * web app, as the signed-in user, is what closes the consent-phishing hole of
 * a callback that connects on its own.
 */

/** Where the API's OAuth callback sends the browser back to. */
export function connectReturnPath(slug: string): string {
  return `/w/${slug}/accounts`;
}

export const CONNECT_ERROR_CODES = [
  "platform_denied",
  "platform_error",
  "invalid_connect_code",
  "not_permitted",
  "forbidden",
  "workspace_archived",
  "account_mismatch",
  "account_already_connected",
  "account_limit_reached",
  "account_suspended",
  "stagger_required",
] as const;

export type ConnectErrorCode = (typeof CONNECT_ERROR_CODES)[number];

export function connectErrorCode(code: string): ConnectErrorCode | "unknown" {
  return (CONNECT_ERROR_CODES as readonly string[]).includes(code) ? (code as ConnectErrorCode) : "unknown";
}

/** What the redirect brought back. */
export type ConnectReturn =
  | { kind: "code"; code: string }
  | { kind: "error"; code: ConnectErrorCode | "unknown"; accountId?: string };

type SearchParamsLike = URLSearchParams | Readonly<Record<string, string | string[] | undefined>>;

function param(params: SearchParamsLike, name: string): string | undefined {
  const raw = params instanceof URLSearchParams ? (params.get(name) ?? undefined) : params[name];
  const value = Array.isArray(raw) ? raw[0] : raw;
  return value && value.trim() !== "" ? value.trim() : undefined;
}

/** Read `?connect_code=<code>` or `?error=<code>[&account=<id>]` from the callback redirect. */
export function parseConnectReturn(params: SearchParamsLike): ConnectReturn | null {
  const code = param(params, "connect_code");
  if (code) return { kind: "code", code };
  const error = param(params, "error");
  if (!error) return null;
  const account = param(params, "account");
  return account
    ? { kind: "error", code: connectErrorCode(error), accountId: account }
    : { kind: "error", code: connectErrorCode(error) };
}

/** The query keys the callback adds; stripped as soon as the page has read them. */
export const CONNECT_RETURN_PARAMS = ["connect_code", "connected", "error", "account"] as const;

/** `href` without the connect-return keys (path, other params and hash kept). */
export function stripConnectParams(href: string): string {
  const url = new URL(href, "http://x.invalid");
  for (const key of CONNECT_RETURN_PARAMS) url.searchParams.delete(key);
  const search = url.searchParams.toString();
  const origin = /^[a-z][a-z0-9+.-]*:\/\//i.test(href) ? url.origin : "";
  return `${origin}${url.pathname}${search ? `?${search}` : ""}${url.hash}`;
}

/** The end state of a connect attempt, from the redirect or from connect/complete. */
export interface PageChoice {
  platform_account_id: string;
  handle: string;
  display_name: string | null;
  account_type: string;
}

export type ConnectResult =
  | { kind: "pending" }
  /** The sign-in manages several Pages (P2-M1.5): the person picks one. */
  | { kind: "choose"; code: string; choices: PageChoice[] }
  | { kind: "connected"; accountId: string }
  | { kind: "reconnected"; accountId: string }
  | { kind: "error"; code: ConnectErrorCode | "unknown"; accountId?: string; message?: string };

/** The first result to show: an error straight from the redirect, or "finishing" while the code is redeemed. */
export function initialConnectResult(ret: ConnectReturn | null): ConnectResult | null {
  if (!ret) return null;
  if (ret.kind === "code") return { kind: "pending" };
  return ret.accountId ? { kind: "error", code: ret.code, accountId: ret.accountId } : { kind: "error", code: ret.code };
}

export interface ConnectNotice {
  tone: "default" | "info" | "warn" | "danger";
  title: string;
  body?: string;
  /** Account to select in the detail panel (the new one, or the existing duplicate). */
  accountId?: string;
}

const CONNECT_ERROR_TEXT: Record<ConnectErrorCode | "unknown", { tone: ConnectNotice["tone"]; title: string; body: string }> = {
  platform_denied: {
    tone: "warn",
    title: "Nothing was connected.",
    body: "The platform didn't grant access. Try again and approve the request when the platform asks.",
  },
  platform_error: {
    tone: "danger",
    title: "The platform returned an error.",
    body: "Nothing was connected. Try again in a few minutes.",
  },
  invalid_connect_code: {
    tone: "warn",
    title: "Nothing was connected.",
    body: "This connection link has expired or belongs to someone else. Start again.",
  },
  not_permitted: {
    tone: "danger",
    title: "You can't connect accounts here.",
    body: "Only owners and admins can connect or reconnect accounts in this workspace.",
  },
  forbidden: {
    tone: "danger",
    title: "You can't connect accounts here.",
    body: "Only owners and admins can connect or reconnect accounts in this workspace.",
  },
  workspace_archived: {
    tone: "warn",
    title: "This workspace is archived.",
    body: "Accounts can't be connected until an owner restores it.",
  },
  account_mismatch: {
    tone: "warn",
    title: "That was a different account.",
    body: "You signed in to the platform as someone other than the account being reconnected. Sign in as that account and try again.",
  },
  account_already_connected: {
    tone: "info",
    title: "That account is already connected here.",
    body: "Mera kept the existing connection rather than adding a second one; two credential sets would race for one account's rate limits.",
  },
  account_limit_reached: {
    tone: "warn",
    title: "This workspace is at its account limit.",
    body: "Disconnect an account you no longer use, then connect this one.",
  },
  account_suspended: {
    tone: "danger",
    title: "That account is suspended.",
    body: "The platform has suspended it, so Mera can't publish to it. Resolve it with the platform, then reconnect.",
  },
  stagger_required: {
    tone: "warn",
    title: "Reconnecting would break a group's stagger rule.",
    body: "Change that group's stagger mode from “All at once” first, then reconnect.",
  },
  unknown: {
    tone: "danger",
    title: "Connecting didn't finish.",
    body: "Try again. If it keeps happening, check the platform's status.",
  },
};

/**
 * What to tell the user. `accounts` names the account once the list has it.
 * Where the API's message is the specific part (which account is the
 * duplicate, which group blocks a reconnect), it leads the body.
 */
export function connectNotice(
  result: ConnectResult,
  accounts: readonly Pick<AccountOut, "id" | "nickname" | "platform">[],
): ConnectNotice {
  if (result.kind === "pending") {
    return { tone: "default", title: "Finishing the connection…", body: "Confirming the connection is yours." };
  }
  if (result.kind === "choose") {
    return {
      tone: "default",
      title: "Choose which Page to connect.",
      body: "This sign-in manages several Pages. Connect one now; connect another the same way afterwards.",
    };
  }
  const named = result.accountId ? accounts.find((a) => a.id === result.accountId) : undefined;
  if (result.kind !== "error") {
    const verb = result.kind === "connected" ? "Connected" : "Reconnected";
    return {
      tone: "info",
      title: named ? `${verb} ${named.nickname}.` : `Account ${verb.toLowerCase()}.`,
      body:
        result.kind === "reconnected"
          ? "Its held posts can publish again."
          : named
            ? `${platformLabel(named.platform)} is ready. Add it to a group to post to it with others.`
            : "Add it to a group to post to it with others.",
      accountId: result.accountId,
    };
  }
  const text = CONNECT_ERROR_TEXT[result.code];
  const title =
    result.code === "account_already_connected" && named ? `${named.nickname} is already connected here.` : text.title;
  let body = text.body;
  if (result.message && result.code === "account_already_connected") body = result.message;
  if (result.message && result.code === "stagger_required") body = `${result.message} ${text.body}`;
  return { tone: text.tone, title, body, ...(result.accountId ? { accountId: result.accountId } : {}) };
}

/** Messages for API errors from connect / reauthorize / disconnect / update / health check. */
export const ACCOUNT_ERROR_TEXT: Readonly<Record<string, string>> = {
  platform_unavailable: "That platform isn’t configured. Ask a workspace admin to check Settings → Social OAuth apps.",
  instance_required: "Enter the server the account lives on, like mastodon.social.",
  invalid_instance: "That doesn't look like a public Mastodon server. Use its address, like mastodon.social.",
  instance_unavailable: "That server didn't let Mera register. Check the address, or try again later.",
  use_app_password: "Bluesky connects with an app password, not a sign-in page.",
  account_limit_reached: "This workspace is at its account limit. Disconnect an account you no longer use first.",
  workspace_archived: "This workspace is archived, so its accounts can't change.",
  forbidden: "Only owners and admins can change connected accounts.",
};

/* ---- capabilities ------------------------------------------------------- */

/**
 * accounts.html "What this account can do", from the adapter descriptor
 * (ADR-0008: never a platform-name check). Supported first, then
 * limitations, then partial support (warn), as CapabilityTags orders them.
 */
export function accountCapabilityTags(caps: PlatformCapabilities): CapabilityTag[] {
  const tags: CapabilityTag[] = [];
  const flag = (key: string, value: boolean | undefined, yes: string, no: string) => {
    if (value === undefined) return;
    tags.push({ key, label: value ? yes : no, support: value ? "yes" : "no" });
  };
  flag("supports_alt_text", caps.supports_alt_text, "Alt text", "No alt text");
  flag("supports_threads", caps.supports_threads, "Threads", "No threads");
  flag("supports_edit", caps.supports_edit, "Editing", "No editing");
  flag("supports_delete", caps.supports_delete, "Delete", "No deleting");
  flag("supports_native_scheduling", caps.supports_native_scheduling, "Native scheduling", "No native scheduling");
  flag("supports_hide_comments", caps.supports_hide_comments, "Hide comments", "No comment hiding");
  if (caps.requires_media) tags.push({ key: "requires_media", label: "Media required", support: "partial" });
  switch (caps.account_analytics) {
    case "full":
      tags.push({ key: "analytics", label: "Analytics", support: "yes" });
      break;
    case "counts_only":
      tags.push({ key: "analytics", label: "Counts only, no impressions", support: "partial" });
      break;
    case "paid":
      tags.push({ key: "analytics", label: "Analytics need a paid API tier", support: "partial" });
      break;
    case "none":
      tags.push({ key: "analytics", label: "No analytics", support: "no" });
      break;
  }
  if (caps.prohibits_duplicate_across_accounts)
    tags.push({ key: "no_duplicates", label: "Different text per account", support: "partial" });
  if (caps.rate_limit_shared_across_accounts)
    tags.push({ key: "shared_rate_limit", label: "Rate limit shared across accounts", support: "partial" });
  const order = { yes: 0, no: 1, partial: 2 } as const;
  return tags.sort((a, b) => order[a.support] - order[b.support]);
}

/* ---- dashboard ---------------------------------------------------------- */

export type AttentionItem =
  | { kind: "account"; key: string; account: AccountOut }
  | { kind: "notification"; key: string; notification: NotificationOut };

/**
 * Account health comes first on the dashboard: a dead token silently stops
 * publishing. Accounts needing reauth, then unread critical notifications
 * that are not about one of those accounts (no saying the same thing twice).
 */
export function dashboardAttention(
  accounts: readonly AccountOut[],
  notifications: readonly NotificationOut[],
): AttentionItem[] {
  const reauth = accounts.filter((a) => a.status === "needs_reauth");
  const covered = new Set(reauth.map((a) => a.id));
  const critical = notifications.filter(
    (n) => n.severity === "critical" && n.read_at === null && !(n.social_account_id && covered.has(n.social_account_id)),
  );
  return [
    ...reauth.map((account) => ({ kind: "account" as const, key: `a:${account.id}`, account })),
    ...critical.map((notification) => ({ kind: "notification" as const, key: `n:${notification.id}`, notification })),
  ];
}

/* ---- filters ------------------------------------------------------------ */

export interface AccountFilter {
  /** "attention": only accounts whose health is warn or danger. */
  view: "all" | "attention";
  platform?: ApiPlatform;
  /** A group id, or "ungrouped". */
  group?: string;
}

export const ALL_ACCOUNTS: AccountFilter = { view: "all" };

export function isUnfiltered(f: AccountFilter): boolean {
  return f.view === "all" && f.platform === undefined && f.group === undefined;
}

export function filterAccounts<T extends Pick<AccountOut, "status" | "health" | "platform" | "group_ids">>(
  accounts: readonly T[],
  f: AccountFilter,
): T[] {
  return accounts.filter(
    (a) =>
      (f.view === "all" || needsAttention(a)) &&
      (f.platform === undefined || a.platform === f.platform) &&
      (f.group === undefined || (f.group === "ungrouped" ? a.group_ids.length === 0 : a.group_ids.includes(f.group))),
  );
}
