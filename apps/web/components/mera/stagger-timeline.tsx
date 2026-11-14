import type { ComponentProps, ReactNode } from "react";
import { Time } from "@/components/ui/time";
import { cn } from "@/lib/utils";

/*
 * composer.html "Publishing order": the stagger as stored offsets. One row per
 * account in publish order: time (mono) · account · offset from the first
 * ("+0", "+4m"). Offsets are computed from the times, so what is shown is
 * what was approved.
 *
 * Before a set is scheduled there is no time yet, only the stored offset:
 * pass `offsetSeconds` instead of `at` and the first column shows the
 * publish position.
 */
export type StaggerItem = {
  id: string;
  label: ReactNode;
} & ({ at: Date | string; offsetSeconds?: never } | { at?: never; offsetSeconds: number });

export interface StaggerTimelineProps extends Omit<ComponentProps<"ol">, "children"> {
  items: readonly StaggerItem[];
  tz: string;
}

export function formatOffset(ms: number): string {
  const min = Math.round(ms / 60000);
  if (min === 0) return "+0";
  if (min < 60) return `+${min}m`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m ? `+${h}h ${m}m` : `+${h}h`;
}

export function StaggerTimeline({ items, tz, className, ...props }: StaggerTimelineProps) {
  const firstAt = items[0]?.at;
  const first = firstAt ? new Date(firstAt).getTime() : 0;
  return (
    <ol
      data-slot="stagger-timeline"
      aria-label="Publishing order"
      className={cn("grid rounded-md border border-border", className)}
      {...props}
    >
      {items.map((item, i) => (
        <li
          key={item.id}
          className="grid grid-cols-[auto_1fr_auto] items-baseline gap-2.5 border-b border-border px-3.5 py-[9px] text-sm last:border-b-0 hover:bg-surface-hover"
        >
          {item.at !== undefined ? (
            <Time value={item.at} tz={tz} className="text-xs text-fg-subtle" />
          ) : (
            <span aria-hidden="true" className="tabular min-w-4 text-right font-mono text-2xs text-fg-subtle">
              {i + 1}
            </span>
          )}
          <span className="truncate">{item.label}</span>
          <span className="tabular text-xs text-fg-subtle">
            {formatOffset(item.at !== undefined ? new Date(item.at).getTime() - first : item.offsetSeconds * 1000)}
          </span>
        </li>
      ))}
    </ol>
  );
}
