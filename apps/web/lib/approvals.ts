/*
 * The Approval Queue's vocabulary (M6, docs/07, ADR-0014). Every decision sends back
 * the content hash of each account's post as it was shown here; if the post changed in
 * between, the API refuses (422 approval_content_mismatch) instead of approving new text.
 */

import type { ApprovalRequest, ApprovalVariant } from "@/lib/api/models";
import { APPROVAL_VARIANT_STATES } from "@/lib/status";

export type ApprovalVariantState = (typeof APPROVAL_VARIANT_STATES)[number];

export function asApprovalVariantState(state: string): ApprovalVariantState {
  return (APPROVAL_VARIANT_STATES as readonly string[]).includes(state) ? (state as ApprovalVariantState) : "pending";
}

export const OPEN_REQUEST_STATES = ["pending", "awaiting_edit"] as const;

export function isOpen(req: Pick<ApprovalRequest, "state">): boolean {
  return (OPEN_REQUEST_STATES as readonly string[]).includes(req.state);
}

export const REQUEST_STATE_LABEL: Record<string, string> = {
  pending: "Waiting",
  awaiting_edit: "Edit requested",
  approved: "Approved",
  partially_approved: "Partially approved",
  rejected: "Rejected",
  expired: "Expired",
  superseded: "Superseded",
  cancelled: "Withdrawn",
};

/** The hashes the approver is looking at, for the accounts a decision names. */
export function shownHashes(variants: readonly ApprovalVariant[]): Record<string, string> {
  return Object.fromEntries(variants.map((v) => [v.post_variant_id, v.content_hash]));
}

export function decidable(req: ApprovalRequest): ApprovalVariant[] {
  return req.variants.filter((v) => v.can_decide);
}

/** "1 of 2 approvals" when an account needs more than one approver. */
export function quorumLine(v: Pick<ApprovalVariant, "required" | "approve_count" | "quorum">): string | null {
  if (v.required <= 1) return null;
  return `${v.approve_count} of ${v.required} approvals${v.quorum === "all_of" ? " (every named approver)" : ""}`;
}

/** Product copy for the decision and submission errors (ADR-0014 §3). */
export const APPROVAL_ERROR_TEXT: Record<string, string> = {
  approval_content_mismatch: "This post changed since you opened it. Reload to review the new text before deciding.",
  approval_already_decided: "Someone already decided this. Reload to see the outcome.",
  already_voted: "You already approved this account; it needs another approver.",
  approval_expired: "This request expired. Nothing was published; send it for approval again.",
  approver_not_authorized: "You can't approve one of these accounts. Decide only the ones shown as yours.",
  kill_switch_engaged: "Automation is stopped for one of these accounts. Resume it before approving.",
  guardrail_blocked: "A guardrail now blocks this post (for example a newly banned word). Edit it, then send it again.",
  approval_pending: "This post is already waiting for approval.",
  generation_in_progress: "Drafting is still running. Send it once the drafts are in.",
  workspace_archived: "This workspace is archived, so nothing can change.",
};
