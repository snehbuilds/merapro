import type { ComponentProps, ReactNode } from "react";
import { Icon, type IconName } from "@/components/icons";
import { cn } from "@/lib/utils";

/*
 * mera.css .empty, designed for use (v2 defines it but never shows one).
 *
 * The ledger idiom: an empty state is a blank page in the ledger, not an
 * illustration. A subtle glyph, a one-line condensed heading that names what
 * is absent ("Nothing waiting"), one sentence that says why or what fills it,
 * and at most one action. It sits inside the panel it replaces, on a dashed
 * rule like the panel foot, so it reads as "this column is empty" rather than
 * "something broke".
 *
 * `tone="quiet"` drops the dashed frame for empty table bodies and lists.
 */
export interface EmptyStateProps extends Omit<ComponentProps<"div">, "title"> {
  icon?: IconName;
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  tone?: "framed" | "quiet";
}

export function EmptyState({
  icon = "inbox",
  title,
  description,
  action,
  tone = "framed",
  className,
  ...props
}: EmptyStateProps) {
  return (
    <div
      data-slot="empty-state"
      className={cn(
        "grid place-items-center gap-2 px-6 py-11 text-center",
        tone === "framed" && "m-3.5 rounded-md border border-dashed border-border-strong bg-surface-muted",
        className,
      )}
      {...props}
    >
      <Icon name={icon} size="xl" className="text-fg-subtle" />
      <p className="font-label text-caption font-semibold tracking-[0.09em] text-fg uppercase">{title}</p>
      {description ? <p className="max-w-[46ch] text-sm text-fg-muted">{description}</p> : null}
      {action ? <div className="mt-1.5">{action}</div> : null}
    </div>
  );
}
