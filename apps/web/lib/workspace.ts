import type { MeOut, WorkspaceOut } from "@mera/sdk";

/*
 * Shell view models built from GET /workspaces and GET /auth/me. Routes are
 * /w/[slug]/…; the slug is resolved against the user's own workspace list,
 * so a pasted link can never act in a workspace the user isn't in.
 */

export interface ShellWorkspace {
  id: string;
  slug: string;
  name: string;
  /** Two-letter mark shown in the switcher. */
  mark: string;
  /** Secondary line in the switcher: role and timezone. */
  meta: string;
  /** The signed-in user's role here. */
  role: string;
  timezone: string;
  /** Archived workspaces are read-only. */
  archived: boolean;
}

export interface ShellUser {
  id: string;
  name: string;
  email: string;
  initials: string;
  /** Unverified accounts can't accept invitations (GET /auth/me email_verified). */
  emailVerified: boolean;
}

export const ROLE_LABEL: Readonly<Record<string, string>> = {
  owner: "Owner",
  admin: "Admin",
  editor: "Editor",
  approver: "Approver",
  viewer: "Viewer",
};

export function roleLabel(role: string): string {
  return ROLE_LABEL[role] ?? role.charAt(0).toUpperCase() + role.slice(1);
}

/** "Acme" → "AC", "Personal Brand" → "PB". */
export function initials(name: string): string {
  const words = name.trim().split(/[\s._-]+/u).filter(Boolean);
  const chars =
    words.length >= 2
      ? [Array.from(words[0]!)[0], Array.from(words[1]!)[0]]
      : Array.from(words[0] ?? "").slice(0, 2);
  return chars.join("").toUpperCase() || "?";
}

export function toShellWorkspace(ws: WorkspaceOut): ShellWorkspace {
  return {
    id: ws.id,
    slug: ws.slug,
    name: ws.name,
    mark: initials(ws.name),
    meta: `${roleLabel(ws.role)} · ${ws.timezone}`,
    role: ws.role,
    timezone: ws.timezone,
    archived: ws.archived,
  };
}

export function toShellUser(user: MeOut): ShellUser {
  return {
    id: user.id,
    name: user.display_name,
    email: user.email,
    initials: initials(user.display_name),
    emailVerified: user.email_verified,
  };
}

/** Owner and admin manage members, invitations and API keys (docs/04 §2.6). */
export function isAdminRole(role: string): boolean {
  return role === "owner" || role === "admin";
}

/** Editors and up manage account groups (docs/04 §2.6, MANAGE_GROUPS). */
export function isEditorRole(role: string): boolean {
  return role === "owner" || role === "admin" || role === "editor";
}

/** Roles an invitation can grant (ownership is transferred, never invited). */
export const INVITE_ROLES = ["admin", "editor", "approver", "viewer"] as const;
export type InviteRole = (typeof INVITE_ROLES)[number];

/** The id of the invite form, which the Team topbar's "Invite" action jumps to. */
export const INVITE_FORM_ID = "invite";

/** Roles a member can be given; only an owner may make another owner (the API decides). */
export const MEMBER_ROLES = ["owner", "admin", "editor", "approver", "viewer"] as const;

export function findBySlug<T extends { slug: string }>(list: readonly T[], slug: string): T | undefined {
  return list.find((w) => w.slug === slug);
}

/** Slug the API accepts: 1–48 of a-z 0-9 and "-", starting and ending alphanumeric. */
export const SLUG_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$/;

/** "Acme Social, Inc." → "acme-social-inc". */
export function slugify(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48)
    .replace(/-+$/g, "");
}

/**
 * A member's scope in words (Team, read-only in Phase 1: docs/11 §5).
 * Unrestricted → "Whole workspace"; restricted → the group names, then the
 * account nicknames. Ids the viewer can't resolve are counted, not dropped.
 */
export function scopeSummary(
  scopes: { restricted: boolean; account_group_ids: readonly string[]; social_account_ids: readonly string[] },
  groups: readonly { id: string; name: string }[],
  accounts: readonly { id: string; nickname: string }[],
): string {
  if (!scopes.restricted) return "Whole workspace";
  const groupNames = new Map(groups.map((g) => [g.id, g.name]));
  const accountNames = new Map(accounts.map((a) => [a.id, a.nickname]));
  const parts: string[] = [];
  let unknown = 0;
  for (const id of scopes.account_group_ids) {
    const n = groupNames.get(id);
    if (n) parts.push(`${n} (group)`);
    else unknown++;
  }
  for (const id of scopes.social_account_ids) {
    const n = accountNames.get(id);
    if (n) parts.push(n);
    else unknown++;
  }
  if (unknown > 0) parts.push(`${unknown} more`);
  return parts.length > 0 ? parts.join(", ") : "Nothing yet";
}
