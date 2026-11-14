import type { ComponentProps, ReactNode } from "react";
import { Time } from "@/components/ui/time";
import { cn } from "@/lib/utils";

/*
 * mera.css .proof: the approval receipt, a perforated (dashed) slip on muted
 * paper. Keys are condensed tracked labels; machine values (version, hash) are
 * mono. It states exactly what was approved: who, when, where, which account,
 * which version, and the content hash the approval is bound to (editing makes
 * a new version and invalidates it).
 */
export interface ApprovalProofProps extends Omit<ComponentProps<"div">, "children"> {
  decision?: "Approved" | "Rejected";
  approver: ReactNode;
  at: Date | string;
  tz: string;
  /** Where the decision was made: "Web", "Telegram". */
  surface: ReactNode;
  account: ReactNode;
  version: number | string;
  hash: string;
}

function Item({ k, children, mono }: { k: ReactNode; children: ReactNode; mono?: boolean }) {
  return (
    <div className="inline-flex items-baseline gap-2.5">
      <dt className="font-label text-2xs font-semibold tracking-[0.1em] text-fg-subtle uppercase">{k}</dt>
      <dd className={cn(mono ? "font-mono text-xs text-fg" : "text-caption")}>{children}</dd>
    </div>
  );
}

export function ApprovalProof({
  decision = "Approved",
  approver,
  at,
  tz,
  surface,
  account,
  version,
  hash,
  className,
  ...props
}: ApprovalProofProps) {
  const v = typeof version === "number" ? `v${version}` : version;
  return (
    <div
      role="group"
      aria-label="Approval receipt"
      data-slot="approval-proof"
      className={cn(
        "relative rounded-md border border-dashed border-border-strong bg-surface-muted px-3 py-2",
        className,
      )}
      {...props}
    >
      <dl className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
        <Item k={decision}>
          {approver} · <Time value={at} tz={tz} className="text-caption" /> via {surface}
        </Item>
        <Item k="Account">{account}</Item>
        <Item k="Version" mono>
          {v}
        </Item>
        <Item k="Hash" mono>
          {hash}
        </Item>
      </dl>
    </div>
  );
}
