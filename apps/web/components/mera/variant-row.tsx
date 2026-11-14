import type { ComponentProps, ReactNode } from "react";
import { StatusPill } from "@/components/ui/status-pill";
import { Tag } from "@/components/ui/tag";
import { Time } from "@/components/ui/time";
import type { PostVariantState, StatusModifiers } from "@/lib/status";
import { cn } from "@/lib/utils";
import { AccountChip, type AccountRef } from "./account-chip";

/*
 * mera.css .variant: one account's post, hanging off its post set on a drawn
 * branch (the ::before tick here; the trunk is VariantList's ::before).
 * Columns: account (180px) · excerpt · aside (confidence, time, status).
 *
 * `off` is an account switched off for this set: the reason replaces the
 * excerpt and is struck through, the mark dims, and the status reads Skipped.
 * The strike is visual; screen readers hear "Off:" before the reason.
 */
export interface VariantRowProps extends Omit<ComponentProps<"div">, "children"> {
  account: Pick<AccountRef, "platform" | "name" | "handle">;
  excerpt?: ReactNode;
  /** Editor confidence 0..1, shown as a mono outline tag. */
  confidence?: number;
  /** Scheduled publish time, rendered in `tz`. */
  time?: Date | string;
  tz?: string;
  /** The set head already states the zone, so rows can omit it. */
  showZone?: boolean;
  state?: PostVariantState;
  statusModifiers?: StatusModifiers;
  /** Switched off for this set, with the reason shown in place of the excerpt. */
  off?: { reason: ReactNode };
}

export function VariantRow({
  account,
  excerpt,
  confidence,
  time,
  tz = "UTC",
  showZone = true,
  state,
  statusModifiers,
  off,
  className,
  ...props
}: VariantRowProps) {
  const resolvedState: PostVariantState | undefined = off ? (state ?? "skipped") : state;
  return (
    <div
      data-slot="variant-row"
      data-off={off ? "true" : undefined}
      className={cn(
        "relative mr-2.5 ml-7 grid grid-cols-[180px_minmax(0,1fr)_auto] items-center gap-3.5 rounded-md py-2 pr-2.5 pl-[22px]",
        "before:absolute before:top-1/2 before:left-0 before:h-px before:w-3.5 before:bg-border-strong",
        "hover:bg-surface-hover",
        "max-rail:ml-[18px] max-rail:grid-cols-[minmax(0,1fr)] max-rail:gap-[7px] max-rail:before:top-[19px]",
        off && "text-fg-subtle [&_[data-slot=account-mark]]:opacity-55",
        className,
      )}
      {...props}
    >
      <AccountChip account={account} />
      <span
        className={cn(
          "truncate text-sm text-fg-muted",
          off && "text-fg-subtle line-through",
        )}
      >
        {off ? (
          <>
            <span className="sr-only">Off: </span>
            {off.reason}
          </>
        ) : (
          excerpt
        )}
      </span>
      <span className="flex items-center gap-3 justify-self-end max-rail:justify-self-start max-narrow:flex-wrap max-narrow:gap-2">
        {confidence !== undefined && !off ? (
          <Tag tone="outline" title="Editor confidence">
            <span className="sr-only">Confidence </span>
            {confidence.toFixed(2)}
          </Tag>
        ) : null}
        {time && !off ? <Time value={time} tz={tz} showZone={showZone} className="text-fg-subtle" /> : null}
        {resolvedState ? <StatusPill kind="post_variant" state={resolvedState} modifiers={statusModifiers} /> : null}
      </span>
    </div>
  );
}

/** mera.css .variants: the trunk line the rows branch from. */
export function VariantList({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="variant-list"
      className={cn(
        "relative pb-2",
        "before:absolute before:top-0 before:bottom-[26px] before:left-7 before:w-px before:bg-border-strong",
        "max-rail:before:left-[18px]",
        className,
      )}
      {...props}
    />
  );
}
