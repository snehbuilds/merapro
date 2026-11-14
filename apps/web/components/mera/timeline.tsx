import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/utils";

/*
 * mera.css .tl (agents.html run steps, audit): numbered ledger rows.
 * Columns: step number (mono, right-aligned) · step kind · detail · meta (cost).
 * A row with tone warn / ok tints the row and inks the detail, for fallbacks
 * and guardrail verdicts; the detail text still states the verdict.
 */
export interface TimelineItem {
  id: string;
  kind: ReactNode;
  detail: ReactNode;
  meta?: ReactNode;
  /** Mono meta (costs); default true. */
  metaMono?: boolean;
  tone?: "warn" | "ok" | "danger";
}

export interface TimelineProps extends Omit<ComponentProps<"ol">, "children"> {
  items: readonly TimelineItem[];
  /** Number the first row from this (default 1). */
  start?: number;
}

const ROW_TONE = {
  warn: "bg-warn-bg [&_[data-col=detail]]:text-warn",
  ok: "bg-ok-bg [&_[data-col=detail]]:text-ok",
  danger: "bg-danger-bg [&_[data-col=detail]]:text-danger",
} as const;

export function Timeline({ items, start = 1, className, ...props }: TimelineProps) {
  return (
    <ol data-slot="timeline" start={start} className={cn("grid", className)} {...props}>
      {items.map((item, i) => (
        <li
          key={item.id}
          data-tone={item.tone}
          className={cn(
            "grid grid-cols-[28px_132px_minmax(0,1fr)_auto] items-baseline gap-2.5 border-b border-border px-3.5 py-[9px] text-sm last:border-b-0",
            "max-narrow:grid-cols-[28px_minmax(0,1fr)_auto] max-narrow:[&>[data-col=kind]]:col-start-2",
            item.tone ? ROW_TONE[item.tone] : "hover:bg-surface-hover",
          )}
        >
          <span aria-hidden="true" className="tabular text-right font-mono text-2xs text-fg-subtle">
            {start + i}
          </span>
          <span data-col="kind">{item.kind}</span>
          <span data-col="detail" className="truncate text-fg-muted max-narrow:col-span-2 max-narrow:col-start-2">
            {item.detail}
          </span>
          {item.meta !== undefined ? (
            <span className={cn("text-fg-subtle", item.metaMono !== false && "font-mono")}>{item.meta}</span>
          ) : (
            <span />
          )}
        </li>
      ))}
    </ol>
  );
}
