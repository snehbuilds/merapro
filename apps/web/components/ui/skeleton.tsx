import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

/*
 * mera.css .skel: a 10px shimmer bar on sunken paper. Reduced motion stops the
 * shimmer (globals.css), leaving a still placeholder.
 *
 * Skeletons draw the shape of the content that is coming, at its real
 * position, so the layout does not jump: SkeletonRows mimics a ledger of
 * VariantRows / table rows. The container is aria-busy with a hidden label;
 * the bars themselves are decorative.
 */
export function Skeleton({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      aria-hidden="true"
      data-slot="skeleton"
      className={cn(
        "h-2.5 animate-shimmer rounded-sm bg-[length:400%_100%]",
        "bg-[linear-gradient(90deg,var(--bg-sunken)_25%,var(--bg-hover)_37%,var(--bg-sunken)_63%)]",
        className,
      )}
      {...props}
    />
  );
}

export interface SkeletonRowsProps extends ComponentProps<"div"> {
  rows?: number;
  /** Announced to assistive tech while loading. */
  label?: string;
}

const WIDTHS = ["w-[72%]", "w-[58%]", "w-[66%]", "w-[49%]", "w-[61%]"];

export function SkeletonRows({ rows = 4, label = "Loading", className, ...props }: SkeletonRowsProps) {
  return (
    <div role="status" aria-busy="true" data-slot="skeleton-rows" className={cn("grid", className)} {...props}>
      <span className="sr-only">{label}</span>
      {Array.from({ length: rows }, (_, i) => (
        <div
          key={i}
          className="grid grid-cols-[22px_120px_minmax(0,1fr)_56px] items-center gap-3.5 border-b border-border px-3.5 py-3 last:border-b-0"
        >
          <Skeleton className="size-[22px] rounded-full" />
          <Skeleton className="w-[86%]" />
          <Skeleton className={WIDTHS[i % WIDTHS.length]} />
          <Skeleton className="h-[18px] rounded-sm" />
        </div>
      ))}
    </div>
  );
}
