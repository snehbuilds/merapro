import type { ComponentProps, ReactNode } from "react";
import { groupColor } from "@/lib/group-color";
import { cn } from "@/lib/utils";

/*
 * mera.css .gdot: group identity as a small square mark (squares are identity;
 * circles are status). Colour comes from the generated palette in
 * lib/group-color.ts, keyed by the group's position or id. The dot is always
 * decorative: the group name next to it carries the meaning.
 */
export interface GroupDotProps extends Omit<ComponentProps<"span">, "children"> {
  /** Palette key: the group's index or its id. */
  group: number | string;
}

export function GroupDot({ group, className, style, ...props }: GroupDotProps) {
  return (
    <span
      aria-hidden="true"
      data-slot="group-dot"
      className={cn("inline-block size-2 shrink-0 rounded-[1px]", className)}
      style={{ background: groupColor(group), ...style }}
      {...props}
    />
  );
}

/** Dot + name, as in post set meta lines. */
export function GroupLabel({ group, name, className }: { group: number | string; name: ReactNode; className?: string }) {
  return (
    <span data-slot="group-label" className={cn("inline-flex items-center gap-[5px]", className)}>
      <GroupDot group={group} />
      {name}
    </span>
  );
}
