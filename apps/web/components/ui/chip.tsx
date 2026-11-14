import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";
import { Icon } from "@/components/icons";
import { cn } from "@/lib/utils";

/*
 * mera.css .chip: the filter-bar pill.
 *   variant="toggle" (default)  pass `pressed`; rendered as aria-pressed, and the
 *                               pressed chip inverts (ink on paper → paper on ink).
 *   variant="filter"            a trigger that opens a filter menu; leads with
 *                               the filter glyph and carries no pressed state
 *                               (pass aria-haspopup / aria-expanded from the menu).
 * `count` renders a mono suffix ("Pending 7"); inside a pressed chip it inherits
 * the inverse ink, because --fg-subtle on --bg-inverse is under 3:1 (mera.css).
 */
const chipVariants = cva(
  [
    "group/chip inline-flex h-7 items-center gap-1.5 whitespace-nowrap rounded-[14px] border border-border bg-surface-raised px-2.5",
    "text-caption text-fg-muted transition-[background-color,border-color,color] duration-[90ms] ease-out",
    "hover:border-border-strong hover:text-fg",
    "aria-pressed:border-surface-inverse aria-pressed:bg-surface-inverse aria-pressed:font-medium aria-pressed:text-fg-inverse",
    "aria-pressed:hover:text-fg-inverse",
    "aria-pressed:[&_[data-slot=group-dot]]:shadow-[0_0_0_1.5px_var(--fg-inverse)]",
    "disabled:pointer-events-none disabled:opacity-55",
  ],
  {
    variants: {
      variant: {
        toggle: "",
        filter: "",
      },
    },
    defaultVariants: { variant: "toggle" },
  },
);

export interface ChipProps extends Omit<ComponentProps<"button">, "aria-pressed">, VariantProps<typeof chipVariants> {
  /** Toggle chips only. Rendered as aria-pressed. */
  pressed?: boolean;
  /** Optional count suffix, e.g. the number of items the filter matches. */
  count?: number;
}

export function Chip({ variant, pressed, count, className, children, type, ...props }: ChipProps) {
  const isFilter = variant === "filter";
  return (
    <button
      type={type ?? "button"}
      data-slot="chip"
      data-variant={variant ?? "toggle"}
      aria-pressed={isFilter ? undefined : (pressed ?? false)}
      className={cn(chipVariants({ variant }), className)}
      {...props}
    >
      {isFilter ? <Icon name="filter" size="sm" /> : null}
      {children}
      {count !== undefined ? (
        <span className="tabular font-mono text-2xs text-fg-subtle group-aria-pressed/chip:text-current">{count}</span>
      ) : null}
    </button>
  );
}

export { chipVariants };
