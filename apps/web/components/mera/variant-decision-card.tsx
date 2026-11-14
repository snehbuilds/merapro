import type { ComponentProps, ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { StatusPill } from "@/components/ui/status-pill";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import type { ApprovalVariantState, StatusModifiers } from "@/lib/status";
import { cn } from "@/lib/utils";
import { AccountChip, type AccountRef } from "./account-chip";
import { ApprovalProof, type ApprovalProofProps } from "./approval-proof";

/*
 * approvals.html, one <article> per account: the decision unit. Every account
 * in a set is approved, edited or rejected on its own.
 *
 *   header   AccountChip lg (name, handle · account kind · time) · confidence ·
 *            "PIN required" · approval status
 *   preview  a PlatformPreview (passed in)
 *   notices  similarity warnings, differentiation notes (Notice elements)
 *   actions  Approve this account (primary) · Edit · Regenerate · Reschedule ·
 *            … · Reject (danger, pushed right). Only callbacks that are passed
 *            render a button.
 *   proof    once decided: the ApprovalProof receipt and a caption.
 *
 * Edit is driven by capabilities, never a platform check (ADR-0008): pass
 * `capabilities.supports_edit === false` and Edit renders disabled with the
 * reason, rather than disappearing.
 */
export interface DecisionActions {
  onApprove?: () => void;
  onEdit?: () => void;
  onRegenerate?: () => void;
  onReschedule?: () => void;
  onReject?: () => void;
}

export interface VariantDecisionCardProps extends Omit<ComponentProps<"article">, "children">, DecisionActions {
  account: Pick<AccountRef, "platform" | "name" | "handle">;
  /** e.g. "personal", "company page", "thread, 3 posts". */
  accountKind?: ReactNode;
  scheduledAt?: Date | string;
  tz: string;
  confidence?: number;
  state: ApprovalVariantState;
  statusModifiers?: StatusModifiers;
  preview: ReactNode;
  notices?: ReactNode;
  /** Inline note in the action row, e.g. "Company page — publishing asks for your PIN." */
  actionNote?: ReactNode;
  capabilities?: { supports_edit?: boolean; edit_unsupported_reason?: string };
  proof?: ApprovalProofProps;
  /** Under the proof: what happens if this is edited now. */
  caption?: ReactNode;
  /** Disables the action row (e.g. while a decision is in flight). */
  busy?: boolean;
}

export function VariantDecisionCard({
  account,
  accountKind,
  scheduledAt,
  tz,
  confidence,
  state,
  statusModifiers,
  preview,
  notices,
  actionNote,
  capabilities,
  proof,
  caption,
  busy,
  onApprove,
  onEdit,
  onRegenerate,
  onReschedule,
  onReject,
  className,
  ...props
}: VariantDecisionCardProps) {
  const decided = state !== "pending";
  const editSupported = capabilities?.supports_edit !== false;
  const meta = (
    <>
      {[account.handle, accountKind].filter(Boolean).map((part, i) => (
        <span key={i}>
          {part}
          {" · "}
        </span>
      ))}
      {scheduledAt ? <Time value={scheduledAt} tz={tz} className="text-xs" /> : null}
    </>
  );
  const hasActions = !decided && (onApprove || onEdit || onRegenerate || onReschedule || onReject);

  return (
    <article
      data-slot="variant-decision-card"
      data-state={state}
      aria-label={`${account.name}`}
      className={cn(
        "border-b border-border px-[18px] py-3.5 last:border-b-0",
        decided && "bg-surface-muted",
        className,
      )}
      {...props}
    >
      <div className="mb-2.5 flex flex-wrap items-center justify-between gap-3.5">
        <AccountChip account={account} size="lg" meta={meta} />
        <span className="flex shrink-0 flex-wrap items-center gap-2">
          {confidence !== undefined ? <Tag tone="outline">Confidence {confidence.toFixed(2)}</Tag> : null}
          {statusModifiers?.pinRequired && !decided ? <Tag tone="warn">PIN required</Tag> : null}
          <StatusPill
            kind="approval_variant"
            state={state}
            // The PIN is already a tag here; keep the pill to the plain state.
            modifiers={statusModifiers ? { ...statusModifiers, pinRequired: false } : undefined}
          />
        </span>
      </div>

      {preview}

      {notices ? <div className="mt-2.5 grid gap-2">{notices}</div> : null}

      {hasActions ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {onApprove ? (
            <Button variant="primary" size="sm" onClick={onApprove} disabled={busy}>
              Approve this account
            </Button>
          ) : null}
          {onEdit ? (
            <Button
              size="sm"
              onClick={onEdit}
              disabled={busy || !editSupported}
              title={!editSupported ? capabilities?.edit_unsupported_reason : undefined}
            >
              Edit
            </Button>
          ) : null}
          {onRegenerate ? (
            <Button size="sm" onClick={onRegenerate} disabled={busy}>
              Regenerate
            </Button>
          ) : null}
          {onReschedule ? (
            <Button size="sm" onClick={onReschedule} disabled={busy}>
              Reschedule
            </Button>
          ) : null}
          {actionNote ? <span className="text-xs text-fg-subtle">{actionNote}</span> : null}
          {!editSupported && onEdit && capabilities?.edit_unsupported_reason ? (
            <span className="text-xs text-fg-subtle">{capabilities.edit_unsupported_reason}</span>
          ) : null}
          <span className="flex-1" />
          {onReject ? (
            <Button variant="danger" size="sm" onClick={onReject} disabled={busy}>
              Reject
            </Button>
          ) : null}
        </div>
      ) : null}

      {proof ? <ApprovalProof className="mt-2.5" {...proof} /> : null}
      {caption ? <p className="mt-2 text-xs leading-[1.55] text-fg-subtle">{caption}</p> : null}
    </article>
  );
}
