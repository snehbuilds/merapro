import type { GroupOut, StaggerMode } from "@mera/sdk";
import type { GroupMember } from "@/lib/api/models";
import { platformLabel } from "@/lib/accounts";
import { groupColor } from "@/lib/group-color";

/*
 * Account Groups view logic (groups.html, ADR-0012): member order, the
 * platform summary in the group list, and the stagger guardrail.
 */

/** The group's chosen colour, or one generated from its id. */
export function groupSwatch(group: Pick<GroupOut, "id" | "color">): string {
  return group.color ?? groupColor(group.id);
}

/** Members in publish order. */
export function orderedMembers<T extends Pick<GroupMember, "position">>(members: readonly T[]): T[] {
  return [...members].sort((a, b) => a.position - b.position);
}

/** "LinkedIn ×2, X ×2, Facebook Page", in publish order of first appearance. */
export function platformSummary(members: readonly Pick<GroupMember, "platform" | "position">[]): string {
  const counts = new Map<string, number>();
  for (const m of orderedMembers(members)) {
    const label = platformLabel(m.platform);
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  if (counts.size === 0) return "No accounts yet";
  return [...counts].map(([label, n]) => (n > 1 ? `${label} ×${n}` : label)).join(", ");
}

/**
 * Move the item at `from` to `to` (both indexes into the current order) and
 * renumber positions 0..n-1. Out-of-range or no-op moves return null.
 *
 * `to` is also the PATCH .../members/{id} `position`: the API removes the
 * account from the order and re-inserts it at that index, which is exactly
 * this splice, so the optimistic order and the server's agree.
 */
export function moveMember<T extends Pick<GroupMember, "position">>(
  members: readonly T[],
  from: number,
  to: number,
): T[] | null {
  const order = orderedMembers(members);
  if (from === to || from < 0 || to < 0 || from >= order.length || to >= order.length) return null;
  const [moved] = order.splice(from, 1);
  order.splice(to, 0, moved!);
  return order.map((m, position) => ({ ...m, position }));
}

/** The move for an up / down button press on the member at `index`. */
export function stepMove(index: number, direction: "up" | "down", length: number): { from: number; to: number } | null {
  const to = direction === "up" ? index - 1 : index + 1;
  return to < 0 || to >= length ? null : { from: index, to };
}

/**
 * docs/08 §3.3: a group with two or more live accounts on one platform cannot
 * publish "all at once". Every Phase 1 adapter sets
 * `requires_stagger_within_platform`, so the client predicts the rule from the
 * member list; the API's `stagger_required` 422 stays the authority, and its
 * message wins when it arrives. Returns the first conflict, or null.
 */
export function staggerConflict(
  members: readonly Pick<GroupMember, "platform" | "status">[],
): { platform: GroupMember["platform"]; label: string; count: number } | null {
  const counts = new Map<GroupMember["platform"], number>();
  for (const m of members) {
    if (m.status === "disconnected") continue;
    counts.set(m.platform, (counts.get(m.platform) ?? 0) + 1);
  }
  for (const [platform, count] of counts) {
    if (count >= 2) return { platform, label: platformLabel(platform), count };
  }
  return null;
}

/** The reason "All at once" is unavailable, worded as the API words stagger_required; null when allowed. */
export function staggerBlock(members: readonly Pick<GroupMember, "platform" | "status">[]): string | null {
  const c = staggerConflict(members);
  return c
    ? `This group has ${c.count} ${c.label} accounts, so posts must be staggered. Choose a stagger mode other than 'all at once'.`
    : null;
}

export const STAGGER_MODE_LABEL: Readonly<Record<StaggerMode, string>> = {
  random_spread: "Random spread",
  fixed: "Fixed interval",
  none: "All at once",
};

export const STAGGER_MODES: readonly StaggerMode[] = ["random_spread", "fixed", "none"];

/** Seconds ↔ the minutes the form shows. The API bound is 0 ≤ min ≤ max ≤ 6 h. */
export const STAGGER_MAX_MINUTES = 360;

export function secondsToMinutes(seconds: number): string {
  const m = seconds / 60;
  return Number.isInteger(m) ? String(m) : m.toFixed(1);
}

export function minutesToSeconds(value: string): number | null {
  const trimmed = value.trim();
  if (trimmed === "" || !/^\d+(\.\d+)?$/.test(trimmed)) return null;
  return Math.round(Number(trimmed) * 60);
}

/** Client-side check before the API's invalid_stagger_bounds. */
export function staggerBoundsError(minSeconds: number | null, maxSeconds: number | null): string | null {
  if (minSeconds === null || maxSeconds === null) return "Enter both bounds in minutes.";
  if (minSeconds > maxSeconds) return "The minimum can't be more than the maximum.";
  if (maxSeconds > STAGGER_MAX_MINUTES * 60) return "The maximum is 6 hours (360 minutes).";
  return null;
}

/** Messages for group API errors that carry no field. */
export const GROUP_ERROR_TEXT: Readonly<Record<string, string>> = {
  workspace_archived: "This workspace is archived, so its groups can't change.",
  forbidden: "Only editors, admins and owners can change groups.",
};
